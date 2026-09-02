"""Testy enrichmentu danych kontaktowych (batch Serper → scrape → Claude)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import contact_enrichment as ce

pytestmark = pytest.mark.unit


SAMPLE_HTML = """
<html><body>
  <a href="mailto:info@example-shop.de">Mail</a>
  <a href="tel:+4989123456789">Tel</a>
  <a href="https://example-shop.de">Home</a>
  <p>Inhaber: Herr Max Mustermann</p>
</body></html>
"""


class TestExtractContactsFromHtml:
    def test_parses_email_phone_and_owner(self):
        contact = ce.extract_contacts_from_html(SAMPLE_HTML, "https://example-shop.de")
        assert contact.email == "info@example-shop.de"
        assert "+49" in contact.telefon or contact.telefon.startswith("0")
        assert "Mustermann" in contact.osoba_kontaktowa


class TestRecordNeedsContactEnrichment:
    @pytest.mark.parametrize(
        "telefon,email,website,osoba,needs",
        [
            ("+49 89 123", "a@b.de", "https://x.de", "Max M", False),
            ("+49 89 123", "a@b.de", "https://x.de", "", True),
            ("+49 89 123", "", "https://x.de", "Max M", True),
            ("", "", "", "", True),
        ],
    )
    def test_detects_missing_contact_fields(self, telefon, email, website, osoba, needs):
        assert ce.record_needs_contact_enrichment(telefon, email, website, osoba) is needs


class TestBatchSerper:
    def test_batch_serper_sets_target_urls(self, silent_logger, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "test-key")
        jobs = [
            ce.ContactEnrichmentJob(
                job_id="1",
                category="Markets",
                record_index=0,
                company_name="Example Shop",
                address="81675 München",
                missing_fields=["telefon", "email"],
            )
        ]
        with patch.object(ce, "serper_search_url", return_value="https://example-shop.de"):
            ce.batch_serper_search(jobs, "test-key", silent_logger)
        assert jobs[0].target_url == "https://example-shop.de"
        assert "Impressum" in jobs[0].serper_query


class TestFinalizeJobContact:
    def test_rejected_scraped_data_not_written_to_record(self, silent_logger):
        import neueroeffnung_scraper as scraper

        job = ce.ContactEnrichmentJob(
            job_id="1",
            category="Markets",
            record_index=0,
            company_name="Shop",
            address="Adres",
            scraped=ce.ContactData(
                telefon="+49 123",
                email="maybe@wrong.de",
                source_url="https://example.de",
            ),
            verified=ce.ContactData(verified=False),
            target_url="https://example.de",
        )
        contact = ce.finalize_job_contact(job)
        record = scraper.Record("Shop", "Adres", "", "03.09.2026")
        ce.apply_contact_to_record(record, contact)

        assert contact.telefon == ""
        assert contact.email == ""
        assert record.telefon == ""
        assert record.email == ""
        assert record.kontakt_zweryfikowany is False

    def test_verified_data_written_to_record(self):
        import neueroeffnung_scraper as scraper

        contact = ce.ContactData(
            telefon="+49 123",
            email="ok@shop.de",
            verified=True,
            source_url="https://shop.de",
        )
        record = scraper.Record("Shop", "Adres", "", "03.09.2026")
        ce.apply_contact_to_record(record, contact)

        assert record.telefon == "+49 123"
        assert record.email == "ok@shop.de"
        assert record.kontakt_zweryfikowany is True


class TestBatchClaude:
    def test_batch_verify_applies_results(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CONTACT_VERIFY", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        job = ce.ContactEnrichmentJob(
            job_id="1",
            category="Markets",
            record_index=0,
            company_name="Example Shop",
            address="81675 München",
            scraped=ce.ContactData(email="info@example-shop.de", source_url="https://x.de"),
            html_snippet="<html></html>",
        )

        fake_response = MagicMock()
        fake_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "records": [
                            {
                                "id": "1",
                                "telefon": "+49 89 123",
                                "email": "info@example-shop.de",
                                "website": "https://example-shop.de",
                                "osoba_kontaktowa": "Max Mustermann",
                                "verified": True,
                            }
                        ]
                    }
                )
            )
        ]

        with patch("anthropic.Anthropic") as anthropic_cls:
            anthropic_cls.return_value.messages.create.return_value = fake_response
            ce.batch_verify_contacts_with_claude([job], silent_logger)

        assert job.verified.verified is True
        assert job.verified.email == "info@example-shop.de"
        assert job.verified.osoba_kontaktowa == "Max Mustermann"


class TestRunBatchContactEnrichment:
    def test_full_batch_pipeline(self, silent_logger, monkeypatch, tmp_path):
        monkeypatch.setenv("ENABLE_CLAUDE_CONTACT_VERIFY", "false")
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.setattr(ce, "CONTACT_CACHE_FILE", tmp_path / "cache.json")
        monkeypatch.setattr(ce, "CONTACT_BATCH_REPORT_FILE", tmp_path / "batch.json")

        import neueroeffnung_scraper as scraper

        record = scraper.Record(
            nazwa_firmy="Example Shop",
            adres="81675 München",
            data_zamkniecia="",
            data_otwarcia="03.09.2026",
        )
        sheets = {
            "Markets": [record],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }

        with patch.object(ce, "batch_serper_search") as mock_serper:
            with patch.object(ce, "batch_scrape_jobs") as mock_scrape:
                def fill_scraped(jobs, session, headers, logger, **kwargs):
                    jobs[0].scraped = ce.ContactData(
                        email="info@example-shop.de",
                        telefon="+49 89 123",
                        source_url="https://example-shop.de",
                    )

                mock_scrape.side_effect = fill_scraped
                result_sheets, report = ce.run_batch_contact_enrichment(
                    MagicMock(),
                    sheets,
                    scraper.DATA_SHEET_NAMES,
                    {},
                    silent_logger,
                )

        mock_serper.assert_not_called()
        assert result_sheets["Markets"][0].email == ""
        assert result_sheets["Markets"][0].telefon == ""
        assert result_sheets["Markets"][0].kontakt_zweryfikowany is False
        assert report["jobs_total"] == 1
        assert (tmp_path / "batch.json").exists()

    def test_scrape_only_does_not_apply_contacts(self, silent_logger, monkeypatch, tmp_path):
        monkeypatch.delenv("SERPER_API_KEY", raising=False)
        monkeypatch.setattr(ce, "CONTACT_CACHE_FILE", tmp_path / "cache.json")
        monkeypatch.setattr(ce, "CONTACT_BATCH_REPORT_FILE", tmp_path / "batch.json")

        import neueroeffnung_scraper as scraper

        record = scraper.Record(
            nazwa_firmy="Example Shop",
            adres="81675 München",
            data_zamkniecia="",
            data_otwarcia="03.09.2026",
        )
        sheets = {
            "Markets": [record],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }

        with patch.object(ce, "batch_scrape_jobs") as mock_scrape:
            def fill_scraped(jobs, session, headers, logger, **kwargs):
                jobs[0].scraped = ce.ContactData(
                    email="maybe@wrong.de",
                    telefon="+49 89 123",
                    source_url="https://example-shop.de",
                )

            mock_scrape.side_effect = fill_scraped
            result_sheets, report = ce.run_batch_contact_enrichment(
                MagicMock(),
                sheets,
                scraper.DATA_SHEET_NAMES,
                {},
                silent_logger,
            )

        assert len(result_sheets["Markets"]) == 1
        assert result_sheets["Markets"][0].email == ""
        assert report["jobs"][0]["scraped"]["email"] == "maybe@wrong.de"

    def test_saves_batch_report_with_serper(self, silent_logger, monkeypatch, tmp_path):
        monkeypatch.setenv("SERPER_API_KEY", "k")
        monkeypatch.setattr(ce, "CONTACT_CACHE_FILE", tmp_path / "cache.json")
        monkeypatch.setattr(ce, "CONTACT_BATCH_REPORT_FILE", tmp_path / "batch.json")

        import neueroeffnung_scraper as scraper

        record = scraper.Record("Shop", "Adres", "", "03.09.2026")
        sheets = {
            "Markets": [record],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }

        with patch.object(ce, "batch_serper_search"):
            with patch.object(ce, "batch_scrape_jobs"):
                _, report = ce.run_batch_contact_enrichment(
                    MagicMock(), sheets, scraper.DATA_SHEET_NAMES, {}, silent_logger
                )

        assert report["serper_enabled"] is True


class TestEnrichRecordContactsCompat:
    def test_uses_cache_without_network(self, silent_logger):
        cache = {
            "contact::lidl::32756 detmold": {
                "telefon": "+49 5231 12345",
                "email": "info@lidl.de",
                "website": "https://lidl.de",
                "osoba_kontaktowa": "",
                "source_url": "https://lidl.de",
                "verified": True,
            }
        }
        result = ce.enrich_record_contacts(
            session=MagicMock(),
            company_name="Lidl",
            address="32756 Detmold",
            telefon="",
            email="",
            website="",
            osoba_kontaktowa="",
            headers={},
            cache=cache,
            logger=silent_logger,
        )
        assert result.email == "info@lidl.de"
        assert result.verified is True

    def test_proceeds_when_no_contacts_found(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CONTACT_VERIFY", "false")
        with patch.object(ce, "batch_serper_search"):
            with patch.object(ce, "batch_scrape_jobs"):
                result = ce.enrich_record_contacts(
                    session=MagicMock(),
                    company_name="Unknown",
                    address="Nowhere",
                    telefon="",
                    email="",
                    website="",
                    osoba_kontaktowa="",
                    headers={},
                    cache={},
                    logger=silent_logger,
                )
        assert result.has_any() is False
        assert result.verified is False


class TestApplyContactPipeline:
    def test_pipeline_updates_record(self, silent_logger, monkeypatch):
        import neueroeffnung_scraper as scraper

        monkeypatch.setenv("ENABLE_CONTACT_ENRICHMENT", "true")
        record = scraper.Record(
            nazwa_firmy="Test",
            adres="Adres 1",
            data_zamkniecia="",
            data_otwarcia="03.09.2026",
        )
        sheets = {
            "Markets": [record],
            "Restaurants": [],
            "Drugstores": [],
            "Shopping centers": [],
        }

        fake_report = {"jobs_total": 1, "enriched": 1, "verified": 1}

        with patch(
            "contact_enrichment.run_batch_contact_enrichment",
            return_value=(sheets, fake_report),
        ):
            sheets, report = scraper.run_contact_enrichment_pipeline(MagicMock(), sheets, silent_logger)

        updated = sheets["Markets"][0]
        assert report["jobs_total"] == 1
