"""Testy warstwy Google Maps (Playwright mockowany)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import google_maps_enricher as maps

pytestmark = pytest.mark.unit


class TestBuildSearchQuery:
    def test_joins_company_and_partial_address(self):
        assert maps.build_search_query("Lidl", "32756 Detmold") == "Lidl 32756 Detmold"


class TestScoreMapsMatch:
    def test_prefers_name_and_plz(self):
        score = maps.score_maps_match("Lidl", "32756 Detmold", "Lidl · 32756 Detmold")
        assert score >= 35


class TestNormalizeMapsAddress:
    def test_strips_address_prefix(self):
        assert maps.normalize_maps_address("Adresse: Hauptstraße 1, 32756 Detmold") == (
            "Hauptstraße 1, 32756 Detmold"
        )


class TestLooksLikeOpeningHours:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Mo-Fr 9:00-18:00", True),
            ("Montag 10:00-20:00", True),
            ("Neueröffnung im September", False),
            ("Kernsanierung ab August", False),
        ],
    )
    def test_detects_hours_patterns(self, text: str, expected: bool):
        assert maps._looks_like_opening_hours(text) is expected


class TestGoogleMapsEnricher:
    def test_verify_place_uses_cache_without_browser(self, silent_logger):
        cache = {
            "maps::Lidl::32756 Detmold": {
                "adres": "Hauptstraße 1, 32756 Detmold",
                "godziny_pracy": "Mo-Fr 9:00-18:00",
                "verified": True,
                "query": "Lidl 32756 Detmold",
                "not_found": False,
            },
        }
        enricher = maps.GoogleMapsEnricher(silent_logger, cache=cache)
        result = enricher.verify_place("Lidl", "32756 Detmold")
        assert result.adres == "Hauptstraße 1, 32756 Detmold"
        assert result.godziny_pracy == "Mo-Fr 9:00-18:00"
        assert result.verified is True

    def test_verify_place_writes_to_cache(self, silent_logger):
        cache: dict = {}
        enricher = maps.GoogleMapsEnricher(silent_logger, cache=cache)
        enricher._page = MagicMock()

        fake_result = maps.MapsPlaceResult(
            adres="Teststraße 2, 80331 München",
            godziny_pracy="Mo-Sa 10:00-22:00",
            verified=True,
        )
        with patch.object(enricher, "_lookup_place", return_value=fake_result):
            result = enricher.verify_place("McDonald's", "80331 München")

        assert result.godziny_pracy == "Mo-Sa 10:00-22:00"
        assert cache["maps::McDonald's::80331 München"]["godziny_pracy"] == "Mo-Sa 10:00-22:00"

    def test_is_enrichment_enabled_respects_env(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GOOGLE_MAPS_ENRICHMENT", "false")
        assert maps.is_enrichment_enabled() is False
        monkeypatch.setenv("ENABLE_GOOGLE_MAPS_ENRICHMENT", "true")
        assert maps.is_enrichment_enabled() is True
