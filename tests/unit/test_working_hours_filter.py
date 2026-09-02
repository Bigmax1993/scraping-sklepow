"""Testy twardej reguły godzin pracy przed eksportem do Excel."""

from __future__ import annotations

import pytest

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


class TestContainsWorkingHours:
    @pytest.mark.parametrize(
        "field,value,expected",
        [
            ("godziny_pracy", "Mo-Fr 9:00-18:00", True),
            ("informacja", "Öffnungszeiten: Mo-Sa 10-20", True),
            ("informacja", "Neueröffnung nach Kernsanierung", False),
            ("adres", "Hauptstraße 1, 32756 Detmold", False),
        ],
    )
    def test_detects_working_hours_in_record_fields(self, field, value, expected):
        record = scraper.Record(
            nazwa_firmy="Test",
            adres="Adres",
            data_zamkniecia="",
            data_otwarcia="03.09.2026",
            informacja="Opis sklepu.",
        )
        setattr(record, field, value)
        assert scraper.contains_working_hours(record) is expected


class TestPurgeRecordsWithWorkingHours:
    def test_removes_records_with_hours_before_excel(self, silent_logger):
        sheets = {
            "Markets": [
                scraper.Record(
                    "Bez godzin",
                    "Adres",
                    "",
                    "03.09.2026",
                    kategoria="Markets",
                ),
                scraper.Record(
                    "Z godzinami",
                    "Adres",
                    "",
                    "03.09.2026",
                    kategoria="Markets",
                    godziny_pracy="Mo-Fr 9:00-18:00",
                ),
            ],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        skipped: list[scraper.SkippedRecord] = []
        result = scraper.purge_records_with_working_hours(sheets, skipped, silent_logger)

        assert len(result["Markets"]) == 1
        assert result["Markets"][0].nazwa_firmy == "Bez godzin"
        assert len(skipped) == 1
        assert skipped[0].powod == scraper.SKIP_REASON_WORKING_HOURS
