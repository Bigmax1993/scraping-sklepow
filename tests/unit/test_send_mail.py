"""Testy wysyłki Gmail (bez prawdziwego SMTP)."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest
from openpyxl import Workbook

import send_mail as sm

pytestmark = pytest.mark.unit


def _make_xlsx(path: Path) -> None:
    wb = Workbook()
    wb.active["A1"] = "test"
    wb.save(path)


def test_build_message_attaches_xlsx(tmp_path: Path):
    xlsx = tmp_path / "neueroeffnung_wynik.xlsx"
    _make_xlsx(xlsx)
    msg = sm.build_message(
        sender="svinchak1993@gmail.com",
        to="svinchak1993@gmail.com",
        path=xlsx,
    )
    assert msg["From"] == "svinchak1993@gmail.com"
    assert msg["To"] == "svinchak1993@gmail.com"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["neueroeffnung_wynik.xlsx"]


def test_send_excel_uses_env_and_smtp_hook(tmp_path: Path, monkeypatch):
    xlsx = tmp_path / "neueroeffnung_wynik.xlsx"
    _make_xlsx(xlsx)
    monkeypatch.setenv("GMAIL_USER", "svinchak1993@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("MAIL_TO", "")
    monkeypatch.setattr(sm, "_hydrate_user_env", lambda: None)
    seen: dict[str, object] = {}

    def fake_smtp(msg: EmailMessage, *, sender: str, to: str) -> None:
        seen["msg"] = msg
        seen["sender"] = sender
        seen["to"] = to

    def fake_imap(msg: EmailMessage) -> str:
        seen["imap"] = msg["To"]
        return "[Gmail]/Wysłane"

    info = sm.send_excel(xlsx, smtp_send=fake_smtp, imap_append=fake_imap)
    assert info["to"] == sm.DEFAULT_MAIL_TO
    assert seen["sender"] == "svinchak1993@gmail.com"
    assert seen["imap"] == sm.DEFAULT_MAIL_TO


def test_mail_config_requires_gmail_credentials(monkeypatch):
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="Brak zmiennych"):
        sm.mail_config()


def test_should_send_email_respects_flag(monkeypatch):
    monkeypatch.setenv("SEND_EMAIL", "false")
    assert sm.should_send_email() is False
    monkeypatch.setenv("SEND_EMAIL", "true")
    assert sm.should_send_email() is True
