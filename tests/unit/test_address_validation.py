"""Testy walidacji adresów i uzupełniania brakujących ulic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import neueroeffnung_scraper as scraper

pytestmark = pytest.mark.unit


class TestIsIncompleteAddress:
    @pytest.mark.parametrize(
        "adres,incomplete",
        [
            ("80331 München", True),
            ("49637 Samtgemeinde Artland", True),
            ("01237 Dresden", True),
            ("Beckrather Straße 39, 41189 Mönchengladbach", False),
            ("Hauptstraße 24, 23738 Damlos", False),
            ("", True),
        ],
    )
    def test_detects_incomplete_addresses(self, adres: str, incomplete: bool):
        assert scraper.is_incomplete_address(adres) is incomplete


class TestValidateAddress:
    def test_trims_to_650_chars(self, silent_logger):
        long_street = "A" * 700
        result = scraper.validate_address(long_street, silent_logger, "Test")
        assert len(result) == scraper.MAX_ADDRESS_LENGTH

    def test_keeps_valid_short_address(self, silent_logger):
        adres = "Beckrather Straße 39, 41189 Mönchengladbach"
        assert scraper.validate_address(adres, silent_logger) == adres

    def test_max_length_constant_is_650(self):
        assert scraper.MAX_ADDRESS_LENGTH == 650


class TestScoreSearchMatch:
    def test_prefers_exact_name_and_plz_match(self):
        score = scraper.score_search_match(
            "Lidl",
            "32756 Detmold",
            "Lidl",
            "32756 Detmold",
        )
        assert score >= 35


class TestResolveDetailUrl:
    def test_finds_detail_url_via_search(self, load_fixture, silent_logger, monkeypatch):
        item = scraper.ListingItem(
            nazwa="Lidl",
            data_otwarcia="09.09.2026",
            adres_lista="32756 Detmold",
        )
        cache = {}

        with patch.object(
            scraper,
            "fetch_html",
            return_value=load_fixture("search_result_lidl.html"),
        ):
            url = scraper.resolve_detail_url(MagicMock(), item, cache, silent_logger)

        assert url.endswith("/detmold/lidl_5379")

    def test_listing_to_record_fetches_full_address_when_incomplete(
        self, load_fixture, silent_logger, monkeypatch
    ):
        item = scraper.ListingItem(
            nazwa="Lidl",
            data_otwarcia="09.09.2026",
            adres_lista="32756 Detmold",
            url="",
        )

        def fake_fetch_html(session, url, logger):
            if "/suche/" in url:
                return load_fixture("search_result_lidl.html")
            return load_fixture("detail_rewe_esch.html")

        with patch.object(scraper, "fetch_html", side_effect=fake_fetch_html):
            record = scraper.listing_to_record(MagicMock(), item, {}, silent_logger)

        assert scraper.is_incomplete_address(record.adres) is False
        assert "Straße" in record.adres or "41189" in record.adres
        assert "Kernsanierung" in record.informacja
