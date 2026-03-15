from pathlib import Path
from src.ingest.models import NormalizedDocument

def load_text_file(file_path):
    path = Path(file_path)

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    return [
        NormalizedDocument(
            source="file",
            doc_id=path.stem,
            title=path.name,
            created_at=None,
            content=content,
            metadata={"type": "text_file"},
        )
    ]