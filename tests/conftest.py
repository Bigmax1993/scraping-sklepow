from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class MockHttpResponse:
    """Mock odpowiedzi requests.get()."""

    def __init__(self, text: str, status_code: int = 200, encoding: str = "utf-8"):
        self.text = text
        self.status_code = status_code
        self.encoding = encoding
        self.apparent_encoding = encoding

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def load_fixture(fixtures_dir: Path):
    def _load(name: str) -> str:
        return (fixtures_dir / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def load_json_fixture(fixtures_dir: Path):
    def _load(name: str):
        return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def silent_logger() -> logging.Logger:
    logger = logging.getLogger("test-neueroeffnung")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def scraper_test_env(monkeypatch, tmp_path):
    """Wspólna konfiguracja testów integracyjnych scrapera."""
    import neueroeffnung_scraper as scraper

    monkeypatch.setattr(scraper, "REQUEST_DELAY_SEC", 0)
    monkeypatch.setattr(scraper, "MAX_PAGES_PER_CATEGORY", 1)
    monkeypatch.setattr(scraper, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(scraper, "LOG_FILE", tmp_path / "test.log")
    return scraper


def build_mock_session(
    route_map: list[tuple[str, str]],
    search_map: dict[str, str] | None = None,
) -> MagicMock:
    """Buduje mock sesji HTTP z mapą URL -> HTML i opcjonalną mapą wyszukiwań."""
    session = MagicMock()
    search_map = search_map or {}

    def fake_get(url, **kwargs):
        if "/suche/" in url and search_map:
            decoded = url.replace("+", " ").lower()
            for needle, html in search_map.items():
                if needle.lower() in decoded:
                    return MockHttpResponse(html)
        for key, html in route_map:
            if key in url:
                return MockHttpResponse(html)
        raise AssertionError(f"Nieoczekiwany URL: {url}")

    session.get.side_effect = fake_get
    return session
