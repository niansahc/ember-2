import json
from pathlib import Path
from src.ingest.models import NormalizedDocument

def load_chatgpt_export(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    docs = []
    for i, convo in enumerate(data):
        title = convo.get("title", f"chat_{i}")
        created = convo.get("create_time")

        docs.append(
            NormalizedDocument(
                source="chatgpt",
                doc_id=f"chatgpt_{i}",
                title=title,
                created_at=str(created) if created else None,
                content="",
                metadata={"type": "chatgpt_export"},
            )
        )
    return docs