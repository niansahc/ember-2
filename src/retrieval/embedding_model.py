"""
src/retrieval/embedding_model.py

Embedding model interface for Ember-2.

Uses Ollama's embedding API with nomic-embed-text (768-dimensional output)
by default. Model is configurable via EMBER_EMBED_MODEL in .env.

Previous implementation used sentence-transformers/all-MiniLM-L6-v2
(384-dimensional). Indexes built with the old model must be rebuilt
after upgrading — run: python scripts/rebuild_indexes.py
"""

import ollama

from src.core.config import get_ember_embed_model


# nomic-embed-text has an 8192 token context window.
# Truncate at 8000 chars (~2000 tokens) to stay well within limits.
_MAX_EMBED_CHARS = 4000


def _truncate(text: str) -> str:
    """Truncate text to fit the embedding model's context window."""
    if len(text) > _MAX_EMBED_CHARS:
        return text[:_MAX_EMBED_CHARS]
    return text


def embed_text(text: str) -> list[float]:
    """
    Generate an embedding vector for the given text using Ollama.

    Returns a list of floats (768-dimensional for nomic-embed-text).
    """
    model = get_ember_embed_model()
    response = ollama.embeddings(model=model, prompt=_truncate(text))
    return response["embedding"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embedding vectors for multiple texts in a single batch.

    Much faster than calling embed_text() in a loop — Ollama processes
    the batch in one pass. Returns a list of embedding vectors in the
    same order as the input texts.
    """
    if not texts:
        return []
    model = get_ember_embed_model()
    response = ollama.embed(model=model, input=[_truncate(t) for t in texts])
    return response["embeddings"]
