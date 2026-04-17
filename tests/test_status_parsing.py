import pytest

from scraper import extract_open_status, is_closed_status


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Sklep - tymczasowo zamknięte", "Tymczasowo zamknięte"),
        ("Sklep - tymczasowo zamkniete", "Tymczasowo zamknięte"),
        ("Vorübergehend geschlossen", "Vorübergehend geschlossen"),
        ("voruebergehend geschlossen", "Vorübergehend geschlossen"),
        ("Temporarily closed", "Temporarily closed"),
        ("UNUSUAL TRAFFIC - temporarily closed", "Temporarily closed"),
        ("open now", "Open"),
        ("Geöffnet", "Geöffnet"),
        ("geoeffnet", "Geöffnet"),
        ("Closed", "Closed"),
        ("", ""),
    ],
)
def test_extract_open_status(text, expected):
    assert extract_open_status(text) == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Tymczasowo zamknięte", True),
        ("tymczasowo zamkniete", True),
        ("Vorübergehend geschlossen", True),
        ("voruebergehend geschlossen", True),
        ("Temporarily closed", True),
        ("  temporarily closed  ", True),
        ("Zamknięte na stałe", False),
        ("Geschlossen", False),
        ("Closed", False),
        ("Otwarte", False),
        ("Open", False),
        ("", False),
    ],
)
def test_is_closed_status_temporarily_only(status, expected):
    assert is_closed_status(status) == expected

