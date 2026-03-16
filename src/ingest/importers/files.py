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


def load_text_file(file_path, source="file", doc_id=None, extra_metadata=None):
    path = Path(file_path)

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    return [
        NormalizedDocument(
            source=source,
            doc_id=doc_id or path.stem,
            title=path.name,
            created_at=None,
            content=content,
            metadata={
                "type": "text_file",
                "extension": path.suffix.lower(),
                **(extra_metadata or {}),
            },
        )
    ]


def load_file(file_path, source="file", doc_id=None, extra_metadata=None):
    suffix = Path(file_path).suffix.lower()

    if suffix in SKIP_EXTENSIONS:
        raise ValueError(f"Skipping unsupported file type: {suffix}")

    if suffix in [".txt", ".md"]:
        return load_text_file(
            file_path,
            source=source,
            doc_id=doc_id,
            extra_metadata=extra_metadata,
        )

    if suffix == ".pdf":
        docs = load_pdf(file_path)
    elif suffix == ".docx":
        docs = load_docx(file_path)
    elif suffix == ".csv":
        docs = load_csv(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    for doc in docs:
        doc.source = source
        doc.doc_id = doc_id or doc.doc_id
        doc.metadata.update(extra_metadata or {})

    return docs