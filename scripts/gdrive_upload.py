# -*- coding: utf-8 -*-
"""
Upload pliku neueroeffnung_wynik.xlsx do Google Drive.

Zmienne środowiskowe:
  GDRIVE_FOLDER_ID            — ID folderu docelowego na Drive
  GDRIVE_OAUTH_CLIENT_ID      — OAuth client ID
  GDRIVE_OAUTH_CLIENT_SECRET  — OAuth client secret
  GDRIVE_OAUTH_REFRESH_TOKEN  — OAuth refresh token
"""
from __future__ import annotations

import mimetypes
import os
import sys
from pathlib import Path


SCOPES = ("https://www.googleapis.com/auth/drive",)

_DRIVE_API_OPTS = {
    "supportsAllDrives": True,
    "supportsTeamDrives": True,
}
_LIST_OPTS = {
    **_DRIVE_API_OPTS,
    "includeItemsFromAllDrives": True,
}


def _load_credentials():
    refresh = (os.environ.get("GDRIVE_OAUTH_REFRESH_TOKEN") or "").strip()
    client_id = (os.environ.get("GDRIVE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET") or "").strip()

    if not all([refresh, client_id, client_secret]):
        print(
            "BLAD: Ustaw GDRIVE_OAUTH_CLIENT_ID, GDRIVE_OAUTH_CLIENT_SECRET "
            "i GDRIVE_OAUTH_REFRESH_TOKEN w GitHub Secrets."
        )
        sys.exit(1)

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=None,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(SCOPES),
    )
    creds.refresh(Request())
    return creds


def _upload_or_update(service, local: Path, folder_id: str) -> str:
    from googleapiclient.http import MediaFileUpload

    mime, _ = mimetypes.guess_type(str(local))
    media = MediaFileUpload(
        str(local), mimetype=mime or "application/octet-stream", resumable=True
    )

    safe_name = local.name.replace("'", "\\'")
    q = f"'{folder_id}' in parents and name = '{safe_name}' and trashed = false"
    existing = (
        service.files()
        .list(q=q, fields="files(id)", pageSize=1, corpora="allDrives", **_LIST_OPTS)
        .execute()
        .get("files")
        or []
    )

    if existing:
        fid = existing[0]["id"]
        service.files().update(
            fileId=fid, media_body=media, **_DRIVE_API_OPTS
        ).execute()
        print(f"Zaktualizowano: {local.name} (id={fid})")
        return fid

    body = {"name": local.name, "parents": [folder_id]}
    created = (
        service.files()
        .create(body=body, media_body=media, fields="id", **_DRIVE_API_OPTS)
        .execute()
    )
    fid = created["id"]
    print(f"Utworzono: {local.name} (id={fid})")
    return fid


def main() -> int:
    folder_id = (os.environ.get("GDRIVE_FOLDER_ID") or "").strip()
    if not folder_id:
        print("BLAD: Ustaw GDRIVE_FOLDER_ID w GitHub Secrets.")
        return 1

    files_to_upload = []
    for name in ("neueroeffnung_wynik.xlsx",):
        p = Path(name)
        if p.is_file():
            files_to_upload.append(p)

    if not files_to_upload:
        print("Brak plikow do wyslania (neueroeffnung_wynik.xlsx nie istnieje).")
        return 1

    creds = _load_credentials()
    from googleapiclient.discovery import build

    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    for f in files_to_upload:
        _upload_or_update(service, f, folder_id)

    print(
        f"Gotowe. Folder: https://drive.google.com/drive/folders/{folder_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
