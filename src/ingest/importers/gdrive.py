from src.ingest.models import NormalizedDocument

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