#!/usr/bin/env python3
# coding: utf-8

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _load_credentials() -> Credentials:
    token_file = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "").strip()
    token_json = os.getenv("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
    token_b64 = os.getenv("GOOGLE_OAUTH_TOKEN_B64", "").strip()

    if token_b64:
        token_json = base64.b64decode(token_b64.encode("utf-8")).decode("utf-8")

    if token_json:
        info = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(info, DRIVE_SCOPES)
    elif token_file:
        creds = Credentials.from_authorized_user_file(token_file, DRIVE_SCOPES)
    else:
        raise RuntimeError(
            "Set GOOGLE_OAUTH_TOKEN_JSON, GOOGLE_OAUTH_TOKEN_B64, or GOOGLE_OAUTH_TOKEN_FILE for Google Drive upload."
        )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        raise RuntimeError("Google Drive credentials are not valid. Regenerate token.json and update env vars.")
    return creds


def _service():
    return build("drive", "v3", credentials=_load_credentials(), cache_discovery=False)


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_folder(service, name: str, parent_id: str | None = None) -> str | None:
    clauses = [
        f"mimeType='{DRIVE_FOLDER_MIME}'",
        f"name='{_escape_query_value(name)}'",
        "trashed=false",
    ]
    if parent_id:
        clauses.append(f"'{parent_id}' in parents")
    result = service.files().list(
        q=" and ".join(clauses),
        fields="files(id,name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files") or []
    return files[0]["id"] if files else None


def _create_folder(service, name: str, parent_id: str | None = None) -> str:
    metadata = {"name": name, "mimeType": DRIVE_FOLDER_MIME}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]


def ensure_folder(service, name: str, parent_id: str | None = None) -> str:
    existing = _find_folder(service, name, parent_id)
    return existing or _create_folder(service, name, parent_id)


def _make_public(service, file_id: str) -> None:
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
        supportsAllDrives=True,
    ).execute()


def upload_file(path: str | Path, parent_id: str | None = None) -> dict:
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))

    service = _service()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    metadata = {"name": path.name}
    if parent_id:
        metadata["parents"] = [parent_id]

    media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
    uploaded = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,name,webViewLink,webContentLink",
        supportsAllDrives=True,
    ).execute()

    if os.getenv("GDRIVE_MAKE_PUBLIC", "1").lower() not in {"0", "false", "off", "no"}:
        _make_public(service, uploaded["id"])
        uploaded = service.files().get(
            fileId=uploaded["id"],
            fields="id,name,webViewLink,webContentLink",
            supportsAllDrives=True,
        ).execute()

    return uploaded


def upload_files(paths: Iterable[str | Path], folder_name: str | None = None) -> list[dict]:
    service = _service()
    root_id = os.getenv("GDRIVE_ROOT_FOLDER_ID", "").strip() or None
    parent_id = root_id

    if folder_name:
        parent_id = ensure_folder(service, folder_name[:120], root_id)

    results = []
    for path in paths:
        path = Path(path)
        if path.is_file():
            results.append(upload_file(path, parent_id))
    return results
