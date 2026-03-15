from src.ingest.models import NormalizedDocument, ChunkedDocument

def enrich_document_metadata(doc: NormalizedDocument):
    doc.metadata = {
        **doc.metadata,
        "source": doc.source,
        "doc_id": doc.doc_id,
        "title": doc.title,
        "created_at": doc.created_at,
    }
    return doc

def enrich_chunk_metadata(chunk: ChunkedDocument):
    chunk.metadata = {
        **chunk.metadata,
        "doc_id": chunk.doc_id,
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
    }
    return chunk