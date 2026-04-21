from unittest.mock import Mock

import pytest

import scraper


pytestmark = pytest.mark.regression


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Temporarily closed · Closed", "Temporarily closed"),
        ("voruebergehend geschlossen i obecnie zamkniete", "Vorübergehend geschlossen"),
    ],
)
def test_regression_temporary_closed_has_priority(text, expected):
    # Regression: "temporary closed" must not be downgraded to generic "closed".
    assert scraper.extract_open_status(text) == expected
    assert scraper.is_closed_status(expected) is True


def test_regression_cache_without_places_key_is_repaired(tmp_path, monkeypatch):
    # Regression: corrupted cache file without "places" key should still load safely.
    broken_cache = tmp_path / "cache.json"
    broken_cache.write_text('{"unexpected": 1}', encoding="utf-8")
    monkeypatch.setattr(scraper, "CACHE_FILE", broken_cache)

    loaded = scraper.load_cache(Mock())

    assert "places" in loaded
    assert loaded["places"] == {}


def test_regression_get_place_details_uses_cache_without_driver_calls():
    url = "https://maps.google.com/place/1"
    cache = {
        "places": {
            url: {
                "phone": "+49 111 222",
                "website": "https://example.org",
                "status": "Temporarily closed",
                "full_address": "Musterstrasse 1, 10115 Berlin",
            }
        }
    }
    driver = Mock()
    logger = Mock()

    phone, website, status, full_address = scraper.get_place_details_with_cache(
        driver=driver, url=url, cache=cache, logger=logger
    )

    assert phone == "+49 111 222"
    assert website == "https://example.org"
    assert status == "Temporarily closed"
    assert full_address == "Musterstrasse 1, 10115 Berlin"
    driver.execute_script.assert_not_called()
