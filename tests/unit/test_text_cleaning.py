"""Testy jednostkowe lokalnego czyszczenia danych."""

from __future__ import annotations

import pytest

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


class TestCleanTextLocal:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  REWE   Esch  ", "REWE Esch"),
            ("M&uuml;nchen", "München"),
            ("Beckrather Stra&szlig;e 39", "Beckrather Straße 39"),
            ("Caf&eacute; â€“ Bistro", "Café - Bistro"),
            ("M\u00c3\u00b6nchengladbach", "Mönchengladbach"),
            ("", ""),
        ],
    )
    def test_fixes_entities_and_mojibake(self, raw: str, expected: str):
        assert scraper.clean_text_local(raw) == expected


class TestCleanRecords:
    def test_cleans_html_entities_in_records(self, silent_logger):
        records = [
            scraper.Record("M&uuml;ller Markt", "K&ouml;ln", "", "01.09.2026"),
        ]
        cleaned = scraper.clean_records(records, silent_logger)

        assert len(cleaned) == 1
        assert cleaned[0].nazwa_firmy == "Müller Markt"
        assert cleaned[0].adres == "Köln"

    def test_clean_all_sheets_processes_every_category(self, silent_logger):
        sheets = {
            "Markets": [scraper.Record("REWE", "Stra&szlig;e 1", "", "2026")],
            "Restaurants": [scraper.Record("Caf&eacute;", "Berlin", "", "2026")],
            "Drugstores": [],
            "Shopping centers": [],
        }

        result = scraper.clean_all_sheets(sheets, silent_logger)

        assert result["Markets"][0].nazwa_firmy == "REWE"
        assert result["Restaurants"][0].nazwa_firmy == "Café"
        assert result["Drugstores"] == []
