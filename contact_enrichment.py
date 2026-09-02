"""
Warstwa danych kontaktowych po Google Maps.

1. Sprawdza rekordy JSON pod kątem telefonu / e-mail / WWW / osoby kontaktowej.
2. Gdy brakuje — wyszukuje w internecie (nazwa + adres), pobiera stronę (requests + bs4).
3. Claude weryfikuje dopasowanie; przy odrzuceniu analizuje pełny HTML strony.
4. Brak danych po retry — rekord przechodzi dalej bez blokady.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
CONTACT_CACHE_FILE = SCRIPT_DIR / "neueroeffnung_contact_cache.json"
CONTACT_REQUEST_DELAY_SEC = 1.0
CONTACT_SEARCH_TIMEOUT_SEC = 25
MAX_HTML_FOR_CLAUDE = 48_000

EMAIL_PATTERN = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
)
PHONE_PATTERN = re.compile(
    r"(?:\+49[\d\s\(\)\-/]{6,}\d|0\d{2,4}[\s\-/]?\d[\d\s\-/]{4,}\d)"
)
JUNK_EMAIL_DOMAINS = (
    "example.com",
    "sentry.io",
    "wixpress.com",
    "domain.com",
    "email.com",
    "yourdomain.com",
)
SKIP_SEARCH_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "neueroeffnung.info",
    "google.com",
    "google.de",
    "wikipedia.org",
)
IMPRESSUM_HINTS = ("impressum", "kontakt", "contact", "legal", "about", "ueber-uns", "über-uns")


@dataclass
class ContactData:
    telefon: str = ""
    email: str = ""
    website: str = ""
    osoba_kontaktowa: str = ""
    source_url: str = ""
    verified: bool = False

    def has_any(self) -> bool:
        return bool(self.telefon or self.email or self.website or self.osoba_kontaktowa)

    def merge_into(self, other: ContactData) -> ContactData:
        return ContactData(
            telefon=self.telefon or other.telefon,
            email=self.email or other.email,
            website=self.website or other.website,
            osoba_kontaktowa=self.osoba_kontaktowa or other.osoba_kontaktowa,
            source_url=self.source_url or other.source_url,
            verified=self.verified or other.verified,
        )


def is_enrichment_enabled() -> bool:
    return os.environ.get("ENABLE_CONTACT_ENRICHMENT", "true").lower() in ("1", "true", "yes")


def is_claude_verification_enabled() -> bool:
    flag = os.environ.get("ENABLE_CLAUDE_CONTACT_VERIFY", "true").lower()
    if flag not in ("1", "true", "yes"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def load_contact_cache(logger: logging.Logger) -> dict:
    if not CONTACT_CACHE_FILE.exists():
        return {}
    try:
        with open(CONTACT_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Nie wczytano cache kontaktów: %s", exc)
        return {}


def save_contact_cache(cache: dict, logger: logging.Logger) -> None:
    try:
        with open(CONTACT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("Błąd zapisu cache kontaktów: %s", exc)


def contact_cache_key(company_name: str, address: str) -> str:
    return f"contact::{company_name.strip().lower()}::{address.strip().lower()}"


def record_needs_contact_enrichment(
    telefon: str,
    email: str,
    website: str,
    osoba_kontaktowa: str,
) -> bool:
    """True gdy brakuje kluczowych danych kontaktowych."""
    has_phone = bool((telefon or "").strip())
    has_email = bool((email or "").strip())
    has_website = bool((website or "").strip())
    return not (has_phone and has_email and has_website)


def normalize_phone(raw: str) -> str:
    text = " ".join((raw or "").split()).strip()
    text = re.sub(r"^(tel\.?|telefon:?|phone:?)\s*", "", text, flags=re.I)
    return text.strip(" ,.;")


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if "@" not in email:
        return ""
    domain = email.split("@")[-1]
    if any(domain.endswith(junk) or junk in domain for junk in JUNK_EMAIL_DOMAINS):
        return ""
    return email


def extract_contacts_from_html(html: str, page_url: str) -> ContactData:
    soup = BeautifulSoup(html, "html.parser")
    emails: set[str] = set()
    phones: set[str] = set()
    people: set[str] = set()
    website = ""

    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        text = a.get_text(" ", strip=True)
        if href.startswith("mailto:"):
            emails.add(normalize_email(href.replace("mailto:", "").split("?")[0]))
        elif href.startswith("tel:"):
            phones.add(normalize_phone(href.replace("tel:", "")))
        elif href.startswith("http") and not website:
            host = urlparse(href).netloc.lower()
            if not any(skip in host for skip in SKIP_SEARCH_DOMAINS):
                website = href

    text = soup.get_text("\n", strip=True)
    for match in EMAIL_PATTERN.findall(text):
        normalized = normalize_email(match)
        if normalized:
            emails.add(normalized)
    for match in PHONE_PATTERN.findall(text):
        phones.add(normalize_phone(match))

    for pattern in (
        r"(?:Inhaber|Geschäftsführer|Geschaeftsfuehrer|Ansprechpartner|Kontaktperson)[:\s]+([^\n\r]{3,80})",
        r"(?:Owner|Managing Director)[:\s]+([^\n\r]{3,80})",
    ):
        for match in re.finditer(pattern, text, re.I):
            people.add(match.group(1).strip(" .,;"))

    return ContactData(
        telefon=next(iter(phones), "") if phones else "",
        email=next(iter(emails), "") if emails else "",
        website=website or page_url,
        osoba_kontaktowa=next(iter(people), "") if people else "",
        source_url=page_url,
    )


def find_impressum_url(soup: BeautifulSoup, base_url: str) -> str:
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        label = f"{a.get_text(' ', strip=True)} {href}".lower()
        if any(hint in label for hint in IMPRESSUM_HINTS):
            return urljoin(base_url, href)
    return ""


def search_web_url(
    session: requests.Session,
    company_name: str,
    address: str,
    headers: dict,
    logger: logging.Logger,
) -> str:
    query = f"{company_name} {address} Impressum Kontakt"
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    logger.info("  Szukam strony: %s", query)
    try:
        resp = session.post(
            search_url,
            data={"q": query},
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=CONTACT_SEARCH_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.result__a"):
            href = link.get("href", "")
            if not href.startswith("http"):
                continue
            host = urlparse(href).netloc.lower()
            if any(skip in host for skip in SKIP_SEARCH_DOMAINS):
                continue
            logger.info("  -> Wynik wyszukiwania: %s", href)
            return href
    except Exception as exc:
        logger.warning("  -> Błąd wyszukiwania dla '%s': %s", company_name, exc)
    return ""


def fetch_page_html(
    session: requests.Session,
    url: str,
    headers: dict,
    logger: logging.Logger,
) -> str:
    try:
        resp = session.get(url, headers=headers, timeout=CONTACT_SEARCH_TIMEOUT_SEC)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        time.sleep(CONTACT_REQUEST_DELAY_SEC)
        return resp.text
    except Exception as exc:
        logger.warning("  -> Błąd pobierania %s: %s", url, exc)
        return ""


def scrape_contacts_from_site(
    session: requests.Session,
    url: str,
    headers: dict,
    logger: logging.Logger,
) -> tuple[ContactData, str]:
    html = fetch_page_html(session, url, headers, logger)
    if not html:
        return ContactData(), ""
    contact = extract_contacts_from_html(html, url)
    if contact.has_any():
        return contact, html
    soup = BeautifulSoup(html, "html.parser")
    impressum_url = find_impressum_url(soup, url)
    if impressum_url and impressum_url != url:
        logger.info("  -> Próbuję Impressum/Kontakt: %s", impressum_url)
        imp_html = fetch_page_html(session, impressum_url, headers, logger)
        if imp_html:
            merged = extract_contacts_from_html(imp_html, impressum_url).merge_into(contact)
            return merged, imp_html
    return contact, html


def verify_contacts_with_claude(
    company_name: str,
    address: str,
    contact: ContactData,
    html: str,
    logger: logging.Logger,
    *,
    html_only: bool = False,
) -> ContactData:
    if not is_claude_verification_enabled():
        contact.verified = contact.has_any()
        return contact

    try:
        import anthropic
    except ImportError:
        logger.warning("Brak pakietu anthropic — pomijam weryfikację Claude")
        contact.verified = contact.has_any()
        return contact

    client = anthropic.Anthropic()
    model = os.environ.get("CLAUDE_CONTACT_MODEL", "claude-sonnet-4-6")

    if html_only:
        prompt = f"""Przeanalizuj HTML strony firmy i wyciągnij dane kontaktowe dla obiektu handlowego.
