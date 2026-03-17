from src.ingest.chunking import chunk_document
from src.ingest.clean_text import clean_text
from src.ingest.metadata import enrich_document_metadata, enrich_chunk_metadata

def run_ingestion_pipeline(documents):
    all_chunks = []

    for doc in documents:
        doc.content = clean_text(doc.content)
        doc = enrich_document_metadata(doc)
        chunks = chunk_document(doc)

        for chunk in chunks:
            all_chunks.append(enrich_chunk_metadata(chunk))

    return all_chunks