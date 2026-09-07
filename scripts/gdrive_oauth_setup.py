# -*- coding: utf-8 -*-
"""
Jednorazowa konfiguracja OAuth do uploadu na Google Drive z GitHub Actions.

Uruchom lokalnie:
    pip install google-auth-oauthlib
    python scripts/gdrive_oauth_setup.py

Skrypt otworzy przegladarke, zaloguj sie na konto Google, a token zostanie
automatycznie ustawiony w GitHub Secrets repozytorium scraping-sklepow.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_REPO = "Bigmax1993/scraping-sklepow"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPO", DEFAULT_REPO))
    parser.add_argument("--no-github", action="store_true")
    args = parser.parse_args()

    client_id = (os.environ.get("GDRIVE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET") or "").strip()

    if not client_id or not client_secret:
        print("Ustaw zmienne GDRIVE_OAUTH_CLIENT_ID i GDRIVE_OAUTH_CLIENT_SECRET")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip install google-auth-oauthlib")
        return 1

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print("Otwieram przegladarke — zaloguj sie na konto Google...")
    creds = flow.run_local_server(port=0, open_browser=True)

    if not creds.refresh_token:
        print("Brak refresh_token — usun dostep w https://myaccount.google.com/permissions")
        return 1

    refresh = creds.refresh_token
    print(f"\nGDRIVE_OAUTH_REFRESH_TOKEN={refresh[:20]}...")

    if args.no_github:
        return 0

    for name, value in (
        ("GDRIVE_OAUTH_CLIENT_ID", client_id),
        ("GDRIVE_OAUTH_CLIENT_SECRET", client_secret),
        ("GDRIVE_OAUTH_REFRESH_TOKEN", refresh),
    ):
        subprocess.run(
            ["gh", "secret", "set", name, "-R", args.repo],
            input=value, text=True, check=True,
        )
        print(f"OK: gh secret set {name}")

    print(f"\nGotowe. Sprawdz: https://github.com/{args.repo}/actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
