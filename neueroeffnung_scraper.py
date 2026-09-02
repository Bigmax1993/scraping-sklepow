"""
Scraper neueroeffnung.info → JSON → Walidacja → Excel

Kategorie (Excel): Markets, Restaurants, Drugstores, Shopping centers
Analityka: Schedule, By region, Validation report, Skipped

Pipeline: Scraping → JSON → Walidacja (+ retry) → Google Maps (wszystkie rekordy) → filtr godzin pracy → Excel
Kolumny Excel (EN): Company name | Address | Closing date | Opening date | Information | Entry type | Validation status | Missing fields
"""

from __future__ import annotations

import calendar
import html
import json
import logging
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup, Tag
from openpyxl import Workbook

# =========================
# KONFIGURACJA
# =========================
BASE_URL = "https://www.neueroeffnung.info"
# Ścieżki względem lokalizacji skryptu (nie cwd terminala)
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = SCRIPT_DIR / "neueroeffnung_wynik.xlsx"
JSON_OUTPUT_FILE = SCRIPT_DIR / "neueroeffnung_wynik.json"
VALIDATION_REPORT_FILE = SCRIPT_DIR / "neueroeffnung_raport_brakow.json"
PROCESSED_RECORDS_FILE = SCRIPT_DIR / "neueroeffnung_processed.json"
CACHE_FILE = SCRIPT_DIR / "neueroeffnung_detail_cache.json"
LOG_FILE = SCRIPT_DIR / "neueroeffnung_scraper.log"

REQUEST_DELAY_SEC = 0.8
MAX_PAGES_PER_CATEGORY = 30  # strona 1 ma linki do szczegółów; 2+ tylko podstawowe dane
TIMEOUT_SEC = 30
MAX_VALIDATION_RETRIES = 2

VALIDATION_STATUS_OK = "OK"
VALIDATION_STATUS_NEEDS_REVIEW = "Needs review"

SHEET_SCHEDULE = "Schedule"
SHEET_BY_REGION = "By region"
SHEET_VALIDATION_REPORT = "Validation report"
SHEET_SKIPPED = "Skipped"
SKIP_REASON_OUT_OF_RANGE = "Opening date outside Q3 2026 – Q4 2028"
SKIP_REASON_WORKING_HOURS = "Contains working hours"
SKIP_REASON_ALREADY_EXPORTED = "Already exported in previous run"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,pl;q=0.8",
}

CATEGORIES = {
    "Markets": f"{BASE_URL}/branche/supermaerkte",
    "Restaurants": f"{BASE_URL}/branche/gastronomie",
    "Drugstores": f"{BASE_URL}/branche/drogerie",
    "Shopping centers": f"{BASE_URL}/branche/einkaufszentrum",
}

DATA_SHEET_NAMES = (
    "Markets",
    "Restaurants",
    "Drugstores",
    "Shopping centers",
)

EXCEL_COLUMNS = (
    "Company name",
    "Address",
    "Closing date",
    "Opening date",
    "Information",
    "Entry type",
    "Validation status",
    "Missing fields",
)
VALIDATION_REPORT_COLUMNS = (
    "Category",
    "Company name",
    "Address",
    "Closing date",
    "Opening date",
    "Entry type",
    "Validation status",
    "Missing fields",
    "Retry attempts",
)
HARMONOGRAM_COLUMNS = (
    "Category",
    "Company name",
    "Address",
    "Closing date",
    "Opening date",
    "Entry type",
    "Information",
)
REGION_COLUMNS = (
    "Bundesland",
    "Postal code",
    "City",
    "Category",
    "Company name",
    "Address",
    "Closing date",
    "Opening date",
    "Entry type",
)
SKIPPED_COLUMNS = (
    "Category",
    "Company name",
    "Address",
    "Opening date",
    "Entry type",
    "Reason",
)

ENTRY_TYPE_LABELS = {
    "reopening": "Reopening",
    "new_opening": "Neueröffnung",
}

PLZ_BUNDESLAND = {
    "01": "Sachsen", "02": "Sachsen", "03": "Brandenburg", "04": "Sachsen",
    "05": "Sachsen-Anhalt", "06": "Sachsen-Anhalt", "07": "Thüringen", "08": "Sachsen", "09": "Sachsen",
    "10": "Berlin", "11": "Brandenburg", "12": "Berlin", "13": "Berlin", "14": "Brandenburg",
    "15": "Brandenburg", "16": "Brandenburg", "17": "Mecklenburg-Vorpommern",
    "18": "Mecklenburg-Vorpommern", "19": "Mecklenburg-Vorpommern",
    "20": "Hamburg", "21": "Niedersachsen", "22": "Hamburg", "23": "Schleswig-Holstein",
    "24": "Schleswig-Holstein", "25": "Schleswig-Holstein",
    "26": "Niedersachsen", "27": "Niedersachsen", "28": "Bremen", "29": "Niedersachsen",
    "30": "Niedersachsen", "31": "Niedersachsen", "32": "Niedersachsen",
    "33": "Nordrhein-Westfalen", "34": "Hessen", "35": "Hessen", "36": "Hessen",
    "37": "Niedersachsen", "38": "Niedersachsen", "39": "Sachsen-Anhalt",
    "40": "Nordrhein-Westfalen", "41": "Nordrhein-Westfalen", "42": "Nordrhein-Westfalen",
    "43": "Nordrhein-Westfalen", "44": "Nordrhein-Westfalen", "45": "Nordrhein-Westfalen",
    "46": "Nordrhein-Westfalen", "47": "Nordrhein-Westfalen", "48": "Nordrhein-Westfalen",
    "49": "Niedersachsen",
    "50": "Nordrhein-Westfalen", "51": "Nordrhein-Westfalen", "52": "Nordrhein-Westfalen",
    "53": "Nordrhein-Westfalen", "54": "Rheinland-Pfalz", "55": "Rheinland-Pfalz",
    "56": "Rheinland-Pfalz", "57": "Nordrhein-Westfalen", "58": "Nordrhein-Westfalen",
    "59": "Nordrhein-Westfalen",
    "60": "Hessen", "61": "Hessen", "62": "Hessen", "63": "Hessen", "64": "Hessen",
    "65": "Hessen", "66": "Saarland", "67": "Rheinland-Pfalz", "68": "Baden-Württemberg",
    "69": "Baden-Württemberg",
    "70": "Baden-Württemberg", "71": "Baden-Württemberg", "72": "Baden-Württemberg",
    "73": "Baden-Württemberg", "74": "Baden-Württemberg", "75": "Baden-Württemberg",
    "76": "Baden-Württemberg", "77": "Baden-Württemberg", "78": "Baden-Württemberg",
    "79": "Baden-Württemberg",
    "80": "Bayern", "81": "Bayern", "82": "Bayern", "83": "Bayern", "84": "Bayern",
    "85": "Bayern", "86": "Bayern", "87": "Bayern", "88": "Baden-Württemberg", "89": "Bayern",
    "90": "Bayern", "91": "Bayern", "92": "Bayern", "93": "Bayern", "94": "Bayern",
    "95": "Bayern", "96": "Bayern", "97": "Bayern", "98": "Thüringen", "99": "Thüringen",
}
MAX_ADDRESS_LENGTH = 650
MAX_INFO_LENGTH = 5000

