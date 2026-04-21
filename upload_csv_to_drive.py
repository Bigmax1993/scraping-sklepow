import argparse
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def build_drive_service(credentials_path: str):
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials)


def upload_or_replace_file(service, file_path: Path, folder_id: str) -> str:
    filename = file_path.name

    existing = (
        service.files()
        .list(
            q=(
                f"name = '{filename}' and '{folder_id}' in parents "
                "and trashed = false"
            ),
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )

    media = MediaFileUpload(str(file_path), mimetype="text/csv", resumable=False)

    if existing:
        file_id = existing[0]["id"]
        updated = (
            service.files()
            .update(fileId=file_id, media_body=media, fields="id,name,webViewLink")
            .execute()
        )
        return updated["webViewLink"]

    metadata = {"name": filename, "parents": [folder_id]}
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id,name,webViewLink")
        .execute()
    )
    return created["webViewLink"]


def main():
    parser = argparse.ArgumentParser(
        description="Upload final CSV output to Google Drive folder."
    )
    parser.add_argument(
        "--file",
        default="Wyniki/germany_markets_selenium_closed_only.csv",
        help="Path to CSV file to upload.",
    )
    parser.add_argument(
        "--folder-id",
        default=os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip(),
        help="Google Drive folder ID.",
    )
    parser.add_argument(
        "--credentials",
        default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip(),
        help="Path to service-account JSON credentials.",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        raise FileNotFoundError(f"CSV file not found: {file_path}")
    if not args.folder_id:
        raise ValueError("Missing folder id. Set --folder-id or GOOGLE_DRIVE_FOLDER_ID.")
    if not args.credentials:
        raise ValueError(
            "Missing credentials path. Set --credentials or GOOGLE_APPLICATION_CREDENTIALS."
        )

    drive_service = build_drive_service(args.credentials)
    link = upload_or_replace_file(drive_service, file_path, args.folder_id)
    print(f"Uploaded CSV to Google Drive: {link}")


if __name__ == "__main__":
    main()
