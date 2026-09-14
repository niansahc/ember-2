"""
src/memory/resolve_memory.py

Cross-type record resolution (ADR-015 amendment, PR #180, implementation
step 1).

`source_record_ids` on a derived record is a flat list of ids that can span
journal, ingested, conversation and reflection -- but every existing read
helper (read_memories, search_memories) is scoped to a single memory_type
directory via storage.get_memory_dir(). Neither can answer "give me these
ids, whatever type each one turns out to be."

resolve_source_records() answers that. It does not scan the corpus: every
memory record's filename is its id (write_memory.py sets
`memory_id = timestamp` and the file is `{timestamp}.json`; ingested chunks
use `{chunk.chunk_id}.json`), so for each id it checks at most one file per
candidate type directly, rather than listing and parsing every record of
every type to find a match.

find_conversation_ids_for_session() is the piece that makes the resolver
reachable from session_reflection.py and session_summary.py. Neither writer
has per-turn record ids available -- ConversationBuffer stores turns as bare
{"user": str, "assistant": str} dicts with no id ever attached, at any point
in their lifecycle (see conversation_buffer.py). The correlator both writers
DO have is session_id, and conversation records already carry
metadata.session_id at write time (openai_adapter.py). This resolves that
correlator into the ids resolve_source_records() needs.
"""

from __future__ import annotations

from src.core.config import get_private_vault_path
from src.core.jsonio import JsonIoError
from src.memory.read_memory import read_memories
from src.memory.storage import MemoryStorage

storage = MemoryStorage()

# Every memory type that can appear in a derived record's source_record_ids.
# state/task/project/reference are deliberately excluded: nothing generates
# provenance pointing at them today, and get_memory_items/get_profile_items
# do not retrieve through those directories either (see the retrieval
# ablation corpus docstring, tools/retrieval_ablation/corpus.py).
_CANDIDATE_TYPES: tuple[str, ...] = ("journal", "ingested", "conversation", "reflection")


def resolve_source_records(source_ids: list[str]) -> list[dict]:
    """Resolve a list of source_record_ids to their full records.

    Ids may come from any of _CANDIDATE_TYPES; the caller does not need to
    know which. Missing or unresolvable ids are skipped, not raised --
    a derived record's provenance list is allowed to reference a record
    that has since been suppressed or otherwise become unreadable, and one
    bad id must not fail the whole resolution.

    Cost is at most len(source_ids) * len(_CANDIDATE_TYPES) file-existence
    checks, not a directory scan -- the ingested corpus alone is 16,000+
    records, and this must not become O(corpus size) per call.
    """
    if not source_ids:
        return []

    vault = get_private_vault_path()
    resolved: list[dict] = []

    for source_id in source_ids:
        if not source_id:
            continue
        for memory_type in _CANDIDATE_TYPES:
            memory_dir = storage.get_memory_dir(vault, memory_type)
            file_path = memory_dir / f"{source_id}.json"
            if not file_path.exists():
                continue
            try:
                record = storage.read_json(file_path)
            except JsonIoError:
                continue
            # Ingested chunks carry no top-level "id" field in the JSON body
            # (src/ingest/writers.py writes "chunk_id", not "id"); the
            # filename-as-id convention still holds, so normalize it here
            # rather than leaving callers to special-case ingested records.
            record.setdefault("id", source_id)
            resolved.append(record)
            break

    return resolved


def find_conversation_ids_for_session(session_id: str, limit: int = 200) -> list[str]:
    """Return the ids of conversation records carrying this session_id.

    limit bounds a pathologically long session. It is a real, if soft,
    bound: ConversationBuffer.max_turns (20) caps only what the in-memory
    buffer HOLDS, not how many turns a single session has had written to
    the vault over its lifetime.
    """
    if not session_id:
        return []

    records = read_memories(memory_type="conversation", limit=limit)
    ids = []
    for record in records:
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("session_id") == session_id:
            record_id = record.get("id")
            if record_id:
                ids.append(record_id)
    return ids
