from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from src.ingest.google_auth import get_drive_credentials


EXPORT_MAP = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}

SKIP_MIME_TYPES = {
    "application/vnd.google-apps.folder",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "application/zip",
    "application/x-zip-compressed",
}


def sanitize_file_name(file_name):
    safe = Path(file_name).stem
    safe = safe.replace("/", "-").replace("\\", "-").strip()
    return safe or "downloaded_file"


def download_drive_file(file_id, mime_type, file_name, download_dir):
    if mime_type in SKIP_MIME_TYPES:
        raise ValueError(f"Skipping unsupported mime type: {mime_type}")

    creds = get_drive_credentials()
    service = build("drive", "v3", credentials=creds)

    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_file_name(file_name)

    if mime_type in EXPORT_MAP:
        export_mime, ext = EXPORT_MAP[mime_type]
        target_path = download_dir / f"{safe_name}{ext}"
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        ext = Path(file_name).suffix or ""
        target_path = download_dir / f"{safe_name}{ext}"
        request = service.files().get_media(fileId=file_id)

    with open(target_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    return str(target_path)