# -*- coding: utf-8 -*-
"""Wysyłka neueroeffnung_wynik.xlsx przez Gmail SMTP + kopia w Wysłanych (IMAP)."""
from __future__ import annotations

import argparse
import imaplib
import os
import re
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_XLSX = SCRIPT_DIR / "neueroeffnung_wynik.xlsx"
DEFAULT_MAIL_TO = "svinchak1993@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"
SENT_CANDIDATES = (
    "[Gmail]/Sent Mail",
    "[Gmail]/Wysłane",
    "Sent",
    "Wysłane",
)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip().strip('"').strip("'")


def _hydrate_user_env() -> None:
    """Windows: wczytaj GMAIL_* z User env, gdy brak w bieżącym procesie."""
    names = ("GMAIL_USER", "GMAIL_APP_PASSWORD", "SEND_EMAIL")
    try:
        import winreg
    except ImportError:
        return
    for name in names:
        if _env(name):
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                val, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if val:
            os.environ[name] = str(val).strip()


def should_send_email() -> bool:
    flag = _env("SEND_EMAIL").lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return True


def mail_config() -> tuple[str, str, str]:
    user = _env("GMAIL_USER")
    password = _env("GMAIL_APP_PASSWORD").replace(" ", "")
    to = DEFAULT_MAIL_TO
    if not user or not password:
        missing = [
            n
            for n, v in (
                ("GMAIL_USER", user),
                ("GMAIL_APP_PASSWORD", password),
            )
            if not v
        ]
        raise RuntimeError("Brak zmiennych: " + ", ".join(missing))
    return user, password, to


def _mailbox_name(list_line: str) -> str | None:
    names = re.findall(r'"([^"]+)"', list_line)
    if names:
        return names[-1]
    parts = list_line.rsplit(" ", 1)
    return parts[-1].strip() if parts else None


def sent_folder_from_list(lines: list[bytes | str]) -> str | None:
    for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        flags = line.split(")", 1)[0].upper()
        if "\\SENT" in flags:
            name = _mailbox_name(line)
            if name:
                return name
    return None


def _quote_mailbox(name: str) -> str:
    if name.startswith('"') and name.endswith('"'):
        return name
    return f'"{name}"'


def save_to_sent(
    sender: str,
    password: str,
    msg: EmailMessage,
    *,
    imap_append=None,
) -> str:
    if imap_append is not None:
        folder = imap_append(msg)
        return str(folder or SENT_CANDIDATES[0])
    with imaplib.IMAP4_SSL(IMAP_HOST, timeout=45) as imap:
        imap.login(sender, password)
        typ, boxes = imap.list()
        folder = sent_folder_from_list(boxes or [])
        if not folder:
            for cand in SENT_CANDIDATES:
                sel = imap.select(_quote_mailbox(cand), readonly=True)
                if sel[0] == "OK":
                    folder = cand
                    break
        if not folder:
            raise RuntimeError("Nie znaleziono folderu Wysłane — włącz IMAP w Gmailu")
        payload = msg.as_bytes()
        imap.append(
            _quote_mailbox(folder),
            "\\Seen",
            imaplib.Time2Internaldate(time.time()),
            payload,
        )
    return folder


def build_message(
    *,
    sender: str,
    to: str,
    path: Path,
    subject: str | None = None,
) -> EmailMessage:
    xlsx = Path(path)
    if not xlsx.is_file():
        raise FileNotFoundError(f"Brak pliku: {xlsx}")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject or (
        f"Neueroeffnung — raport Excel ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    )
    msg.set_content(
        "W załączniku znajduje się gotowy plik Excel ze scrapingu neueroeffnung.info.\n"
        "Arkusze: Markety, Restauracje, Drogerie, Centra handlowe, Harmonogram, "
        "Według regionu, Raport braków, Pominięte.\n"
    )
    msg.add_attachment(
        xlsx.read_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=xlsx.name,
    )
    return msg


def send_excel(
    path: Path | None = None,
    *,
    smtp_send=None,
    imap_append=None,
) -> dict[str, str]:
    _hydrate_user_env()
    if not should_send_email():
        raise RuntimeError("Wysyłka e-mail wyłączona (SEND_EMAIL=false)")
    sender, password, to = mail_config()
    xlsx = Path(path) if path is not None else DEFAULT_XLSX
    msg = build_message(sender=sender, to=to, path=xlsx)
    if smtp_send is None:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=45) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
    else:
        smtp_send(msg, sender=sender, to=to)
    sent_folder = save_to_sent(sender, password, msg, imap_append=imap_append)
    return {
        "from": sender,
        "to": to,
        "file": str(xlsx.resolve()),
        "sent_folder": sent_folder,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Wyślij neueroeffnung_wynik.xlsx na Gmail")
    parser.add_argument("--plik", type=Path, default=DEFAULT_XLSX, help="Ścieżka do xlsx")
    args = parser.parse_args(argv)
    info = send_excel(args.plik)
    print(
        f"Wysłano {Path(info['file']).name} → {info['to']} "
        f"(kopia: {info['from']} / {info['sent_folder']})"
    )


if __name__ == "__main__":
    main()
