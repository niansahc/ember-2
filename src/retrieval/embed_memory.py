"""
src/retrieval/embed_memory.py

Text embedding interface for Ember-2.

Delegates to embedding_model.py which uses Ollama with nomic-embed-text.
"""

from src.retrieval.embedding_model import embed_text, embed_texts


__all__ = ["embed_text", "embed_texts"]
