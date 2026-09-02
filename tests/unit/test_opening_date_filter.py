"""Testy filtra dat otwarcia Q3 2026 – Q4 2028."""

from __future__ import annotations

from datetime import date

import pytest

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


class TestParseOpeningDateToRange:
    @pytest.mark.parametrize(
        "text,expected_start,expected_end",
        [
            ("03.09.2026", date(2026, 9, 3), date(2026, 9, 3)),
            ("2. Halbjahr 2026", date(2026, 7, 1), date(2026, 12, 31)),
            ("1. Halbjahr 2026", date(2026, 1, 1), date(2026, 6, 30)),
            ("1. Quartal 2027", date(2027, 1, 1), date(2027, 3, 31)),
            ("4. Quartal 2028", date(2028, 10, 1), date(2028, 12, 31)),
            ("August 2026", date(2026, 8, 1), date(2026, 8, 31)),
            ("2027", date(2027, 1, 1), date(2027, 12, 31)),
        ],
    )
    def test_parses_german_date_formats(self, text, expected_start, expected_end):
        result = scraper.parse_opening_date_to_range(text)
        assert result == (expected_start, expected_end)


class TestIsOpeningDateInRange:
    @pytest.mark.parametrize(
        "text,in_range",
        [
            ("03.09.2026", True),
            ("2. Halbjahr 2026", True),
            ("4. Quartal 2028", True),
            ("Januar 2028", True),
            ("2027", True),
            ("1. Halbjahr 2026", False),
            ("30.06.2026", False),
            ("1. Quartal 2026", False),
            ("2029", False),
            ("1. Quartal 2029", False),
            ("", False),
        ],
    )
    def test_filters_q3_2026_to_q4_2028(self, text: str, in_range: bool):
        assert scraper.is_opening_date_in_range(text) is in_range


class TestFilterRecordsByOpeningDate:
    def test_keeps_only_matching_records(self, silent_logger):
        records = [
            scraper.Record("A", "Adres", "", "03.09.2026"),
            scraper.Record("B", "Adres", "", "2029"),
            scraper.Record("C", "Adres", "", "2. Halbjahr 2027"),
        ]
        filtered = scraper.filter_records_by_opening_date(records, silent_logger)
        assert len(filtered) == 2
        assert filtered[0].nazwa_firmy == "A"
        assert filtered[1].nazwa_firmy == "C"
