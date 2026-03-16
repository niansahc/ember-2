from pathlib import Path

from src.ingest.models import NormalizedDocument


def load_csv(file_path):
    path = Path(file_path)

    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    return [
        NormalizedDocument(
            source="csv",
            doc_id=path.stem,
            title=path.name,
            created_at=None,
            content=content,
            metadata={"type": "csv", "extension": path.suffix.lower()},
        )
    ]