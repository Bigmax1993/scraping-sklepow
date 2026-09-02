"""
Warstwa danych kontaktowych po Google Maps (zbiorczo).

1. Zbiera rekordy z brakami kontaktów (telefon, e-mail, WWW, osoba kontaktowa).
2. Faza Serper — zbiorczo wyszukuje strony dla wszystkich rekordów bez WWW.
3. Faza scrape — requests + bs4 dla wszystkich znalezionych URL-i.
4. Faza Claude — jedno (lub chunked) wywołanie weryfikuje wszystkie pozyskane dane.
5. Wynik trafia do rekordów JSON; brak danych nie blokuje rekordu.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
CONTACT_CACHE_FILE = SCRIPT_DIR / "neueroeffnung_contact_cache.json"
CONTACT_BATCH_REPORT_FILE = SCRIPT_DIR / "neueroeffnung_contact_batch.json"
SERPER_API_URL = "https://google.serper.dev/search"
CONTACT_REQUEST_DELAY_SEC = 1.0
CONTACT_SEARCH_TIMEOUT_SEC = 25
MAX_HTML_FOR_CLAUDE = 12_000
CLAUDE_BATCH_CHUNK_SIZE = 20

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


@dataclass
class ContactEnrichmentJob:
    job_id: str
    category: str
    record_index: int
    company_name: str
    address: str
    telefon: str = ""
    email: str = ""
    website: str = ""
    osoba_kontaktowa: str = ""
    missing_fields: list[str] = field(default_factory=list)
    serper_query: str = ""
    serper_url: str = ""
    target_url: str = ""
    scraped: ContactData = field(default_factory=ContactData)
    html_snippet: str = ""
    verified: ContactData = field(default_factory=ContactData)


def is_enrichment_enabled() -> bool:
    return os.environ.get("ENABLE_CONTACT_ENRICHMENT", "true").lower() in ("1", "true", "yes")


def is_claude_verification_enabled() -> bool:
    flag = os.environ.get("ENABLE_CLAUDE_CONTACT_VERIFY", "true").lower()
    if flag not in ("1", "true", "yes"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def get_serper_api_key() -> str:
    return (os.environ.get("SERPER_API_KEY") or "").strip()


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


def save_contact_batch_report(report: dict, logger: logging.Logger) -> None:
    try:
        with open(CONTACT_BATCH_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("Zapisano raport batch kontaktów: %s", CONTACT_BATCH_REPORT_FILE.resolve())
    except Exception as exc:
        logger.error("Błąd zapisu raportu batch kontaktów: %s", exc)


def contact_cache_key(company_name: str, address: str) -> str:
    return f"contact::{company_name.strip().lower()}::{address.strip().lower()}"


def missing_contact_fields(
    telefon: str,
    email: str,
    website: str,
    osoba_kontaktowa: str,
) -> list[str]:
    missing: list[str] = []
    if not (telefon or "").strip():
        missing.append("telefon")
    if not (email or "").strip():
        missing.append("email")
    if not (website or "").strip():
        missing.append("website")
    if not (osoba_kontaktowa or "").strip():
        missing.append("osoba_kontaktowa")
    return missing


def record_needs_contact_enrichment(
    telefon: str,
    email: str,
    website: str,
    osoba_kontaktowa: str,
) -> bool:
    """True gdy brakuje dowolnego pola kontaktowego."""
    return bool(missing_contact_fields(telefon, email, website, osoba_kontaktowa))


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


def serper_search_url(
    query: str,
    api_key: str,
    logger: logging.Logger,
) -> str:
    try:
        resp = requests.post(
            SERPER_API_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "gl": "de", "hl": "de", "num": 8},
            timeout=CONTACT_SEARCH_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        organic = resp.json().get("organic") or []
        for item in organic:
            href = (item.get("link") or "").strip()
            if not href.startswith("http"):
                continue
            host = urlparse(href).netloc.lower()
            if any(skip in host for skip in SKIP_SEARCH_DOMAINS):
                continue
            return href
    except Exception as exc:
        logger.warning("  Serper fail '%s': %s", query, exc)
    return ""


def batch_serper_search(
    jobs: list[ContactEnrichmentJob],
    api_key: str,
    logger: logging.Logger,
) -> None:
    """Zbiorcza faza Serper — po jednym zapytaniu na rekord bez WWW."""
    pending = [job for job in jobs if not (job.website or "").strip()]
    logger.info("Serper batch: %s zapytań", len(pending))
    for job in jobs:
        if (job.website or "").strip():
            job.target_url = job.website.strip()
    for job in pending:
        job.serper_query = f"{job.company_name} {job.address} Impressum Kontakt"
        job.serper_url = serper_search_url(job.serper_query, api_key, logger)
        job.target_url = job.serper_url
        if job.serper_url:
            logger.info("  Serper [%s]: %s", job.company_name, job.serper_url)
        time.sleep(CONTACT_REQUEST_DELAY_SEC)


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
        logger.info("  -> Impressum/Kontakt: %s", impressum_url)
        imp_html = fetch_page_html(session, impressum_url, headers, logger)
        if imp_html:
            merged = extract_contacts_from_html(imp_html, impressum_url).merge_into(contact)
            return merged, imp_html
    return contact, html


def batch_scrape_jobs(
    jobs: list[ContactEnrichmentJob],
    session: requests.Session,
    headers: dict,
    logger: logging.Logger,
) -> None:
    """Zbiorcza faza scrape — requests + bs4 dla wszystkich URL-i z Serper."""
    with_url = [job for job in jobs if job.target_url]
    logger.info("Scrape batch: %s rekordów", len(with_url))
    for job in with_url:
        scraped, html = scrape_contacts_from_site(session, job.target_url, headers, logger)
        job.scraped = scraped.merge_into(
            ContactData(
                telefon=job.telefon,
                email=job.email,
                website=job.website or job.target_url,
                osoba_kontaktowa=job.osoba_kontaktowa,
                source_url=job.target_url,
            )
        )
        job.html_snippet = html[:MAX_HTML_FOR_CLAUDE] if html else ""


def _parse_claude_json(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def _contact_from_payload(payload: dict, source_url: str = "") -> ContactData:
    return ContactData(
        telefon=normalize_phone(payload.get("telefon", "")),
        email=normalize_email(payload.get("email", "")),
        website=(payload.get("website") or "").strip(),
        osoba_kontaktowa=(payload.get("osoba_kontaktowa") or "").strip(),
        source_url=source_url or (payload.get("source_url") or ""),
        verified=bool(payload.get("verified")),
    )


def batch_verify_contacts_with_claude(
    jobs: list[ContactEnrichmentJob],
    logger: logging.Logger,
) -> None:
    """Zbiorcza weryfikacja Claude — chunki po CLAUDE_BATCH_CHUNK_SIZE rekordów."""
    if not jobs:
        return
    if not is_claude_verification_enabled():
        for job in jobs:
            job.verified = ContactData(verified=False)
        return

    try:
        import anthropic
    except ImportError:
        logger.warning("Brak pakietu anthropic — pomijam weryfikację Claude")
        for job in jobs:
            job.verified = ContactData(verified=False)
        return

    client = anthropic.Anthropic()
    model = os.environ.get("CLAUDE_CONTACT_MODEL", "claude-sonnet-4-6")
    chunk_size = int(os.environ.get("CONTACT_CLAUDE_BATCH_SIZE", str(CLAUDE_BATCH_CHUNK_SIZE)))

    for chunk_start in range(0, len(jobs), chunk_size):
        chunk = jobs[chunk_start : chunk_start + chunk_size]
        records_payload = []
        for job in chunk:
            records_payload.append(
                {
                    "id": job.job_id,
                    "company_name": job.company_name,
                    "address": job.address,
                    "missing_fields": job.missing_fields,
                    "existing": {
                        "telefon": job.telefon,
                        "email": job.email,
                        "website": job.website,
                        "osoba_kontaktowa": job.osoba_kontaktowa,
                    },
                    "scraped": asdict(job.scraped),
                    "source_url": job.target_url,
                    "html_snippet": job.html_snippet[:4000] if job.html_snippet else "",
                }
            )

        prompt = f"""Zweryfikuj zbiorczo dane kontaktowe dla obiektów handlowych w Niemczech/Austrii.

