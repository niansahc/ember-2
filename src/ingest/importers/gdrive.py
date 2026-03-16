from googleapiclient.discovery import build

from src.ingest.google_auth import get_drive_credentials
from src.ingest.models import NormalizedDocument


def list_drive_files(query):
    creds = get_drive_credentials()
    service = build("drive", "v3", credentials=creds)

    results = service.files().list(
        q=query,
        fields="files(id,name,mimeType,createdTime,modifiedTime,parents)",
        pageSize=100,
    ).execute()

    return results.get("files", [])


def parse_gdrive_files(files):
    docs = []

    for f in files:
        docs.append(
            NormalizedDocument(
                source="google_drive",
                doc_id=f["id"],
                title=f["name"],
                created_at=f.get("createdTime"),
                content="",
                metadata={
                    "mimeType": f["mimeType"],
                    "modifiedTime": f.get("modifiedTime"),
                    "parents": f.get("parents", []),
                },
            )
        )

    return docs