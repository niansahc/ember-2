"""
src/ingest/index_routing.py

Which vector store an ingested chunk is indexed into, and as which type.

Two classes of chunk arrive through the ingest pipeline, and ADR-015
(amendment, "Timestamps and prior-substrate conversation") separates them:

  - Prior-substrate conversation: a ChatGPT export. It is the record of one
    continuous relationship that changed substrate, so it is `conversation`
    and lives in memory.db with the rest of the conversation corpus. Any
    future export-and-ingest of the same form inherits that classification.
  - Everything else (documents, files, Drive): genuine third-party material,
    typed `ingested`, in ingested.db.

semantic_search() routes by which file a row lives in, so the store and the
type must be chosen together; a conversation row in ingested.db would be
reported as `ingested` on every read.

ADR-033 is applied before this, by the writer: an assistant-role chunk is
written to disk and indexed nowhere. The predicate still accepts the
assistant role because scripts/rebuild_indexes.py uses it to collect the
prior-substrate corpus, assistant turns included, for memory.db; that the
writer and the rebuild disagree on assistant turns is a known open item.
"""

from __future__ import annotations

from typing import NamedTuple

PRIOR_SUBSTRATE_SOURCE = "chatgpt"
PRIOR_SUBSTRATE_ROLES = frozenset({"user", "assistant"})


class IndexTarget(NamedTuple):
    db_name: str
    memory_type: str
    # Rows whose content a new chunk is compared against before indexing:
    # a memory_type, or None for every row in the store. memory.db holds other
    # types; ingested.db's rows are all read as `ingested` whatever their
    # memory_type column says (the v0.6.0 migration stored the document
    # metadata type there).
    dedupe_type: str | None


INGESTED_TARGET = IndexTarget("ingested.db", "ingested", None)
CONVERSATION_TARGET = IndexTarget("memory.db", "conversation", "conversation")


def is_prior_substrate(source: str | None, metadata: dict | None) -> bool:
    """True when a chunk is prior-substrate conversation.

    Source is the chat export and the role resolves to exactly user or
    assistant. A chunk whose role is missing or unrecognised is not guessed
    at: it stays `ingested`. Same predicate as the ADR-015 reclassification
    (scripts/reclassify_prior_substrate_conversation.py).
    """
    if (source or "") != PRIOR_SUBSTRATE_SOURCE:
        return False
    return ((metadata or {}).get("role") or "") in PRIOR_SUBSTRATE_ROLES


def index_target(source: str | None, metadata: dict | None) -> IndexTarget:
    """The store and memory type a chunk is indexed as."""
    if is_prior_substrate(source, metadata):
        return CONVERSATION_TARGET
    return INGESTED_TARGET
