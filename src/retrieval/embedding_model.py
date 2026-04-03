"""
src/retrieval/embedding_model.py

Embedding model interface for Ember-2.

Currently uses sentence-transformers/all-MiniLM-L6-v2 (384-dimensional).
Upgrade to nomic-embed-text via Ollama is in progress (v0.13.0) —
requires a full index rebuild before switching.
"""

from sentence_transformers import SentenceTransformer


_model = None


def get_embedding_model():
    global _model

    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")

    return _model
