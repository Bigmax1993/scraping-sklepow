import json

import pytest

import scraper


pytestmark = pytest.mark.integration


def test_parse_and_filter_integration_for_temporarily_closed():
    raw = "Supermarket · Musterstrasse 1, Berlin · voruebergehend geschlossen"
    category, address, status = scraper.parse_card_text(raw)

    normalized_status = scraper.extract_open_status(status)

    assert category == "Supermarket"
    assert address == "Musterstrasse 1, Berlin"
    assert normalized_status == "Vorübergehend geschlossen"
    assert scraper.is_closed_status(normalized_status) is True


def test_save_and_load_csv_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper, "OUTPUT_DIR", tmp_path)

    csv_path = tmp_path / "sample.csv"
    rows = [
        {
            "marka": "REWE",
            "nazwa": "REWE Berlin",
            "ocena": "4.1",
            "liczba_opinii": "123",
            "kategoria": "Supermarket",
            "adres": "Musterstrasse 1",
            "full_address": "Musterstrasse 1, 10115 Berlin",
            "status": "Temporarily closed",
            "telefon": "+49 111 222",
            "www": "https://example.org",
            "url": "https://maps.google.com/place/1",
            "lat_center": "",
            "lon_center": "",
            "generalny_wykonawca": "Firma A",
            "grupa_generalnego_wykonawcy": "Firma A",
            "land_niemiecki": "Berlin",
            "grupa_wykonawcy_w_landzie": "Berlin | Firma A",
        }
    ]

    scraper.save_csv(rows, csv_path)
    loaded_rows, seen_urls = scraper.load_existing_csv(csv_path, scraper.logging.getLogger("test"))

    assert loaded_rows == rows
    assert seen_urls == {"https://maps.google.com/place/1"}


def test_save_and_load_cache_roundtrip(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(scraper, "CACHE_FILE", cache_path)

    cache_data = {
        "places": {
            "https://maps.google.com/place/1": {
                "phone": "+49 111 222",
                "website": "https://example.org",
                "status": "Temporarily closed",
                "full_address": "Musterstrasse 1, 10115 Berlin",
            }
        }
    }
    logger = scraper.logging.getLogger("test")

    scraper.save_cache(cache_data, logger)
    loaded = scraper.load_cache(logger)

    assert loaded == cache_data
    # sanity-check file format written to disk
    assert json.loads(cache_path.read_text(encoding="utf-8")) == cache_data
