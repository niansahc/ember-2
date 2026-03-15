from pypdf import PdfReader
from src.ingest.models import NormalizedDocument

def load_pdf(file_path):
    reader = PdfReader(file_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    return [
        NormalizedDocument(
            source="pdf",
            doc_id=file_path,
            title=file_path,
            created_at=None,
            content=text,
            metadata={"type": "pdf"},
        )
    ]