Firma: {company_name}
Adres: {address}

Zwróć WYŁĄCZNIE JSON (bez markdown):
{{"telefon":"","email":"","website":"","osoba_kontaktowa":"","verified":true/false}}

Zasady:
- Tylko dane pasujące do tej firmy i lokalizacji w Niemczech/Austrii.
- Jeśli brak pewności — verified=false i puste pola.
- Nie zgaduj.

HTML:
{html[:MAX_HTML_FOR_CLAUDE]}
"""
    else:
        prompt = f"""Zweryfikuj, czy poniższe dane kontaktowe pasują do firmy i adresu.
Firma: {company_name}
Adres: {address}

Dane do weryfikacji:
{json.dumps(asdict(contact), ensure_ascii=False)}

Zwróć WYŁĄCZNIE JSON:
{{"telefon":"","email":"","website":"","osoba_kontaktowa":"","verified":true/false}}

Jeśli dane są błędne lub nie pasują — verified=false i poprawione puste/prawidłowe pola.
"""

    try:
        message = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        payload = json.loads(raw)
        result = ContactData(
            telefon=normalize_phone(payload.get("telefon", "")),
            email=normalize_email(payload.get("email", "")),
            website=(payload.get("website") or "").strip(),
            osoba_kontaktowa=(payload.get("osoba_kontaktowa") or "").strip(),
            source_url=contact.source_url,
            verified=bool(payload.get("verified")),
        )
        logger.info(
            "  Claude %s: verified=%s | tel=%s email=%s",
            "HTML" if html_only else "JSON",
            result.verified,
            "tak" if result.telefon else "nie",
            "tak" if result.email else "nie",
        )
        return result
    except Exception as exc:
        logger.warning("  Claude błąd weryfikacji: %s", exc)
        contact.verified = False
        return contact


def enrich_record_contacts(
    session: requests.Session,
    company_name: str,
    address: str,
    telefon: str,
    email: str,
    website: str,
    osoba_kontaktowa: str,
    headers: dict,
    cache: dict,
    logger: logging.Logger,
) -> ContactData:
    key = contact_cache_key(company_name, address)
    cached = cache.get(key)
    if cached:
        logger.info("  Cache kontaktów: %s", company_name)
        return ContactData(
            telefon=cached.get("telefon", ""),
            email=cached.get("email", ""),
            website=cached.get("website", ""),
            osoba_kontaktowa=cached.get("osoba_kontaktowa", ""),
            source_url=cached.get("source_url", ""),
            verified=bool(cached.get("verified")),
        )

    if not record_needs_contact_enrichment(telefon, email, website, osoba_kontaktowa):
        result = ContactData(
            telefon=telefon,
            email=email,
            website=website,
            osoba_kontaktowa=osoba_kontaktowa,
            verified=True,
        )
        cache[key] = {**asdict(result), "verified": True}
        return result

    target_url = website.strip() if website.strip() else search_web_url(
        session, company_name, address, headers, logger
    )
    if not target_url:
        cache[key] = asdict(ContactData())
        return ContactData()

    scraped, page_html = scrape_contacts_from_site(session, target_url, headers, logger)
    scraped = scraped.merge_into(
        ContactData(
            telefon=telefon,
            email=email,
            website=website or target_url,
            osoba_kontaktowa=osoba_kontaktowa,
            source_url=target_url,
        )
    )

    verified = verify_contacts_with_claude(company_name, address, scraped, page_html, logger)
    if verified.verified and verified.has_any():
        cache[key] = {**asdict(verified), "verified": True}
        return verified

    if page_html:
        html_verified = verify_contacts_with_claude(
            company_name,
            address,
            scraped,
            page_html,
            logger,
            html_only=True,
        )
        if html_verified.verified and html_verified.has_any():
            html_verified.source_url = target_url
            cache[key] = {**asdict(html_verified), "verified": True}
            return html_verified

    logger.info("  Brak zweryfikowanych kontaktów dla: %s — rekord przechodzi dalej", company_name)
    fallback = scraped if scraped.has_any() else ContactData(source_url=target_url)
    fallback.verified = False
    cache[key] = {**asdict(fallback), "verified": False}
    return fallback
