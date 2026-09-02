"""Testy jednostkowe – izolowane funkcje parsujące i filtrujące."""

from __future__ import annotations

from dataclasses import asdict

from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


class TestCleanText:
    def test_normalizes_whitespace(self):
        assert scraper.clean_text("  hello   world  ") == "hello world"

    def test_empty_string(self):
        assert scraper.clean_text("") == ""
        assert scraper.clean_text("   ") == ""


class TestExtractClosingDate:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Das Restaurant schließt am 01.08.2026 für den Umbau.", "01.08.2026"),
            ("Geschlossen ab 15.03.2026 wegen Renovierung.", "15.03.2026"),
            ("Schließung: 20.12.2025", "20.12.2025"),
            ("Umbau vom 01.07.2026 bis 30.09.2026.", "01.07.2026"),
            ("Neueröffnung ohne Schließung.", ""),
            ("", ""),
        ],
    )
    def test_closing_patterns(self, text: str, expected: str):
        assert scraper.extract_closing_date(text) == expected

    def test_enrich_closing_date_from_information(self):
        record = scraper.Record(
            nazwa_firmy="McDonald's",
            adres="Lüneburg",
            data_zamkniecia="",
            data_otwarcia="15.10.2026",
            informacja="Das Restaurant schließt am 01.08.2026 für den Umbau.",
        )
        scraper.enrich_closing_date(record)
        assert record.data_zamkniecia == "01.08.2026"


class TestExtractOpeningDate:
    def test_reads_bold_span(self):
        html = """
        <h2>Eröffnung: <span class="font-weight-bold">2. Halbjahr 2026</span></h2>
        """
        soup = BeautifulSoup(html, "html.parser")
        assert scraper.extract_opening_date(soup) == "2. Halbjahr 2026"

    def test_returns_empty_when_missing(self):
        soup = BeautifulSoup("<h2>Kontaktdaten</h2>", "html.parser")
        assert scraper.extract_opening_date(soup) == ""


class TestExtractAddress:
    def test_reads_address_after_map_marker(self, load_fixture):
        soup = BeautifulSoup(load_fixture("detail_rewe_esch.html"), "html.parser")
        assert scraper.extract_address(soup) == "Beckrather Straße 39, 41189 Mönchengladbach"

    def test_returns_empty_when_missing(self):
        soup = BeautifulSoup("<div><h2>Kontaktdaten</h2></div>", "html.parser")
        assert scraper.extract_address(soup) == ""


class TestExtractInformation:
    def test_reads_article_paragraphs(self, load_fixture):
        soup = BeautifulSoup(load_fixture("detail_rewe_esch.html"), "html.parser")
        info = scraper.extract_information(soup)
        assert "REWE Esch" in info
        assert "Kernsanierung" in info
        assert "3. September 2026" in info


class TestDerivedSheets:
    def test_format_entry_type(self):
        assert scraper.format_entry_type("reopening") == "Reopening"
        assert scraper.format_entry_type("new_opening") == "Neueröffnung"

    def test_plz_to_bundesland(self):
        assert scraper.plz_to_bundesland("80331") == "Bayern"
        assert scraper.plz_to_bundesland("10115") == "Berlin"

    def test_build_harmonogram_sorts_by_date(self):
        sheets = {
            "Markets": [
                scraper.Record("B", "Adres B", "", "2027", typ_wpisu="Neueröffnung"),
                scraper.Record("A", "Adres A", "", "03.09.2026", typ_wpisu="Reopening"),
            ],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        rows = scraper.build_harmonogram_rows(sheets)
        assert rows[0][1] == "A"
        assert rows[1][1] == "B"


class TestParseListPage:
    def test_parses_linked_entry_cards(self, load_fixture):
        html = load_fixture("list_page_linked.html")
        items = scraper.parse_list_page(html, "https://www.neueroeffnung.info/branche/supermaerkte")

        assert len(items) == 2
        assert items[0].nazwa == "REWE Esch"
        assert items[0].data_otwarcia == "03.09.2026"
        assert items[0].adres_lista == "41189 Mönchengladbach"
        assert items[0].url.endswith("/moenchengladbach/rewe-esch")
        assert items[0].entry_type == "reopening"

    def test_parses_plain_cards_without_links(self, load_fixture):
        html = load_fixture("list_page_plain_cards.html")
        items = scraper.parse_list_page(html, "https://www.neueroeffnung.info/branche/supermaerkte?page=2")

        assert len(items) == 2
        assert items[0].nazwa == "EDEKA van Dungen"
        assert items[0].url == ""
        assert items[1].nazwa == "Lidl"


class TestListingToRecordWithoutDetail:
    def test_uses_list_data_when_no_url(self, silent_logger):
        item = scraper.ListingItem(
            nazwa="Lidl",
            data_otwarcia="09.09.2026",
            adres_lista="Hauptstraße 1, 32756 Detmold",
        )
        with patch.object(scraper, "resolve_detail_url", return_value=""):
            record = scraper.listing_to_record(
                session=MagicMock(),
                item=item,
                cache={},
                logger=silent_logger,
            )
        assert record.nazwa_firmy == "Lidl"
        assert record.adres == "Hauptstraße 1, 32756 Detmold"
        assert record.data_otwarcia == "09.09.2026"
        assert record.data_zamkniecia == ""
        assert record.informacja == ""


class TestCacheHelpers:
    def test_load_cache_missing_file(self, silent_logger, tmp_path, monkeypatch):
        monkeypatch.setattr(scraper, "CACHE_FILE", tmp_path / "missing.json")
        assert scraper.load_cache(silent_logger) == {}

    def test_save_and_load_cache(self, silent_logger, tmp_path, monkeypatch):
        cache_path = tmp_path / "cache.json"
        monkeypatch.setattr(scraper, "CACHE_FILE", cache_path)

        scraper.save_cache({"url": {"adres": "Test"}}, silent_logger)
        loaded = scraper.load_cache(silent_logger)

        assert loaded == {"url": {"adres": "Test"}}
