"""Testy enrichmentu danych kontaktowych."""

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
        "telefon,email,website,needs",
        [
            ("+49 89 123", "a@b.de", "https://x.de", False),
            ("+49 89 123", "", "https://x.de", True),
            ("", "a@b.de", "https://x.de", True),
            ("", "", "", True),
        ],
    )
    def test_detects_missing_contact_fields(self, telefon, email, website, needs):
        assert ce.record_needs_contact_enrichment(telefon, email, website, "") is needs


class TestEnrichRecordContacts:
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

    def test_scrape_and_claude_verify(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CONTACT_VERIFY", "false")
        session = MagicMock()
        with patch.object(ce, "search_web_url", return_value="https://example-shop.de"):
            with patch.object(ce, "fetch_page_html", return_value=SAMPLE_HTML):
                result = ce.enrich_record_contacts(
                    session=session,
                    company_name="Example Shop",
                    address="81675 München",
                    telefon="",
                    email="",
                    website="",
                    osoba_kontaktowa="",
                    headers={},
                    cache={},
                    logger=silent_logger,
                )
        assert result.email == "info@example-shop.de"
        assert result.verified is True

    def test_claude_html_fallback(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CONTACT_VERIFY", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        def fake_claude(company, address, contact, html, logger, *, html_only=False):
            if html_only:
                return ce.ContactData(
                    email="kontakt@firma.de",
                    telefon="+49 89 111",
                    website="https://firma.de",
                    verified=True,
                    source_url=contact.source_url,
                )
            return ce.ContactData(verified=False)

        with patch.object(ce, "search_web_url", return_value="https://firma.de"):
            with patch.object(ce, "fetch_page_html", return_value="<html></html>"):
                with patch.object(ce, "verify_contacts_with_claude", side_effect=fake_claude):
                    result = ce.enrich_record_contacts(
                        session=MagicMock(),
                        company_name="Firma",
                        address="München",
                        telefon="",
                        email="",
                        website="",
                        osoba_kontaktowa="",
                        headers={},
                        cache={},
                        logger=silent_logger,
                    )
        assert result.email == "kontakt@firma.de"
        assert result.verified is True

    def test_proceeds_when_no_contacts_found(self, silent_logger, monkeypatch):
        monkeypatch.setenv("ENABLE_CLAUDE_CONTACT_VERIFY", "false")
        with patch.object(ce, "search_web_url", return_value=""):
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

        fake_contact = ce.ContactData(
            telefon="+49 123",
            email="test@shop.de",
            website="https://shop.de",
            verified=True,
            source_url="https://shop.de",
        )

        with patch("contact_enrichment.enrich_record_contacts", return_value=fake_contact):
            with patch("contact_enrichment.load_contact_cache", return_value={}):
                with patch("contact_enrichment.save_contact_cache"):
                    result = scraper.run_contact_enrichment_pipeline(MagicMock(), sheets, silent_logger)

        updated = result["Markets"][0]
        assert updated.email == "test@shop.de"
        assert updated.kontakt_zweryfikowany is True