Dla każdego rekordu oceń pola scraped + html_snippet. Uzupełnij brakujące pola tylko gdy masz wysoką pewność.
Jeśli dane nie pasują do firmy/adresu — verified=false i puste pola kontaktowe.

Zwróć WYŁĄCZNIE JSON (bez markdown):
{{"records":[{{"id":"","telefon":"","email":"","website":"","osoba_kontaktowa":"","verified":true/false}}]}}

Rekordy:
{json.dumps(records_payload, ensure_ascii=False)}
"""
        try:
            message = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            payload = _parse_claude_json(message.content[0].text)
            by_id = {item["id"]: item for item in payload.get("records", []) if item.get("id")}
            for job in chunk:
                item = by_id.get(job.job_id, {})
                job.verified = _contact_from_payload(item, source_url=job.target_url)
                if not job.verified.source_url:
                    job.verified.source_url = job.target_url
                logger.info(
                    "  Claude [%s]: verified=%s | tel=%s | email=%s | osoba=%s",
                    job.company_name,
                    job.verified.verified,
                    "tak" if job.verified.telefon else "nie",
                    "tak" if job.verified.email else "nie",
                    "tak" if job.verified.osoba_kontaktowa else "nie",
                )
        except Exception as exc:
            logger.warning("Claude batch błąd (chunk %s): %s", chunk_start, exc)
            for job in chunk:
                job.verified = ContactData(verified=False)


def job_to_batch_entry(job: ContactEnrichmentJob) -> dict:
    return {
        "job_id": job.job_id,
        "category": job.category,
        "company_name": job.company_name,
        "address": job.address,
        "missing_fields": job.missing_fields,
        "serper_query": job.serper_query,
        "serper_url": job.serper_url,
        "target_url": job.target_url,
        "scraped": asdict(job.scraped),
        "verified": asdict(job.verified),
    }


def contact_from_cache_entry(cached: dict) -> ContactData:
    return ContactData(
        telefon=cached.get("telefon", ""),
        email=cached.get("email", ""),
        website=cached.get("website", ""),
        osoba_kontaktowa=cached.get("osoba_kontaktowa", ""),
        source_url=cached.get("source_url", ""),
        verified=bool(cached.get("verified")),
    )


def apply_contact_to_record(record: Any, contact: ContactData) -> None:
    """Zapisuje dane kontaktowe do rekordu tylko gdy verified=true."""
    record.kontakt_zweryfikowany = bool(contact.verified)
    if not contact.verified:
        return
    if contact.telefon:
        record.telefon = contact.telefon
    if contact.email:
        record.email = contact.email
    if contact.website:
        record.website = contact.website
    if contact.osoba_kontaktowa:
        record.osoba_kontaktowa = contact.osoba_kontaktowa
    if contact.source_url:
        record.kontakt_zrodlo = contact.source_url


def build_contact_enrichment_jobs(
    sheets: dict[str, list[Any]],
    data_sheet_names: tuple[str, ...],
    cache: dict,
    logger: logging.Logger,
) -> tuple[list[ContactEnrichmentJob], list[tuple[str, int, ContactData]]]:
    """Zwraca joby do przetworzenia oraz trafienia z cache (category, index, contact)."""
    jobs: list[ContactEnrichmentJob] = []
    cached_hits: list[tuple[str, int, ContactData]] = []
    job_counter = 0

    for category_name in data_sheet_names:
        for idx, record in enumerate(sheets.get(category_name, [])):
            missing = missing_contact_fields(
                record.telefon,
                record.email,
                record.website,
                record.osoba_kontaktowa,
            )
            if not missing:
                continue

            key = contact_cache_key(record.nazwa_firmy, record.adres)
            cached = cache.get(key)
            if cached and cached.get("verified"):
                logger.info("  Cache kontaktów (verified): %s", record.nazwa_firmy)
                cached_hits.append((category_name, idx, contact_from_cache_entry(cached)))
                continue

            job_counter += 1
            jobs.append(
                ContactEnrichmentJob(
                    job_id=str(job_counter),
                    category=category_name,
                    record_index=idx,
                    company_name=record.nazwa_firmy,
                    address=record.adres,
                    telefon=record.telefon,
                    email=record.email,
                    website=record.website,
                    osoba_kontaktowa=record.osoba_kontaktowa,
                    missing_fields=missing,
                )
            )

    return jobs, cached_hits


def finalize_job_contact(job: ContactEnrichmentJob) -> ContactData:
    """Do JSON/Excel trafiają wyłącznie dane zweryfikowane przez Claude (verified=true)."""
    if job.verified.verified and job.verified.has_any():
        return job.verified
    return ContactData(source_url=job.target_url, verified=False)


def run_batch_contact_enrichment(
    session: requests.Session,
    sheets: dict[str, list[Any]],
    data_sheet_names: tuple[str, ...],
    headers: dict,
    logger: logging.Logger,
) -> tuple[dict[str, list[Any]], dict]:
    """Batch: Serper → scrape (Claude w claude_record_normalizer)."""
    contact_cache = load_contact_cache(logger)
    jobs, cached_hits = build_contact_enrichment_jobs(
        sheets, data_sheet_names, contact_cache, logger
    )

    for category_name, idx, contact in cached_hits:
        apply_contact_to_record(sheets[category_name][idx], contact)

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "serper_enabled": bool(get_serper_api_key()),
        "jobs_total": len(jobs),
        "cached_hits": len(cached_hits),
        "jobs": [],
    }

    if not jobs:
        save_contact_cache(contact_cache, logger)
        save_contact_batch_report(report, logger)
        return sheets, report

    api_key = get_serper_api_key()
    if api_key:
        batch_serper_search(jobs, api_key, logger)
    else:
        logger.warning("Brak SERPER_API_KEY — używam tylko istniejących URL-i WWW")
        for job in jobs:
            if (job.website or "").strip():
                job.target_url = job.website.strip()

    batch_scrape_jobs(jobs, session, headers, logger)

    for job in jobs:
        report["jobs"].append(job_to_batch_entry(job))

    save_contact_cache(contact_cache, logger)
    save_contact_batch_report(report, logger)
    logger.info("Kontakty scrape batch: %s jobów", len(jobs))
    return sheets, report


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
    """Kompatybilność wsteczna — deleguje do batch pipeline dla jednego rekordu."""
    if not record_needs_contact_enrichment(telefon, email, website, osoba_kontaktowa):
        return ContactData(
            telefon=telefon,
            email=email,
            website=website,
            osoba_kontaktowa=osoba_kontaktowa,
            verified=True,
        )

    key = contact_cache_key(company_name, address)
    cached = cache.get(key)
    if cached and cached.get("verified"):
        return contact_from_cache_entry(cached)

    job = ContactEnrichmentJob(
        job_id="1",
        category="Markets",
        record_index=0,
        company_name=company_name,
        address=address,
        telefon=telefon,
        email=email,
        website=website,
        osoba_kontaktowa=osoba_kontaktowa,
        missing_fields=missing_contact_fields(telefon, email, website, osoba_kontaktowa),
    )
    api_key = get_serper_api_key()
    if api_key:
        batch_serper_search([job], api_key, logger)
    elif (website or "").strip():
        job.target_url = website.strip()

    batch_scrape_jobs([job], session, headers, logger)
    return job.scraped if job.scraped.has_any() else ContactData(source_url=job.target_url)
