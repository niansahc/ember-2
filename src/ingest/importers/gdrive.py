from src.ingest.models import NormalizedDocument
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


def list_drive_files(creds, query):
    service = build("drive", "v3", credentials=creds)

    results = service.files().list(
        q=query,
        fields="files(id,name,mimeType)",
        pageSize=100
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
                created_at=None,
                content="",
                metadata={"mimeType": f["mimeType"]},
            )
        )

    return docs