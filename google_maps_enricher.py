"""
Drugą warstwa uzupełniania adresów — Google Maps przez Playwright (headless).

Używana gdy adres w JSON/rekordzie jest niepełny (np. tylko „80331 München”).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

SCRIPT_DIR = Path(__file__).resolve().parent
MAPS_CACHE_FILE = SCRIPT_DIR / "neueroeffnung_maps_cache.json"
MAPS_REQUEST_DELAY_SEC = 1.5
MAPS_PAGE_TIMEOUT_MS = 25_000
PLZ_PATTERN = re.compile(r"\b(\d{4,5})\b")


def is_enrichment_enabled() -> bool:
    return os.environ.get("ENABLE_GOOGLE_MAPS_ENRICHMENT", "true").lower() in ("1", "true", "yes")


def is_headless() -> bool:
    return os.environ.get("GOOGLE_MAPS_HEADLESS", "true").lower() in ("1", "true", "yes")


def load_maps_cache(logger: logging.Logger) -> dict:
    if not MAPS_CACHE_FILE.exists():
        return {}
    try:
        with open(MAPS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Nie wczytano cache Google Maps: %s", exc)
        return {}


def save_maps_cache(cache: dict, logger: logging.Logger) -> None:
    try:
        with open(MAPS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("Błąd zapisu cache Google Maps: %s", exc)


def build_search_query(company_name: str, partial_address: str) -> str:
    parts = [company_name.strip(), partial_address.strip()]
    return " ".join(part for part in parts if part)


def extract_plz(text: str) -> str:
    match = PLZ_PATTERN.search(text or "")
    return match.group(1) if match else ""


def score_maps_match(company_name: str, partial_address: str, result_text: str) -> int:
    score = 0
    name_l = company_name.lower().strip()
    partial_l = partial_address.lower().strip()
    text_l = result_text.lower().strip()

    if not text_l:
        return 0
    if name_l == text_l:
        score += 20
    elif name_l in text_l or text_l in name_l:
        score += 10

    plz = extract_plz(partial_address)
    if plz and plz in text_l:
        score += 15

    city_hint = re.sub(r"^\d{4,5}\s*", "", partial_address).strip().lower()
    if city_hint and city_hint in text_l:
        score += 8
    if partial_l and partial_l in text_l:
        score += 12
    return score


def normalize_maps_address(raw: str) -> str:
    text = " ".join((raw or "").split()).strip()
    text = re.sub(r"^(Address|Adresse):\s*", "", text, flags=re.I)
    return text.strip(" ·•")


class GoogleMapsEnricher:
    """Playwright w tle — jedna sesja przeglądarki na cały batch rekordów."""

    def __init__(
        self,
        logger: logging.Logger,
        cache: dict | None = None,
        *,
        headless: bool | None = None,
    ):
        self.logger = logger
        self.cache = cache if cache is not None else {}
        self.headless = is_headless() if headless is None else headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> GoogleMapsEnricher:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale="de-DE",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(MAPS_PAGE_TIMEOUT_MS)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for resource in (self._context, self._browser):
            if resource is not None:
                resource.close()
        if self._playwright is not None:
            self._playwright.stop()

    def cache_key(self, company_name: str, partial_address: str) -> str:
        return f"maps::{company_name.strip()}::{partial_address.strip()}"

    def resolve_address(self, company_name: str, partial_address: str) -> str:
        query = build_search_query(company_name, partial_address)
        if not query:
            return ""

        key = self.cache_key(company_name, partial_address)
        cached = self.cache.get(key, {})
        if cached.get("adres"):
            self.logger.info("  Maps cache hit: %s", query)
            return cached["adres"]
        if cached.get("not_found"):
            self.logger.info("  Maps cache (brak wyniku): %s", query)
            return ""

        if self._page is None:
            raise RuntimeError("GoogleMapsEnricher must be used as context manager")

        self.logger.info("  Google Maps: %s", query)
        address = self._lookup_address(query, company_name, partial_address)
        self.cache[key] = {"adres": address, "query": query, "not_found": not bool(address)}
        time.sleep(MAPS_REQUEST_DELAY_SEC)
        return address

    def _lookup_address(self, query: str, company_name: str, partial_address: str) -> str:
        page = self._page
        search_url = f"https://www.google.com/maps/search/{quote_plus(query)}"
        try:
            page.goto(search_url, wait_until="domcontentloaded")
            self._dismiss_consent(page)
            page.wait_for_timeout(1200)

            address = self._read_address_from_place_panel(page)
            if address:
                self.logger.info("  -> Maps: %s", address)
                return address

            self._click_best_search_result(page, company_name, partial_address)
            page.wait_for_timeout(1200)
            address = self._read_address_from_place_panel(page)
            if address:
                self.logger.info("  -> Maps (lista): %s", address)
                return address

            self.logger.warning("  -> Maps: brak adresu dla: %s", query)
            return ""
        except Exception as exc:
            self.logger.warning("  -> Maps błąd dla '%s': %s", query, exc)
            return ""

    def _dismiss_consent(self, page) -> None:
        for selector in (
            'button:has-text("Alle akzeptieren")',
            'button:has-text("Accept all")',
            'button[aria-label="Accept all"]',
            'button:has-text("Zgadzam się")',
        ):
            try:
                button = page.locator(selector).first
                if button.is_visible(timeout=1500):
                    button.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _read_address_from_place_panel(self, page) -> str:
        selectors = (
            'button[data-item-id="address"] div.fontBodyMedium',
            'button[data-item-id="address"]',
            '[data-item-id="address"] .Io6YTe',
            'button[aria-label^="Address:"]',
            'button[aria-label^="Adresse:"]',
        )
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                text = normalize_maps_address(locator.inner_text(timeout=4000))
                if text:
                    return text
            except Exception:
                continue

        for selector in ('button[aria-label^="Address:"]', 'button[aria-label^="Adresse:"]'):
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                aria = locator.get_attribute("aria-label") or ""
                text = normalize_maps_address(re.sub(r"^(Address|Adresse):\s*", "", aria, flags=re.I))
                if text:
                    return text
            except Exception:
                continue
        return ""

    def _click_best_search_result(self, page, company_name: str, partial_address: str) -> None:
        results = page.locator('a[href*="/maps/place"]')
        count = results.count()
        if count == 0:
            return

        best_idx = 0
        best_score = -1
        limit = min(count, 8)
        for idx in range(limit):
            label = results.nth(idx).get_attribute("aria-label") or ""
            score = score_maps_match(company_name, partial_address, label)
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_score < 10:
            self.logger.warning(
                "  -> Maps: słabe dopasowanie wyniku (score=%s) dla %s",
                best_score,
                company_name,
            )
            return

        results.nth(best_idx).click(timeout=5000)
