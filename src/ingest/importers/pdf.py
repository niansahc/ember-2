from pathlib import Path
from pypdf import PdfReader

from src.ingest.models import NormalizedDocument


def load_pdf(file_path):
    path = Path(file_path)

    reader = PdfReader(file_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    return [
        NormalizedDocument(
            source="file",
            doc_id=path.stem,
            title=path.name,
            created_at=None,
            content=text,
            metadata={"type": "pdf"},
        )
    ]