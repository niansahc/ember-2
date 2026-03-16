from src.ingest.models import NormalizedDocument, ChunkedDocument


def chunk_document(doc: NormalizedDocument, size=1200, overlap=150):
    text = doc.content.strip()

    if not text:
        return []

    chunks = []
    start = 0
    i = 0

    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()
        chunk_id = f"{doc.doc_id}_chunk_{i}"

        chunks.append(
            ChunkedDocument(
                source=doc.source,
                doc_id=doc.doc_id,
                chunk_id=chunk_id,
                title=doc.title,
                created_at=doc.created_at,
                content=chunk,
                metadata={
                    **doc.metadata,
                    "source": doc.source,
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "created_at": doc.created_at,
                    "chunk_index": i,
                    "chunk_id": chunk_id,
                },
            )
        )

        if end >= len(text):
            break

        start = end - overlap
        i += 1

    return chunks