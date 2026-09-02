"""Testy walidacji JSON, ponawiania pobrania i raportu braków."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


def _sample_record(**overrides) -> scraper.Record:
    base = scraper.Record(
        nazwa_firmy="REWE Esch",
        adres="Beckrather Straße 39, 41189 Mönchengladbach",
        data_zamkniecia="",
        data_otwarcia="03.09.2026",
        informacja="Opis sklepu.",
        typ_wpisu="Reopening",
        kategoria="Markets",
        detail_url="https://example.com/rewe",
        listing_adres_lista="41189 Mönchengladbach",
        entry_type_raw="reopening",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class TestFindMissingFields:
    def test_complete_record_has_no_missing_fields(self):
        assert scraper.find_missing_fields(_sample_record()) == []

    def test_detects_incomplete_address(self):
        missing = scraper.find_missing_fields(_sample_record(adres="80331 München"))
        assert "address (incomplete)" in missing

    def test_detects_missing_information(self):
        missing = scraper.find_missing_fields(_sample_record(informacja=""))
        assert "information" in missing


class TestValidationPipeline:
    def test_marks_incomplete_records_for_review(self):
        sheets = {
            "Markets": [_sample_record(informacja="")],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        summary = scraper.validate_all_records(sheets)
        assert summary["wymaga_weryfikacji"] == 1
        assert sheets["Markets"][0].status_walidacji == scraper.VALIDATION_STATUS_NEEDS_REVIEW

    def test_retry_refreshes_record_from_listing(self, silent_logger, tmp_path):
        record = _sample_record(informacja="", status_walidacji=scraper.VALIDATION_STATUS_NEEDS_REVIEW)
        refreshed = _sample_record(informacja="Uzupełniony opis.")

        with patch.object(scraper, "listing_to_record", return_value=refreshed) as mock_listing:
            result = scraper.retry_record(MagicMock(), record, {}, silent_logger)

        mock_listing.assert_called_once()
        assert result.informacja == "Uzupełniony opis."
        assert result.proby_ponowienia == 1
        assert result.status_walidacji == scraper.VALIDATION_STATUS_OK

    def test_run_validation_pipeline_retries_then_marks_remaining(
        self, silent_logger, tmp_path
    ):
        incomplete = _sample_record(informacja="")
        sheets = {
            "Markets": [incomplete],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }

        with patch.object(
            scraper,
            "retry_record",
            side_effect=lambda *_args, **_kwargs: _sample_record(informacja=""),
        ):
            _, summary = scraper.run_validation_pipeline(MagicMock(), sheets, {}, silent_logger)

        assert summary["wymaga_weryfikacji"] == 1
        assert sheets["Markets"][0].status_walidacji == scraper.VALIDATION_STATUS_NEEDS_REVIEW


class TestJsonDataset:
    def test_save_and_load_json_roundtrip(self, silent_logger, tmp_path):
        path = tmp_path / "dataset.json"
        sheets = {
            "Markets": [_sample_record()],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        scraper.save_json_dataset(sheets, path, silent_logger, stage="test")
        loaded = scraper.load_json_dataset(path, silent_logger)
        assert loaded["Markets"][0].nazwa_firmy == "REWE Esch"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["stage"] == "test"

    def test_build_validation_report_rows_only_incomplete(self):
        sheets = {
            "Markets": [
                _sample_record(),
                _sample_record(nazwa_firmy="Brak opisu", informacja="", proby_ponowienia=2),
            ],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        scraper.validate_all_records(sheets)
        rows = scraper.build_validation_report_rows(sheets)
        assert len(rows) == 1
        assert rows[0][1] == "Brak opisu"
        assert rows[0][8] == 2


class TestMapsEnrichmentPipeline:
    def test_verifies_all_records_via_maps(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_GOOGLE_MAPS_ENRICHMENT", "true")
        complete = _sample_record(
            adres="Beckrather Straße 39, 41189 Mönchengladbach",
            status_walidacji=scraper.VALIDATION_STATUS_OK,
        )
        incomplete = _sample_record(
            nazwa_firmy="Lidl",
            adres="32756 Detmold",
            listing_adres_lista="32756 Detmold",
            status_walidacji=scraper.VALIDATION_STATUS_NEEDS_REVIEW,
            brakujace_pola="address (incomplete)",
        )
        sheets = {
            "Markets": [complete, incomplete],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }

        from google_maps_enricher import MapsPlaceResult

        def fake_verify(company_name, partial_address):
            if company_name == "Lidl":
                return MapsPlaceResult(
                    adres="Hauptstraße 1, 32756 Detmold",
                    verified=True,
                )
            return MapsPlaceResult(
                adres="Beckrather Straße 39, 41189 Mönchengladbach",
                verified=True,
            )

        class FakeEnricherWithVerify:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            verify_place = staticmethod(fake_verify)

        with patch("google_maps_enricher.GoogleMapsEnricher", FakeEnricherWithVerify):
            with patch("google_maps_enricher.load_maps_cache", return_value={}):
                with patch("google_maps_enricher.save_maps_cache"):
                    sheets, _ = scraper.run_maps_verification_pipeline(sheets, silent_logger)

        assert sheets["Markets"][0].maps_zweryfikowany is True
        assert sheets["Markets"][1].adres == "Hauptstraße 1, 32756 Detmold"
        assert sheets["Markets"][1].maps_zweryfikowany is True

    def test_skips_when_disabled(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_GOOGLE_MAPS_ENRICHMENT", "false")
        sheets = {
            "Markets": [_sample_record(adres="80331 München", listing_adres_lista="80331 München")],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        result = scraper.run_maps_enrichment_pipeline(sheets, silent_logger)
        assert result["Markets"][0].adres == "80331 München"
