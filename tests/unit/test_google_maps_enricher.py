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


class TestGoogleMapsEnricher:
    def test_uses_cache_without_browser(self, silent_logger):
        cache = {
            "maps::Lidl::32756 Detmold": {"adres": "Hauptstraße 1, 32756 Detmold", "not_found": False},
        }
        enricher = maps.GoogleMapsEnricher(silent_logger, cache=cache)
        result = enricher.resolve_address("Lidl", "32756 Detmold")
        assert result == "Hauptstraße 1, 32756 Detmold"

    def test_lookup_writes_to_cache(self, silent_logger):
        cache: dict = {}
        enricher = maps.GoogleMapsEnricher(silent_logger, cache=cache)
        enricher._page = MagicMock()

        with patch.object(enricher, "_lookup_address", return_value="Teststraße 2, 80331 München"):
            result = enricher.resolve_address("McDonald's", "80331 München")

        assert result == "Teststraße 2, 80331 München"
        assert cache["maps::McDonald's::80331 München"]["adres"] == "Teststraße 2, 80331 München"

    def test_is_enrichment_enabled_respects_env(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GOOGLE_MAPS_ENRICHMENT", "false")
        assert maps.is_enrichment_enabled() is False
        monkeypatch.setenv("ENABLE_GOOGLE_MAPS_ENRICHMENT", "true")
        assert maps.is_enrichment_enabled() is True
