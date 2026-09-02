"""Testy na żywo kluczy API (Anthropic, Serper) — wymagają zmiennych środowiskowych."""

from __future__ import annotations

import os

import pytest
import requests

SERPER_API_URL = "https://google.serper.dev/search"


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


pytestmark = pytest.mark.integration


@pytest.mark.api_live
def test_serper_api_key_works():
    api_key = _env("SERPER_API_KEY")
    if not api_key:
        pytest.skip("Brak SERPER_API_KEY")

    response = requests.post(
        SERPER_API_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": "test", "gl": "de", "hl": "de", "num": 1},
        timeout=30,
    )

    assert response.status_code == 200, response.text[:300]
    data = response.json()
    assert "organic" in data or "searchParameters" in data


@pytest.mark.api_live
def test_anthropic_api_key_works():
    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("Brak ANTHROPIC_API_KEY")

    anthropic = pytest.importorskip("anthropic")
    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("CLAUDE_CONTACT_MODEL", "claude-sonnet-4-6")

    message = client.messages.create(
        model=model,
        max_tokens=16,
        messages=[{"role": "user", "content": "Odpowiedz jednym słowem: OK"}],
    )

    assert message.content
    assert message.content[0].text.strip()
