from pathlib import Path

from src.ingest.importers.csv import load_csv
from src.ingest.importers.docx import load_docx
from src.ingest.importers.pdf import load_pdf
from src.ingest.models import NormalizedDocument


SKIP_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".zip",
}


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
            metadata={"type": "text_file", "extension": path.suffix.lower()},
        )
    ]


def load_file(file_path):
    suffix = Path(file_path).suffix.lower()

    if suffix in SKIP_EXTENSIONS:
        raise ValueError(f"Skipping unsupported file type: {suffix}")

    if suffix in [".txt", ".md"]:
        return load_text_file(file_path)

    if suffix == ".pdf":
        return load_pdf(file_path)

    if suffix == ".docx":
        return load_docx(file_path)

    if suffix == ".csv":
        return load_csv(file_path)

    raise ValueError(f"Unsupported file type: {suffix}")