"""Testy pomijania rekordów już wyeksportowanych w poprzednich runach."""

from __future__ import annotations

import json

import pytest

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


class TestRecordFingerprint:
    def test_uses_listing_address_when_available(self):
        record = scraper.Record(
            nazwa_firmy="Lidl",
            adres="Hauptstraße 1, 32756 Detmold",
            data_zamkniecia="",
            data_otwarcia="09.09.2026",
            listing_adres_lista="32756 Detmold",
        )
        item = scraper.ListingItem(
            nazwa="Lidl",
            data_otwarcia="09.09.2026",
            adres_lista="32756 Detmold",
        )
        assert scraper.record_fingerprint(record) == scraper.listing_fingerprint(item)


class TestProcessedRegistry:
    def test_import_from_previous_json(self, silent_logger, tmp_path):
        json_path = tmp_path / "wynik.json"
        payload = {
            "sheets": {
                "Markets": [
                    {
                        "nazwa_firmy": "REWE",
                        "adres": "Ulica 1",
                        "data_zamkniecia": "",
                        "data_otwarcia": "03.09.2026",
                        "informacja": "Opis",
                        "listing_adres_lista": "41189 MG",
                    }
                ],
                "Restaurants": [],
                "Drugstores": [],
                "Shopping centers": [],
            }
        }
        json_path.write_text(json.dumps(payload), encoding="utf-8")

        processed: set[str] = set()
        scraper.import_processed_from_json(json_path, processed, silent_logger)

        assert len(processed) == 1
        assert "rewe|41189 mg|03.09.2026" in processed

    def test_filter_removes_already_processed(self, silent_logger):
        fp = "lidl|32756 detmold|09.09.2026"
        processed = {fp}
        sheets = {
            "Markets": [
                scraper.Record("Lidl", "Adres", "", "09.09.2026", listing_adres_lista="32756 Detmold"),
                scraper.Record("Aldi", "Adres 2", "", "10.09.2026", listing_adres_lista="12345 Berlin"),
            ],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        skipped: list[scraper.SkippedRecord] = []
        result = scraper.filter_already_processed_records(sheets, processed, skipped, silent_logger)

        assert len(result["Markets"]) == 1
        assert result["Markets"][0].nazwa_firmy == "Aldi"
        assert skipped[0].powod == scraper.SKIP_REASON_ALREADY_EXPORTED

    def test_save_and_load_roundtrip(self, silent_logger, tmp_path, monkeypatch):
        monkeypatch.setattr(scraper, "PROCESSED_RECORDS_FILE", tmp_path / "processed.json")
        processed = {"a|b|c"}
        scraper.save_processed_records(processed, silent_logger)
        loaded = scraper.load_processed_records(silent_logger)
        assert loaded == processed
