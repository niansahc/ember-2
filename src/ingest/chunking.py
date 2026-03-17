from src.ingest.models import ChunkedDocument, NormalizedDocument


def chunk_document(
    doc: NormalizedDocument,
    size: int = 1200,
    overlap: int = 150,
):
    if doc.source == "chatgpt" and "messages" in doc.metadata:
        chunks = []

        for i, message in enumerate(doc.metadata["messages"]):
            text = message.strip()

            if not text:
                continue

            chunks.append(
                ChunkedDocument(
                    source=doc.source,
                    doc_id=doc.doc_id,
                    chunk_id=f"{doc.doc_id}_chunk_{i}",
                    title=doc.title,
                    created_at=doc.created_at,
                    content=text,
                    metadata={
                        **doc.metadata,
                        "chunk_index": i,
                    },
                )
            )

        return chunks

    text = doc.content.strip()
    chunks = []
    start = 0
    i = 0

    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()

        if chunk:
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