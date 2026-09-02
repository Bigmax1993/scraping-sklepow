"""Testy Claude — filtr rekordów i spójny wpis JSON."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import claude_record_normalizer as crn
import contact_enrichment as ce

pytestmark = pytest.mark.unit


class TestClaudeRecordNormalizer:
    def test_rejected_record_removed_from_sheets(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_RECORD_NORMALIZE", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        import neueroeffnung_scraper as scraper

        record = scraper.Record(
            nazwa_firmy="Bad Entry",
            adres="Adres 1",
            data_zamkniecia="",
            data_otwarcia="03.09.2026",
            informacja="UPDATE #1 noise Mo-Sa 08:00-20:00",
        )
        sheets = {
            "Markets": [record],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        skipped: list[scraper.SkippedRecord] = []

        def fake_batch(jobs, logger, **kwargs):
            jobs[0].accept = False
            jobs[0].reject_reason = "Nie handel"
            jobs[0].claude_processed = True
            return False

        with patch.object(crn, "batch_normalize_records_with_claude", side_effect=fake_batch):
            result, report = crn.run_claude_record_normalization(
                sheets,
                scraper.DATA_SHEET_NAMES,
                skipped,
                scraper.SKIP_REASON_CLAUDE_REJECT,
                scraper.SkippedRecord,
                silent_logger,
            )

        assert result["Markets"] == []
        assert len(skipped) == 1
        assert report["rejected"] == 1

    def test_accepted_record_gets_unified_informacja(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_RECORD_NORMALIZE", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        import neueroeffnung_scraper as scraper

        record = scraper.Record(
            nazwa_firmy="REWE",
            adres="Ulica 1, 12345 Berlin",
            data_zamkniecia="",
            data_otwarcia="03.09.2026",
            informacja="UPDATE #1 old UPDATE #2 newer",
        )
        sheets = {
            "Markets": [record],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }
        skipped: list[scraper.SkippedRecord] = []

        def fake_batch(jobs, logger, **kwargs):
            jobs[0].accept = True
            jobs[0].informacja = "Der Markt REWE in Berlin eröffnet am 3. September 2026 nach Umbau."
            jobs[0].data_otwarcia = "03.09.2026"
            jobs[0].contacts_verified = True
            jobs[0].verified_contact = ce.ContactData(
                email="info@rewe.de",
                verified=True,
            )
            jobs[0].claude_processed = True
            return False

        with patch.object(crn, "batch_normalize_records_with_claude", side_effect=fake_batch):
            with patch("contact_enrichment.save_contact_cache"):
                with patch("contact_enrichment.load_contact_cache", return_value={}):
                    result, report = crn.run_claude_record_normalization(
                        sheets,
                        scraper.DATA_SHEET_NAMES,
                        skipped,
                        scraper.SKIP_REASON_CLAUDE_REJECT,
                        scraper.SkippedRecord,
                        silent_logger,
                    )

        assert len(result["Markets"]) == 1
        assert "UPDATE" not in result["Markets"][0].informacja
        assert result["Markets"][0].email == "info@rewe.de"
        assert result["Markets"][0].claude_zweryfikowany is True
        assert report["accepted"] == 1

    def test_batch_claude_parses_response(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_RECORD_NORMALIZE", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

        job = crn.RecordNormalizationJob(
            job_id="1",
            category="Markets",
            record_index=0,
            company_name="Shop",
            address="Adres",
            opening_date="03.09.2026",
            closing_date="",
            entry_type="Neueröffnung",
            information_raw="UPDATE #1 text",
        )
        fake_response = MagicMock()
        fake_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "records": [
                            {
                                "id": "1",
                                "accept": True,
                                "reject_reason": "",
                                "informacja": "Ein neuer Shop eröffnet bald.",
                                "data_otwarcia": "03.09.2026",
                                "data_zamkniecia": "",
                                "telefon": "",
                                "email": "",
                                "website": "",
                                "osoba_kontaktowa": "",
                                "contacts_verified": False,
                            }
                        ]
                    }
                )
            )
        ]
        with patch("anthropic.Anthropic") as anthropic_cls:
            anthropic_cls.return_value.messages.create.return_value = fake_response
            crn.batch_normalize_records_with_claude([job], silent_logger)

        assert job.accept is True
        assert job.informacja == "Ein neuer Shop eröffnet bald."
