from src.ingest.models import NormalizedDocument, ChunkedDocument

def chunk_document(doc, size=1200, overlap=150):
    text = doc.content.strip()
    chunks, start, i = [], 0, 0

    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()

        chunks.append(
            ChunkedDocument(
                source=doc.source,
                doc_id=doc.doc_id,
                chunk_id=f"{doc.doc_id}_chunk_{i}",
                title=doc.title,
                created_at=doc.created_at,
                content=chunk,
                metadata={**doc.metadata, "chunk_index": i},
            )
        )

        if end >= len(text):
            break

        start = end - overlap
        i += 1

    return chunks