# Filtr dat otwarcia: Q3 2026 (01.07.2026) – Q4 2028 (31.12.2028)
OPENING_FILTER_START = date(2026, 7, 1)
OPENING_FILTER_END = date(2028, 12, 31)

GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "maerz": 3,
    "märz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

PLZ_PATTERN = re.compile(r"\b(\d{4,5})\b")
STREET_HINT_PATTERN = re.compile(
    r"(?:straße|strasse|str\.|weg|platz|allee|ring|gasse|damm|ufer|chaussee|markt|"
    r"hof|brücke|bruecke|chaussee|steig|stieg|promenade|ufer|center|zentrum)",
    re.IGNORECASE,
)
HOUSE_NUMBER_PATTERN = re.compile(r"\b\d+[a-zA-Z]?\b")

DATE_PATTERN = re.compile(
    r"\b(\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\.\s*(?:Quartal|Halbjahr)\s*\d{4}|"
    r"(?:Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s*\d{4}|"
    r"\d{4})\b",
    re.IGNORECASE,
)
CLOSING_PATTERNS = [
    re.compile(r"(?:schließt|schliesst)\s+(?:am|vom|ab)?\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.I),
    re.compile(r"(?:geschlossen|temporär geschlossen|vorübergehend geschlossen)\s+(?:ab|vom|am|bis)?\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.I),
    re.compile(r"(?:Schließung|Schliessung)[:\s]+(\d{1,2}\.\d{1,2}\.\d{4})", re.I),
    re.compile(r"vom\s+(\d{1,2}\.\d{1,2}\.\d{4})\s+bis", re.I),
    re.compile(
        r"(?:Umbau|Renovierung|Kernsanierung).*?(?:schließt|schliesst)\s+(?:am|vom|ab)?\s*(\d{1,2}\.\d{1,2}\.\d{4})",
        re.I,
    ),
]
WORKING_HOURS_TEXT_PATTERNS = (
    re.compile(r"öffnungszeiten", re.I),
    re.compile(r"opening hours", re.I),
    re.compile(r"geschäftszeiten", re.I),
    re.compile(r"godziny otwarcia", re.I),
    re.compile(r"\bmo\.?\s*[-–]\s*(fr|sa|so)\b", re.I),
    re.compile(r"\b(mo|di|mi|do|fr|sa|so)\s+\d{1,2}:\d{2}", re.I),
    re.compile(
        r"\b(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b[^\n]{0,40}\d{1,2}:\d{2}",
        re.I,
    ),
    re.compile(r"\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}"),
)


@dataclass
class ListingItem:
    nazwa: str
    data_otwarcia: str
    adres_lista: str
    url: str = ""
    entry_type: str = ""


@dataclass
class Record:
    nazwa_firmy: str
    adres: str
    data_zamkniecia: str
    data_otwarcia: str
    informacja: str = ""
    typ_wpisu: str = ""
    kategoria: str = ""
    detail_url: str = ""
    listing_adres_lista: str = ""
    entry_type_raw: str = ""
    status_walidacji: str = VALIDATION_STATUS_OK
    brakujace_pola: str = ""
    proby_ponowienia: int = 0
    godziny_pracy: str = ""
    maps_zweryfikowany: bool = False


@dataclass
class SkippedRecord:
    kategoria: str
    nazwa_firmy: str
    adres: str
    data_otwarcia: str
    typ_wpisu: str
    powod: str


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("neueroeffnung")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def prepare_fresh_output_files(logger: logging.Logger) -> None:
    """Usuwa poprzednie pliki wynikowe — każde uruchomienie zaczyna od pustych tabel."""
    for path in (OUTPUT_FILE, JSON_OUTPUT_FILE, VALIDATION_REPORT_FILE):
        if path.exists():
            path.unlink()
            logger.info("Usunięto poprzedni plik wynikowy: %s", path.name)


def listing_fingerprint(item: ListingItem) -> str:
    """Stabilny klucz rekordu — ten sam co przy deduplikacji listy."""
    return "|".join(
        (
            item.nazwa.lower().strip(),
            item.adres_lista.lower().strip(),
            item.data_otwarcia.lower().strip(),
        )
    )


def record_fingerprint(record: Record) -> str:
    """Stabilny klucz rekordu do rejestru już wyeksportowanych wpisów."""
    location = (record.listing_adres_lista or record.adres).lower().strip()
    return "|".join(
        (
            record.nazwa_firmy.lower().strip(),
            location,
            record.data_otwarcia.lower().strip(),
        )
    )


def load_processed_records(logger: logging.Logger) -> set[str]:
    if not PROCESSED_RECORDS_FILE.exists():
        return set()
    try:
        with open(PROCESSED_RECORDS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fingerprints = payload.get("fingerprints", [])
        logger.info("Wczytano rejestr wyeksportowanych rekordów: %s wpisów", len(fingerprints))
        return set(fingerprints)
    except Exception as exc:
        logger.warning("Nie wczytano rejestru wyeksportowanych rekordów: %s", exc)
        return set()


def save_processed_records(processed: set[str], logger: logging.Logger) -> None:
    try:
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "fingerprints": sorted(processed),
        }
        with open(PROCESSED_RECORDS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("Zapisano rejestr wyeksportowanych rekordów: %s wpisów", len(processed))
    except Exception as exc:
        logger.error("Błąd zapisu rejestru wyeksportowanych rekordów: %s", exc)


def import_processed_from_json(
    json_path: Path,
    processed: set[str],
    logger: logging.Logger,
) -> None:
    """Przed usunięciem poprzedniego JSON — zapamiętuje rekordy jako już wyeksportowane."""
    if not json_path.exists():
        return
    try:
        sheets = load_json_dataset(json_path, logger)
        added = 0
        for records in sheets.values():
            for record in records:
                fp = record_fingerprint(record)
                if fp not in processed:
                    processed.add(fp)
                    added += 1
        if added:
            logger.info(
                "Zaimportowano %s rekordów z poprzedniego JSON do rejestru wyeksportowanych.",
                added,
            )
    except Exception as exc:
        logger.warning("Nie zaimportowano poprzedniego JSON do rejestru: %s", exc)


def filter_already_processed_records(
    sheets: dict[str, list[Record]],
    processed: set[str],
    skipped: list[SkippedRecord],
    logger: logging.Logger,
) -> dict[str, list[Record]]:
    """Usuwa rekordy już wyeksportowane w poprzednich runach (JSON/Excel)."""
    removed = 0
    for category_name in DATA_SHEET_NAMES:
        kept: list[Record] = []
        for record in sheets.get(category_name, []):
            fp = record_fingerprint(record)
            if fp not in processed:
                kept.append(record)
                continue
            removed += 1
            logger.info(
                "  Pominięto (już wyeksportowany): %s | %s",
                record.nazwa_firmy,
                record.data_otwarcia,
            )
            skipped.append(
                SkippedRecord(
                    kategoria=category_name,
                    nazwa_firmy=record.nazwa_firmy,
                    adres=record.adres,
                    data_otwarcia=record.data_otwarcia,
                    typ_wpisu=record.typ_wpisu,
                    powod=SKIP_REASON_ALREADY_EXPORTED,
                )
            )
        sheets[category_name] = kept
    if removed:
        logger.info("Pominięto %s rekordów już obecnych w poprzednim JSON/Excel.", removed)
    return sheets


def mark_records_as_processed(
    sheets: dict[str, list[Record]],
    processed: set[str],
) -> None:
    for category_name in DATA_SHEET_NAMES:
        for record in sheets.get(category_name, []):
            processed.add(record_fingerprint(record))


def load_cache(logger: logging.Logger) -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Nie wczytano cache: %s", exc)
        return {}


def save_cache(cache: dict, logger: logging.Logger) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("Błąd zapisu cache: %s", exc)


def fetch_html(session: requests.Session, url: str, logger: logging.Logger) -> str:
    resp = session.get(url, headers=HEADERS, timeout=TIMEOUT_SEC)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    time.sleep(REQUEST_DELAY_SEC)
    return resp.text


def clean_text(value: str) -> str:
    return clean_text_local(value)


def clean_text_local(value: str) -> str:
    """Lokalne czyszczenie – HTML entities, białe znaki, typowe artefakty kodowania."""
    if not value:
        return ""

    text = html.unescape(value)
    text = text.replace("\xa0", " ")
    text = " ".join(text.split()).strip()

    mojibake_fixes = {
        "Ã¤": "ä", "Ã¶": "ö", "Ã¼": "ü", "ÃŸ": "ß",
        "Ã„": "Ä", "Ã–": "Ö", "Ãœ": "Ü",
        "â€“": "-", "â€”": "-", "â€˜": "'", "â€™": "'",
        "â€œ": '"', "â€": '"', "Â ": " ",
    }
    for broken, fixed in mojibake_fixes.items():
        text = text.replace(broken, fixed)

    # Usuń niewidoczne znaki sterujące, zostaw litery (w tym niemieckie)
    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) != "Cc" or ch in {"\n", "\t"}
    )
    return " ".join(text.split()).strip()


def clean_record(record: Record, logger: logging.Logger) -> Record:
    """Lokalne czyszczenie pól tekstowych rekordu."""
    return Record(
        nazwa_firmy=clean_text_local(record.nazwa_firmy),
        adres=validate_address(record.adres, logger, record.nazwa_firmy),
        data_zamkniecia=clean_text_local(record.data_zamkniecia),
        data_otwarcia=clean_text_local(record.data_otwarcia),
        informacja=validate_information(record.informacja, logger, record.nazwa_firmy),
        typ_wpisu=record.typ_wpisu,
        kategoria=record.kategoria,
        detail_url=record.detail_url,
        listing_adres_lista=record.listing_adres_lista,
        entry_type_raw=record.entry_type_raw,
        status_walidacji=record.status_walidacji,
        brakujace_pola=record.brakujace_pola,
        proby_ponowienia=record.proby_ponowienia,
        godziny_pracy=clean_text_local(record.godziny_pracy),
        maps_zweryfikowany=record.maps_zweryfikowany,
    )


def clean_records(records: list[Record], logger: logging.Logger) -> list[Record]:
    if not records:
        return []
    return [clean_record(record, logger) for record in records]


def clean_all_sheets(
    sheets: dict[str, list[Record]],
    logger: logging.Logger,
) -> dict[str, list[Record]]:
    cleaned: dict[str, list[Record]] = {}
    for sheet_name, records in sheets.items():
        logger.info("Czyszczenie arkusza '%s' (%s rekordów)", sheet_name, len(records))
        cleaned[sheet_name] = clean_records(records, logger)
    return cleaned


def extract_plz(text: str) -> str:
    match = PLZ_PATTERN.search(text or "")
    return match.group(1) if match else ""


def plz_to_bundesland(plz: str) -> str:
    if not plz or len(plz) < 2:
        return ""
    return PLZ_BUNDESLAND.get(plz[:2], "")


def extract_city_from_address(adres: str) -> str:
    adres = clean_text_local(adres)
    if not adres:
        return ""
    plz = extract_plz(adres)
    if not plz:
        return ""
    if "," in adres:
        tail = adres.split(",")[-1].strip()
        city = re.sub(rf"^{re.escape(plz)}\s*", "", tail).strip()
        if city:
            return city
    match = re.search(rf"\b{re.escape(plz)}\s+(.+)$", adres)
    return clean_text_local(match.group(1)) if match else ""


def format_entry_type(entry_type: str) -> str:
    return ENTRY_TYPE_LABELS.get(entry_type.strip(), entry_type or "")


def opening_date_sort_key(data_otwarcia: str) -> date:
    parsed = parse_opening_date_to_range(data_otwarcia)
    return parsed[0] if parsed else date(9999, 12, 31)


def _month_range(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _normalize_month_key(value: str) -> str:
    return (
        value.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def parse_opening_date_to_range(text: str) -> tuple[date, date] | None:
    """Zamienia niemiecki opis daty otwarcia na przedział [od, do]."""
    text = clean_text_local(text)
    if not text:
        return None

    exact = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", text)
    if exact:
        day, month, year = (int(exact.group(1)), int(exact.group(2)), int(exact.group(3)))
        try:
            single = date(year, month, day)
            return single, single
        except ValueError:
            return None

    quarter = re.search(r"(\d)\.\s*Quartal\s*(\d{4})", text, re.IGNORECASE)
    if quarter:
        q_num, year = int(quarter.group(1)), int(quarter.group(2))
        if 1 <= q_num <= 4:
            start_month = (q_num - 1) * 3 + 1
            end_month = start_month + 2
            return _month_range(year, start_month)[0], _month_range(year, end_month)[1]

    half = re.search(r"(\d)\.\s*Halbjahr\s*(\d{4})", text, re.IGNORECASE)
    if half:
        half_num, year = int(half.group(1)), int(half.group(2))
        if half_num == 1:
            return date(year, 1, 1), date(year, 6, 30)
        if half_num == 2:
            return date(year, 7, 1), date(year, 12, 31)

    month_match = re.search(
        r"(Januar|Februar|M(?:a|ä)rz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if month_match:
        month_key = _normalize_month_key(month_match.group(1))
        if month_key.startswith("mar"):
            month_key = "maerz"
        month_num = GERMAN_MONTHS.get(month_key)
        year = int(month_match.group(2))
        if month_num:
            return _month_range(year, month_num)

    year_only = re.match(r"^(\d{4})$", text)
    if year_only:
        year = int(year_only.group(1))
        return date(year, 1, 1), date(year, 12, 31)

    embedded = DATE_PATTERN.search(text)
    if embedded and embedded.group(1) != text:
        return parse_opening_date_to_range(embedded.group(1))

    return None


def is_opening_date_in_range(data_otwarcia: str) -> bool:
    """True, gdy data otwarcia nachodzi na Q3 2026 – Q4 2028."""
    parsed = parse_opening_date_to_range(data_otwarcia)
    if not parsed:
        return False
    range_start, range_end = parsed
    return range_start <= OPENING_FILTER_END and range_end >= OPENING_FILTER_START


def is_opening_date_outside_filter(data_otwarcia: str) -> bool:
    """True, gdy data jest odczytana i całkowicie poza Q3 2026 – Q4 2028."""
    parsed = parse_opening_date_to_range(data_otwarcia)
    if not parsed:
        return False
    return not is_opening_date_in_range(data_otwarcia)


def filter_records_by_opening_date(
    records: list[Record],
    logger: logging.Logger,
) -> list[Record]:
    filtered: list[Record] = []
    for record in records:
        if is_opening_date_in_range(record.data_otwarcia):
            filtered.append(record)
        else:
            logger.info(
                "  Pominięto (data poza Q3 2026 – Q4 2028): %s | %s",
                record.nazwa_firmy,
                record.data_otwarcia or "(brak daty)",
            )
    return filtered


def purge_out_of_range_records(
    sheets: dict[str, list[Record]],
    skipped: list[SkippedRecord],
    logger: logging.Logger,
) -> dict[str, list[Record]]:
    """
    Twarda reguła: rekordy z datą otwarcia poza Q3 2026 – Q4 2028
    nie trafiają do JSON, walidacji, Google Maps ani Excela — tylko do arkusza Skipped.
    """
    removed = 0
    for category_name in DATA_SHEET_NAMES:
        kept: list[Record] = []
        for record in sheets.get(category_name, []):
            if not is_opening_date_outside_filter(record.data_otwarcia):
                kept.append(record)
                continue
            removed += 1
            logger.info(
                "  Odrzucono (twardy filtr dat): %s | %s | %s",
                category_name,
                record.nazwa_firmy,
                record.data_otwarcia or "(brak/nieparsowalna data)",
            )
            skipped.append(
                SkippedRecord(
                    kategoria=category_name,
                    nazwa_firmy=record.nazwa_firmy,
                    adres=record.adres,
                    data_otwarcia=record.data_otwarcia,
                    typ_wpisu=record.typ_wpisu,
                    powod=SKIP_REASON_OUT_OF_RANGE,
                )
            )
        sheets[category_name] = kept
    if removed:
        logger.info("Twardy filtr dat: usunięto %s rekordów spoza zakresu.", removed)
    return sheets


def contains_working_hours(record: Record) -> bool:
    """True gdy rekord zawiera godziny pracy (pole Maps lub wzorzec w tekście JSON)."""
    if clean_text_local(record.godziny_pracy):
        return True
    for value in (
        record.nazwa_firmy,
        record.adres,
        record.informacja,
        record.listing_adres_lista,
    ):
        text = clean_text_local(value)
        if not text:
            continue
        for pattern in WORKING_HOURS_TEXT_PATTERNS:
            if pattern.search(text):
                return True
    return False


def purge_records_with_working_hours(
    sheets: dict[str, list[Record]],
    skipped: list[SkippedRecord],
    logger: logging.Logger,
) -> dict[str, list[Record]]:
    """
    Twarda reguła przed Excel: rekordy z godzinami pracy w JSON
    nie trafiają do Excela — tylko do arkusza Skipped.
    """
    removed = 0
    for category_name in DATA_SHEET_NAMES:
        kept: list[Record] = []
        for record in sheets.get(category_name, []):
            if not contains_working_hours(record):
                kept.append(record)
                continue
            removed += 1
            logger.info(
                "  Odrzucono (godziny pracy): %s | %s | %s",
                category_name,
                record.nazwa_firmy,
                record.godziny_pracy or record.informacja[:80],
            )
            skipped.append(
                SkippedRecord(
                    kategoria=category_name,
                    nazwa_firmy=record.nazwa_firmy,
                    adres=record.adres,
                    data_otwarcia=record.data_otwarcia,
                    typ_wpisu=record.typ_wpisu,
                    powod=SKIP_REASON_WORKING_HOURS,
                )
            )
        sheets[category_name] = kept
    if removed:
        logger.info("Twardy filtr godzin pracy: usunięto %s rekordów przed eksportem do Excel.", removed)
    return sheets


def is_incomplete_address(adres: str) -> bool:
    """True gdy brak ulicy – tylko kod pocztowy i miejscowość."""
    adres = clean_text_local(adres)
    if not adres:
        return True
    if STREET_HINT_PATTERN.search(adres):
        return False
    # Format: „Ulica 12, 12345 Miasto”
    if "," in adres:
        street_part = adres.split(",", 1)[0]
        if HOUSE_NUMBER_PATTERN.search(street_part) and re.search(r"[A-Za-zÄÖÜäöüß]", street_part):
            return False
    # Same „80331 München” lub „49637 Samtgemeinde Artland”
    if re.match(r"^\d{4,5}\s+\S", adres):
        return True
    return len(adres) < 12


def validate_address(adres: str, logger: logging.Logger, context: str = "") -> str:
    """Normalizuje i obcina adres do MAX_ADDRESS_LENGTH (650) znaków."""
    adres = clean_text_local(adres)
    if len(adres) > MAX_ADDRESS_LENGTH:
        logger.warning(
            "Adres obcięty do %s znaków%s: %s…",
            MAX_ADDRESS_LENGTH,
            f" [{context}]" if context else "",
            adres[:80],
        )
        adres = adres[:MAX_ADDRESS_LENGTH].rstrip(" ,.;")
    return adres


def validate_information(informacja: str, logger: logging.Logger, context: str = "") -> str:
    """Normalizuje i obcina opis sklepu do MAX_INFO_LENGTH znaków."""
    informacja = clean_text_local(informacja)
    if len(informacja) > MAX_INFO_LENGTH:
        logger.warning(
            "Informacja obcięta do %s znaków%s: %s…",
            MAX_INFO_LENGTH,
            f" [{context}]" if context else "",
            informacja[:80],
        )
        informacja = informacja[:MAX_INFO_LENGTH].rstrip(" ,.;")
    return informacja


def score_search_match(nazwa: str, adres_lista: str, result_title: str, result_loc: str) -> int:
    score = 0
    nazwa_l = nazwa.lower().strip()
    title_l = result_title.lower().strip()
    loc_l = result_loc.lower().strip()
    lista_l = adres_lista.lower().strip()

    if nazwa_l == title_l:
        score += 20
    elif nazwa_l in title_l or title_l in nazwa_l:
        score += 10

    plz = extract_plz(adres_lista)
    if plz and plz in result_loc:
        score += 15

    city_hint = re.sub(r"^\d{4,5}\s*", "", adres_lista).strip().lower()
    if city_hint and city_hint in loc_l:
        score += 8
    if lista_l and lista_l in loc_l:
        score += 12
    return score


def resolve_detail_url(
    session: requests.Session,
    item: ListingItem,
    cache: dict,
    logger: logging.Logger,
) -> str:
    """Szuka strony szczegółów przez /suche/ gdy lista nie ma linku."""
    cache_key = f"search::{item.nazwa}::{item.adres_lista}"
    if cache_key in cache:
        return cache[cache_key].get("url", "")

    query = f"{item.nazwa} {item.adres_lista}".strip()
    search_url = f"{BASE_URL}/suche/{quote_plus(query)}"
    logger.info("  Szukam adresu: %s", query)

    try:
        html = fetch_html(session, search_url, logger)
    except Exception as exc:
        logger.warning("Błąd wyszukiwania adresu dla '%s': %s", query, exc)
        cache[cache_key] = {"url": ""}
        save_cache(cache, logger)
        return ""

    soup = BeautifulSoup(html, "html.parser")
    best_url = ""
    best_score = 0

    for link in soup.select("a.entry-card"):
        title_el = link.select_one(".card-title")
        if not title_el:
            continue
        loc_el = link.select(".text-muted small, .card-text")
        result_loc = clean_text(loc_el[-1].get_text()) if loc_el else ""
        result_title = clean_text(title_el.get_text())
        score = score_search_match(item.nazwa, item.adres_lista, result_title, result_loc)
        if score > best_score:
            best_score = score
            best_url = urljoin(search_url, link.get("href", ""))

    if best_url and best_score >= 15:
        logger.info("  -> Znaleziono URL (score=%s): %s", best_score, best_url)
    else:
        logger.warning("  -> Nie znaleziono pełnego adresu dla: %s", query)
        best_url = ""

    cache[cache_key] = {"url": best_url}
    save_cache(cache, logger)
    return best_url


def parse_list_page(html: str, page_url: str) -> list[ListingItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ListingItem] = []

    for link in soup.select("a.entry-card"):
        title_el = link.select_one(".card-title")
        if not title_el:
            continue
        date_el = link.select_one("small")
        loc_el = link.select(".text-muted small")
        location = clean_text(loc_el[-1].get_text()) if loc_el else ""
        badge = link.select_one("[data-entry-type]")
        items.append(
            ListingItem(
                nazwa=clean_text(title_el.get_text()),
                data_otwarcia=clean_text(date_el.get_text()) if date_el else "",
                adres_lista=location,
                url=urljoin(page_url, link.get("href", "")),
                entry_type=badge.get("data-entry-type", "") if badge else "",
            )
        )

    for col in soup.select("div.col-md-3.mb-4"):
        if col.select_one("a.entry-card"):
            continue
        card = col.select_one(".card")
        if not card:
            continue
        title_el = card.select_one(".card-title")
        if not title_el:
            continue
        subtitle = card.select_one(".card-subtitle")
        location_el = card.select_one(".card-text")
        items.append(
            ListingItem(
                nazwa=clean_text(title_el.get_text()),
                data_otwarcia=clean_text(subtitle.get_text()) if subtitle else "",
                adres_lista=clean_text(location_el.get_text()) if location_el else "",
                url="",
                entry_type="",
            )
        )

    return items


def iter_category_pages(session: requests.Session, base_url: str, logger: logging.Logger) -> Iterator[list[ListingItem]]:
    for page_num in range(1, MAX_PAGES_PER_CATEGORY + 1):
        page_url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
        logger.info("Lista: %s", page_url)
        try:
            html = fetch_html(session, page_url, logger)
        except Exception as exc:
            logger.error("Błąd pobierania listy %s: %s", page_url, exc)
            break

        items = parse_list_page(html, page_url)
        if not items:
            break
        yield items

        soup = BeautifulSoup(html, "html.parser")
        if not soup.select('link[rel="next"]'):
            break


def extract_opening_date(soup: BeautifulSoup) -> str:
    for h2 in soup.find_all("h2"):
        text = clean_text(h2.get_text(" ", strip=True))
        if text.lower().startswith("eröffnung:") or text.lower().startswith("eroeffnung:"):
            bold = h2.find("span", class_="font-weight-bold")
            if bold:
                return clean_text(bold.get_text())
            match = DATE_PATTERN.search(text)
            if match:
                return match.group(1)
    return ""


def extract_address(soup: BeautifulSoup) -> str:
    kontakt = soup.find("h2", string=re.compile(r"Kontaktdaten", re.I))
    if kontakt:
        container = kontakt.find_parent("div")
        if container:
            marker = container.find("i", class_=re.compile(r"fa-map-marker-alt"))
            if marker:
                parts: list[str] = []
                for sibling in marker.next_siblings:
                    if isinstance(sibling, Tag) and sibling.name == "div":
                        break
                    if isinstance(sibling, str):
                        txt = clean_text(sibling)
                        if txt:
                            parts.append(txt)
                if parts:
                    return clean_text(" ".join(parts))

    marker = soup.find("i", class_=re.compile(r"fa-map-marker-alt"))
    if marker:
        txt = clean_text(marker.parent.get_text(" ", strip=True))
        txt = re.sub(r"^.*?\s(?=\d{4,5}\s)", "", txt)
        if re.search(r"\d{4,5}", txt):
            return txt
    return ""


def extract_closing_date(text: str) -> str:
    if not text:
        return ""
    for pattern in CLOSING_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def enrich_closing_date(record: Record) -> None:
    """Uzupełnia data_zamkniecia z opisu, jeśli pole jest puste."""
    if not record.data_zamkniecia.strip() and record.informacja.strip():
        extracted = extract_closing_date(record.informacja)
        if extracted:
            record.data_zamkniecia = extracted


def enrich_all_closing_dates(sheets: dict[str, list[Record]]) -> None:
    for category_name in DATA_SHEET_NAMES:
        for record in sheets.get(category_name, []):
            enrich_closing_date(record)


def extract_description_text(soup: BeautifulSoup) -> str:
    return extract_information(soup)


def extract_information(soup: BeautifulSoup) -> str:
    """Pełny opis sklepu ze strony szczegółów (akapit pod „Neueröffnung von …”)."""
    chunks: list[str] = []
    for div in soup.select("div.text-wrap-break-word"):
        paragraphs = [
            clean_text(p.get_text(" ", strip=True))
            for p in div.select("p")
        ]
        if paragraphs:
            txt = " ".join(p for p in paragraphs if p)
        else:
            txt = clean_text(div.get_text(" ", strip=True))
        if txt and "Premium" not in txt:
            chunks.append(txt)
    return " ".join(chunks)


def fetch_detail(
    session: requests.Session,
    url: str,
    cache: dict,
    logger: logging.Logger,
) -> tuple[str, str, str, str]:
    if url in cache:
        cached = cache[url]
        informacja = cached.get("informacja", "") or cached.get("opis", "")
        return (
            cached.get("adres", ""),
            cached.get("data_otwarcia", ""),
            cached.get("data_zamkniecia", ""),
            informacja,
        )

    html = fetch_html(session, url, logger)
    soup = BeautifulSoup(html, "html.parser")

    adres = extract_address(soup)
    data_otwarcia = extract_opening_date(soup)
    informacja = extract_information(soup)
    data_zamkniecia = extract_closing_date(informacja)

    cache[url] = {
        "adres": adres,
        "data_otwarcia": data_otwarcia,
        "data_zamkniecia": data_zamkniecia,
        "informacja": informacja,
    }
    save_cache(cache, logger)
    return adres, data_otwarcia, data_zamkniecia, informacja


def listing_to_record(
    session: requests.Session,
    item: ListingItem,
    cache: dict,
    logger: logging.Logger,
) -> Record:
    adres = item.adres_lista
    data_otwarcia = item.data_otwarcia
    data_zamkniecia = ""
    informacja = ""
    detail_url = item.url

    if not detail_url:
        detail_url = resolve_detail_url(session, item, cache, logger)

    if detail_url:
        try:
            detail_adres, detail_otwarcie, detail_zamkniecie, detail_informacja = fetch_detail(
                session, detail_url, cache, logger
            )
            if detail_adres:
                adres = detail_adres
            elif is_incomplete_address(adres):
                logger.warning(
                    "Brak pełnego adresu na stronie szczegółów: %s (%s)",
                    item.nazwa,
                    detail_url,
                )
            if detail_otwarcie:
                data_otwarcia = detail_otwarcie
            data_zamkniecia = detail_zamkniecie
            informacja = detail_informacja
        except Exception as exc:
            logger.warning("Błąd szczegółów %s: %s", detail_url, exc)

    context = f"{item.nazwa} | {item.adres_lista}"
    adres = validate_address(adres, logger, context)
    informacja = validate_information(informacja, logger, item.nazwa)

    resolved_url = detail_url or ""

    record = Record(
        nazwa_firmy=item.nazwa,
        adres=adres,
        data_zamkniecia=data_zamkniecia,
        data_otwarcia=data_otwarcia,
        informacja=informacja,
        typ_wpisu=format_entry_type(item.entry_type),
        detail_url=resolved_url,
        listing_adres_lista=item.adres_lista,
        entry_type_raw=item.entry_type,
    )
    enrich_closing_date(record)
    return record


def collect_category_records(
    session: requests.Session,
    base_url: str,
    cache: dict,
    logger: logging.Logger,
    category_name: str = "",
    skipped: list[SkippedRecord] | None = None,
    processed: set[str] | None = None,
) -> list[Record]:
    records: list[Record] = []
    seen: set[tuple[str, str, str]] = set()

    for batch in iter_category_pages(session, base_url, logger):
        for item in batch:
            key = (item.nazwa.lower(), item.adres_lista.lower(), item.data_otwarcia)
            if key in seen:
                continue

            seen.add(key)
            if processed is not None:
                fp = listing_fingerprint(item)
                if fp in processed:
                    logger.info(
                        "  Pominięto (już wyeksportowany, bez pobierania): %s | %s",
                        item.nazwa,
                        item.data_otwarcia,
                    )
                    if skipped is not None and category_name:
                        skipped.append(
                            SkippedRecord(
                                kategoria=category_name,
                                nazwa_firmy=item.nazwa,
                                adres=item.adres_lista,
                                data_otwarcia=item.data_otwarcia,
                                typ_wpisu=format_entry_type(item.entry_type),
                                powod=SKIP_REASON_ALREADY_EXPORTED,
                            )
                        )
                    continue

            record = listing_to_record(session, item, cache, logger)
            record.kategoria = category_name
            if not is_opening_date_in_range(record.data_otwarcia):
                logger.info(
                    "  Pominięto (data poza Q3 2026 – Q4 2028): %s | %s",
                    record.nazwa_firmy,
                    record.data_otwarcia or "(brak daty)",
                )
                if skipped is not None and category_name:
                    skipped.append(
                        SkippedRecord(
                            kategoria=category_name,
                            nazwa_firmy=record.nazwa_firmy,
                            adres=record.adres,
                            data_otwarcia=record.data_otwarcia,
                            typ_wpisu=record.typ_wpisu,
                            powod=SKIP_REASON_OUT_OF_RANGE,
                        )
                    )
                continue
            records.append(record)
            logger.info("  + %s | %s | otwarcie: %s", record.nazwa_firmy, record.adres, record.data_otwarcia)

    return records


def record_to_dict(record: Record) -> dict:
    return asdict(record)


def record_from_dict(data: dict) -> Record:
    allowed = {field.name for field in fields(Record)}
    return Record(**{key: data[key] for key in allowed if key in data})


def save_json_dataset(
    sheets: dict[str, list[Record]],
    output_path: Path,
    logger: logging.Logger,
    *,
    stage: str,
    validation_summary: dict | None = None,
) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "opening_filter": {
            "start": OPENING_FILTER_START.isoformat(),
            "end": OPENING_FILTER_END.isoformat(),
        },
        "sheets": {
            name: [record_to_dict(record) for record in sheets.get(name, [])]
            for name in DATA_SHEET_NAMES
        },
    }
    if validation_summary is not None:
        payload["validation_summary"] = validation_summary
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("Zapisano JSON (%s): %s", stage, output_path.resolve())


def load_json_dataset(path: Path, logger: logging.Logger) -> dict[str, list[Record]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    sheets: dict[str, list[Record]] = {}
    for name in DATA_SHEET_NAMES:
        sheets[name] = [record_from_dict(item) for item in payload.get("sheets", {}).get(name, [])]
    logger.info("Wczytano JSON: %s rekordów łącznie", sum(len(v) for v in sheets.values()))
    return sheets


def find_missing_fields(record: Record) -> list[str]:
    """Zwraca listę brakujących/niepełnych pól w rekordzie."""
    missing: list[str] = []
    if not clean_text_local(record.nazwa_firmy):
        missing.append("company name")
    if not clean_text_local(record.adres):
        missing.append("address")
    elif is_incomplete_address(record.adres):
        missing.append("address (incomplete)")
    if not clean_text_local(record.data_otwarcia):
        missing.append("opening date")
    if not clean_text_local(record.informacja):
        missing.append("information")
    return missing


def apply_validation_status(record: Record) -> Record:
    missing = find_missing_fields(record)
    if missing:
        record.status_walidacji = VALIDATION_STATUS_NEEDS_REVIEW
        record.brakujace_pola = ", ".join(missing)
    else:
        record.status_walidacji = VALIDATION_STATUS_OK
        record.brakujace_pola = ""
    return record


def validate_all_records(sheets: dict[str, list[Record]]) -> dict[str, int]:
    ok_count = 0
    review_count = 0
    for category_name in DATA_SHEET_NAMES:
        for idx, record in enumerate(sheets.get(category_name, [])):
            updated = apply_validation_status(record)
            sheets[category_name][idx] = updated
            if updated.status_walidacji == VALIDATION_STATUS_OK:
                ok_count += 1
            else:
                review_count += 1
    return {"ok": ok_count, "wymaga_weryfikacji": review_count, "total": ok_count + review_count}


def invalidate_record_cache(record: Record, cache: dict, logger: logging.Logger) -> None:
    if record.detail_url and record.detail_url in cache:
        del cache[record.detail_url]
    search_key = f"search::{record.nazwa_firmy}::{record.listing_adres_lista}"
    if search_key in cache:
        del cache[search_key]
    save_cache(cache, logger)


def retry_record(
    session: requests.Session,
    record: Record,
    cache: dict,
    logger: logging.Logger,
) -> Record:
    """Ponawia pobranie szczegółów rekordu z wyczyszczonym cache."""
    logger.info(
        "  🔄 Ponawiam pobieranie: %s | braki: %s",
        record.nazwa_firmy,
        record.brakujace_pola or "(brak)",
    )
    invalidate_record_cache(record, cache, logger)
    item = ListingItem(
        nazwa=record.nazwa_firmy,
        data_otwarcia=record.data_otwarcia,
        adres_lista=record.listing_adres_lista or record.adres,
        url=record.detail_url,
        entry_type=record.entry_type_raw,
    )
    refreshed = listing_to_record(session, item, cache, logger)
    refreshed.kategoria = record.kategoria
    refreshed.proby_ponowienia = record.proby_ponowienia + 1
    return apply_validation_status(refreshed)


def run_validation_pipeline(
    session: requests.Session,
    sheets: dict[str, list[Record]],
    cache: dict,
    logger: logging.Logger,
) -> tuple[dict[str, list[Record]], dict[str, int]]:
    """
    Walidacja JSON → ponowienie pobrania → oznaczenie rekordów z brakami.
    """
    logger.info("--- Walidacja danych JSON ---")
    summary = validate_all_records(sheets)
    logger.info(
        "Walidacja (przed ponowieniem): OK=%s, wymaga weryfikacji=%s",
        summary["ok"],
        summary["wymaga_weryfikacji"],
    )

    for attempt in range(1, MAX_VALIDATION_RETRIES + 1):
        to_retry: list[tuple[str, int]] = []
        for category_name in DATA_SHEET_NAMES:
            for idx, record in enumerate(sheets.get(category_name, [])):
                if record.status_walidacji != VALIDATION_STATUS_OK:
                    if is_opening_date_outside_filter(record.data_otwarcia):
                        continue
                    to_retry.append((category_name, idx))
        if not to_retry:
            logger.info("✅ Walidacja OK — wszystkie rekordy kompletne.")
            break

        logger.info(
            "🔄 Próba ponowienia %s/%s — %s rekordów do uzupełnienia",
            attempt,
            MAX_VALIDATION_RETRIES,
            len(to_retry),
        )
        for category_name, idx in to_retry:
            record = sheets[category_name][idx]
            sheets[category_name][idx] = retry_record(session, record, cache, logger)

        summary = validate_all_records(sheets)
        logger.info(
            "Po próbie %s: OK=%s, wymaga weryfikacji=%s",
            attempt,
            summary["ok"],
            summary["wymaga_weryfikacji"],
        )

    if summary["wymaga_weryfikacji"]:
        logger.warning(
            "⚠️ %s rekordów nadal wymaga ręcznej weryfikacji — zobacz Raport braków.",
            summary["wymaga_weryfikacji"],
        )

    return sheets, summary


def run_maps_verification_pipeline(
    sheets: dict[str, list[Record]],
    logger: logging.Logger,
) -> dict[str, list[Record]]:
    """Weryfikuje i uzupełnia wszystkie rekordy JSON przez Google Maps (Playwright, headless)."""
    from google_maps_enricher import (
        GoogleMapsEnricher,
        is_enrichment_enabled,
        load_maps_cache,
        save_maps_cache,
    )

    if not is_enrichment_enabled():
        logger.info("Google Maps verification wyłączone (ENABLE_GOOGLE_MAPS_ENRICHMENT=false)")
        return sheets

    total = sum(len(sheets.get(name, [])) for name in DATA_SHEET_NAMES)
    if total == 0:
        logger.info("Google Maps: brak rekordów do weryfikacji.")
        return sheets

    logger.info("--- Google Maps: weryfikacja %s rekordów ---", total)
    maps_cache = load_maps_cache(logger)

    try:
        with GoogleMapsEnricher(logger, maps_cache) as enricher:
            for category_name in DATA_SHEET_NAMES:
                for idx, record in enumerate(sheets.get(category_name, [])):
                    partial = record.listing_adres_lista or record.adres
                    result = enricher.verify_place(record.nazwa_firmy, partial)
                    record.maps_zweryfikowany = result.verified
                    if result.adres:
                        context = f"{record.nazwa_firmy} | {partial}"
                        record.adres = validate_address(result.adres, logger, context)
                    if result.godziny_pracy:
                        record.godziny_pracy = clean_text_local(result.godziny_pracy)
                    sheets[category_name][idx] = apply_validation_status(record)
                    if result.verified:
                        logger.info(
                            "  ✓ Maps: %s | adres=%s | godziny=%s",
                            record.nazwa_firmy,
                            "tak" if result.adres else "brak",
                            "tak" if result.godziny_pracy else "brak",
                        )
    except Exception as exc:
        logger.error("Google Maps verification przerwane: %s", exc)
    finally:
        save_maps_cache(maps_cache, logger)

    return sheets


def run_maps_enrichment_pipeline(
    sheets: dict[str, list[Record]],
    logger: logging.Logger,
) -> dict[str, list[Record]]:
    """Alias wsteczny — weryfikuje wszystkie rekordy przez Google Maps."""
    return run_maps_verification_pipeline(sheets, logger)


def build_validation_report_rows(sheets: dict[str, list[Record]]) -> list[list]:
    rows: list[list] = []
    for category_name in DATA_SHEET_NAMES:
        for record in sheets.get(category_name, []):
            if record.status_walidacji != VALIDATION_STATUS_OK:
                rows.append(
                    [
                        category_name,
                        record.nazwa_firmy,
                        record.adres,
                        record.data_zamkniecia,
                        record.data_otwarcia,
                        record.typ_wpisu,
                        record.status_walidacji,
                        record.brakujace_pola,
                        record.proby_ponowienia,
                    ]
                )
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def save_validation_report_json(
    sheets: dict[str, list[Record]],
    summary: dict[str, int],
    output_path: Path,
    logger: logging.Logger,
) -> None:
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "records": [
            {
                "kategoria": record.kategoria,
                "nazwa_firmy": record.nazwa_firmy,
                "adres": record.adres,
                "data_zamkniecia": record.data_zamkniecia,
                "data_otwarcia": record.data_otwarcia,
                "typ_wpisu": record.typ_wpisu,
                "status_walidacji": record.status_walidacji,
                "brakujace_pola": record.brakujace_pola,
                "proby_ponowienia": record.proby_ponowienia,
            }
            for category_name in DATA_SHEET_NAMES
            for record in sheets.get(category_name, [])
            if record.status_walidacji != VALIDATION_STATUS_OK
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("Zapisano raport braków JSON: %s", output_path.resolve())


def record_to_excel_row(record: Record) -> list:
    return [
        record.nazwa_firmy,
        record.adres,
        record.data_zamkniecia,
        record.data_otwarcia,
        record.informacja,
        record.typ_wpisu,
        record.status_walidacji,
        record.brakujace_pola,
    ]


def collect_verification_skipped(
    sheets: dict[str, list[Record]],
) -> list[SkippedRecord]:
    """Wpisy oznaczone walidacją jako wymagające weryfikacji."""
    flagged: list[SkippedRecord] = []
    for category_name in DATA_SHEET_NAMES:
        for record in sheets.get(category_name, []):
            if record.status_walidacji != VALIDATION_STATUS_OK:
                flagged.append(
                    SkippedRecord(
                        kategoria=category_name,
                        nazwa_firmy=record.nazwa_firmy,
                        adres=record.adres,
                        data_otwarcia=record.data_otwarcia,
                        typ_wpisu=record.typ_wpisu,
                        powod=record.brakujace_pola or VALIDATION_STATUS_NEEDS_REVIEW,
                    )
                )
    return flagged


def build_harmonogram_rows(sheets: dict[str, list[Record]]) -> list[list]:
    rows: list[tuple[date, list]] = []
    for category_name in DATA_SHEET_NAMES:
        for record in sheets.get(category_name, []):
            rows.append(
                (
                    opening_date_sort_key(record.data_otwarcia),
                    [
                        category_name,
                        record.nazwa_firmy,
                        record.adres,
                        record.data_zamkniecia,
                        record.data_otwarcia,
                        record.typ_wpisu,
                        record.informacja,
                    ],
                )
            )
    rows.sort(key=lambda item: (item[0], item[1][1]))
    return [row for _, row in rows]


def build_region_rows(sheets: dict[str, list[Record]]) -> list[list]:
    rows: list[list] = []
    for category_name in DATA_SHEET_NAMES:
        for record in sheets.get(category_name, []):
            plz = extract_plz(record.adres)
            rows.append(
                [
                    plz_to_bundesland(plz),
                    plz,
                    extract_city_from_address(record.adres),
                    category_name,
                    record.nazwa_firmy,
                    record.adres,
                    record.data_zamkniecia,
                    record.data_otwarcia,
                    record.typ_wpisu,
                ]
            )
    rows.sort(key=lambda row: (row[0], row[2], row[4]))
    return rows


def write_excel(
    sheets: dict[str, list[Record]],
    skipped: list[SkippedRecord],
    output_path: Path,
    logger: logging.Logger,
) -> None:
    wb = Workbook()
    default_ws = wb.active
    wb.remove(default_ws)

    sheet_plan: list[tuple[str, tuple[str, ...], list[list]]] = []

    for sheet_name in DATA_SHEET_NAMES:
        records = sheets.get(sheet_name, [])
        sheet_plan.append(
            (
                sheet_name,
                EXCEL_COLUMNS,
                [record_to_excel_row(r) for r in records],
            )
        )

    sheet_plan.append((SHEET_SCHEDULE, HARMONOGRAM_COLUMNS, build_harmonogram_rows(sheets)))
    sheet_plan.append((SHEET_BY_REGION, REGION_COLUMNS, build_region_rows(sheets)))
    validation_rows = build_validation_report_rows(sheets)
    sheet_plan.append((SHEET_VALIDATION_REPORT, VALIDATION_REPORT_COLUMNS, validation_rows))
    sheet_plan.append(
        (
            SHEET_SKIPPED,
            SKIPPED_COLUMNS,
            [
                [
                    s.kategoria,
                    s.nazwa_firmy,
                    s.adres,
                    s.data_otwarcia,
                    s.typ_wpisu,
                    s.powod,
                ]
                for s in skipped
            ],
        )
    )

    for sheet_name, columns, rows in sheet_plan:
        ws = wb.create_sheet(title=sheet_name[:31])
        ws.append(list(columns))
        for row in rows:
            ws.append(row)
        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 40
        ws.column_dimensions["C"].width = 40
        ws.column_dimensions["D"].width = 18
        ws.column_dimensions["E"].width = 18
        ws.column_dimensions["F"].width = 18
        ws.column_dimensions["G"].width = 80

    wb.save(output_path)
    logger.info("Zapisano Excel: %s", output_path.resolve())


def run_scraper() -> None:
    logger = setup_logging()
    logger.info("=== START scrapera neueroeffnung.info ===")

    processed_records = load_processed_records(logger)
    import_processed_from_json(JSON_OUTPUT_FILE, processed_records, logger)
    prepare_fresh_output_files(logger)

    logger.info(
        "Tryb zapisu: tylko nowe rekordy (pomijanie %s już wyeksportowanych)",
        len(processed_records),
    )
    logger.info(
        "Filtr dat otwarcia: Q3 2026 (%s) – Q4 2028 (%s)",
        OPENING_FILTER_START.isoformat(),
        OPENING_FILTER_END.isoformat(),
    )

    cache = load_cache(logger)
    session = requests.Session()
    skipped: list[SkippedRecord] = []

    sheets: dict[str, list[Record]] = {}

    for sheet_name, url in CATEGORIES.items():
        logger.info("--- Kategoria: %s ---", sheet_name)
        sheets[sheet_name] = collect_category_records(
            session,
            url,
            cache,
            logger,
            category_name=sheet_name,
            skipped=skipped,
            processed=processed_records,
        )

    logger.info("--- Czyszczenie danych (lokalne) ---")
    data_only = {name: sheets[name] for name in DATA_SHEET_NAMES if name in sheets}
    cleaned_data = clean_all_sheets(data_only, logger)
    sheets.update(cleaned_data)

    logger.info("--- Twardy filtr dat otwarcia ---")
    sheets = purge_out_of_range_records(sheets, skipped, logger)

    logger.info("--- Pomijanie już wyeksportowanych rekordów ---")
    sheets = filter_already_processed_records(sheets, processed_records, skipped, logger)

    logger.info("--- Zapis JSON (po scrapingu) ---")
    save_json_dataset(sheets, JSON_OUTPUT_FILE, logger, stage="po_scrapingu")

    logger.info("--- Walidacja i ponawianie pobrania ---")
    sheets, validation_summary = run_validation_pipeline(session, sheets, cache, logger)

    logger.info("--- Twardy filtr dat otwarcia (po walidacji) ---")
    sheets = purge_out_of_range_records(sheets, skipped, logger)
    validation_summary = validate_all_records(sheets)

    logger.info("--- Google Maps: weryfikacja wszystkich rekordów ---")
    sheets = run_maps_verification_pipeline(sheets, logger)
    validation_summary = validate_all_records(sheets)
    logger.info(
        "Po Google Maps: OK=%s, wymaga weryfikacji=%s",
        validation_summary["ok"],
        validation_summary["wymaga_weryfikacji"],
    )

    save_json_dataset(
        sheets,
        JSON_OUTPUT_FILE,
        logger,
        stage="po_maps",
        validation_summary=validation_summary,
    )

    logger.info("--- Twardy filtr godzin pracy (przed Excel) ---")
    sheets = purge_records_with_working_hours(sheets, skipped, logger)
    validation_summary = validate_all_records(sheets)

    enrich_all_closing_dates(sheets)

    save_json_dataset(
        sheets,
        JSON_OUTPUT_FILE,
        logger,
        stage="po_walidacji",
        validation_summary=validation_summary,
    )
    save_validation_report_json(sheets, validation_summary, VALIDATION_REPORT_FILE, logger)

    skipped.extend(collect_verification_skipped(sheets))

    write_excel(sheets, skipped, OUTPUT_FILE, logger)

    mark_records_as_processed(sheets, processed_records)
    save_processed_records(processed_records, logger)

    logger.info("--- Wysyłka e-mail z wynikiem ---")
    try:
        import send_mail

        if send_mail.should_send_email():
            mail_info = send_mail.send_excel(OUTPUT_FILE)
            logger.info(
                "Wysłano Excel: %s → %s (kopia w %s)",
                Path(mail_info["file"]).name,
                mail_info["to"],
                mail_info["sent_folder"],
            )
        else:
            logger.info("Wysyłka e-mail pominięta (SEND_EMAIL=false)")
    except Exception as exc:
        logger.error("Nie udało się wysłać e-maila: %s", exc)

    for name in DATA_SHEET_NAMES:
        logger.info("Arkusz '%s': %s rekordów", name, len(sheets.get(name, [])))
    logger.info("Sheet '%s': %s records", SHEET_SCHEDULE, len(build_harmonogram_rows(sheets)))
    logger.info("Sheet '%s': %s records", SHEET_BY_REGION, len(build_region_rows(sheets)))
    logger.info("Sheet '%s': %s records", SHEET_VALIDATION_REPORT, len(build_validation_report_rows(sheets)))
    logger.info("Sheet '%s': %s records", SHEET_SKIPPED, len(skipped))
    logger.info(
        "Walidacja końcowa: OK=%s / %s",
        validation_summary["ok"],
        validation_summary["total"],
    )

    logger.info("=== KONIEC ===")


if __name__ == "__main__":
    run_scraper()
