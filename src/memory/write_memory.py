import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import (
    VaultWriteBlocked,
    get_private_vault_path,
    vault_writes_blocked,
)
from src.memory.authorship import classify_authorship
from src.memory.eval_fixtures import should_index_record
from src.memory.storage import MemoryStorage
from src.retrieval.embed_memory import embed_text
from src.retrieval.sqlite_vector_store import SqliteVectorStore
from src.retrieval.store_cache import get_store
from src.retrieval.vector_index import VectorIndex


logger = logging.getLogger(__name__)

storage = MemoryStorage()
vector_index = VectorIndex()

# Memory types stored in SQLite (memory.db) rather than JSON indexes
SQLITE_MEMORY_TYPES = {"conversation", "profile", "reflection", "journal"}

# Module-level guard against same-microsecond filename collisions in
# write_memory(). Filename convention is `{timestamp}.json` so two
# back-to-back writes (e.g. user turn + assistant turn in the same
# request, or rapid automated batches) that land on the same microsecond
# produce identical paths and MemoryStorage.write_json overwrites the
# prior record. Defense-in-depth — same fix as session._now_id() and
# task_service.next_timestamp(). See BUG-005.
_last_timestamp: str = ""


def _next_timestamp() -> str:
    """Generate a microsecond-precision timestamp string, guaranteed
    unique per process.

    Spins on `datetime.now()` until the result differs from the previous
    return value. The spin can never run for longer than one microsecond
    of real wall-clock time.
    """
    global _last_timestamp
    while True:
        candidate = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        if candidate != _last_timestamp:
            _last_timestamp = candidate
            return candidate


def _get_write_memory_store() -> SqliteVectorStore:
    """Store for the active vault's memory.db, on the write path.

    Resolves the vault on every call. The previous version took the vault
    as an argument and ignored it whenever its singleton was already
    populated, so a write after a vault swap was handed the previous
    vault's connection and the record's embedding landed in the wrong
    vault while its JSON file landed in the right one.

    Unlike the read accessors in semantic_search, this one creates the db
    when it does not exist: a first write to a fresh vault has to.
    """
    db_path = get_private_vault_path() / "embeddings" / "memory.db"
    return get_store(db_path)


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def should_skip_memory(text: str, memory_type: str = "journal") -> bool:
    normalized = normalize_text(text)

    if not normalized:
        return True

    # Conversation turns and deviation records are never skipped —
    # short messages like "Yes" are meaningful context, and deviation
    # records use [deviation:class] prefix that triggers JSON guards.
    if memory_type not in ("conversation", "deviation"):
        min_length = 20 if memory_type == "journal" else 40
        if len(normalized) < min_length:
            return True

    meta_markers = (
        "user asked:",
        "ember responded:",
        "assistant responded:",
        "assistant said:",
        "### task:",
        "generate 1-3 broad tags",
        '"user_message":',
        '"memory_items":',
        '"reflection_items":',
        '"conversation_id":',
        '"chunk_id":',
    )

    if any(marker in normalized for marker in meta_markers):
        return True

    # Skip JSON payload detection for deviation records (they use [deviation:class] prefix)
    if memory_type != "deviation" and (normalized.startswith("{") or normalized.startswith("[")):
        return True

    if "```" in text:
        return True

    return False


# Metadata list keys exempt from the 20-item truncation below. ADR-015
# amendment (PR #180): source-bounding a derived record's tier needs its
# FULL source set, not an arbitrary first-20 subset. lodestone_synthesis.py's
# reflections list is not pre-capped at write time the way
# generate_reflection.py's 8-item selection is, so silently truncating this
# key would drop the sources a tier bound needs without anyone noticing.
_UNTRUNCATED_LIST_KEYS = frozenset({"source_record_ids"})


def flatten_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}

    flattened = {}

    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            flattened[key] = value
        elif isinstance(value, list):
            primitives = [
                item for item in value if isinstance(item, (str, int, float, bool))
            ]
            flattened[key] = (
                primitives
                if key in _UNTRUNCATED_LIST_KEYS
                else primitives[:20]
            )
        else:
            flattened[key] = str(value)

    return flattened


# Metadata kind of the system_event record that ends an exchange with no reply
# (ADR-047). Such records are never indexed and never returned in history.
EXCHANGE_OUTCOME_KIND = "exchange_outcome"


def write_canonical_record(
    text: str,
    memory_type: str = "journal",
    source: str = "api",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict, Path]:
    """
    Write one canonical record to the vault, without indexing it.

    No content filter runs here: whether the text is worth indexing is
    should_index()'s question, asked separately (ADR-047). Returns the
    record and the path of the file written.

    Raises VaultWriteBlocked when a vault swap could not be verified, and
    lets storage errors propagate, so a caller can never mistake a failed
    write for a skipped one.
    """
    reason = vault_writes_blocked()
    if reason:
        logger.error("[VAULT_BLOCK] refused memory write: %s", reason)
        raise VaultWriteBlocked(reason)

    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    timestamp = _next_timestamp()
    clean_metadata = flatten_metadata(metadata)

    # ADR-021 prerequisite: tag conversation records with whether they
    # mention a named third party. Heuristic detection at write time.
    # setdefault preserves explicit caller overrides (manual annotations,
    # ingestion-time NER, etc.).
    if memory_type == "conversation":
        from src.memory.third_party_detection import contains_named_third_party
        clean_metadata.setdefault(
            "contains_named_third_party",
            contains_named_third_party(text),
        )

    memory = {
        "id": timestamp,
        "timestamp": timestamp,
        "type": memory_type,
        "text": text,
        "normalized_text": normalize_text(text),
        "source": source,
        "tags": tags or [],
        "metadata": clean_metadata,
    }

    file_path = memory_dir / f"{timestamp}.json"
    storage.write_json(file_path, memory)
    return memory, file_path


def should_index(record: dict, vault: Path | None = None) -> bool:
    """
    Decide whether a canonical record belongs in the index.

    The one filter shared by the live write path, the exchange recorder,
    index rebuilds and monthly reflection (ADR-047): exchange outcome records
    never index, should_skip_memory() rejects low-value text, and eval
    fixtures index only into the configured test vault (issue #211).
    """
    metadata = record.get("metadata") or {}
    if metadata.get("kind") == EXCHANGE_OUTCOME_KIND:
        return False
    if should_skip_memory(
        record.get("text") or "", memory_type=record.get("type") or "journal",
    ):
        return False
    if vault is None:
        vault = get_private_vault_path()
    return should_index_record(vault, record.get("source"), metadata)


def index_record(record: dict, file_path: Path) -> None:
    """
    Embed a canonical record and add it to the active vault's index.

    SQLite types go to memory.db; any other type goes to its JSON index.
    Callers decide whether to index (should_index); this only does it.
    """
    vault = get_private_vault_path()
    memory_type = record["type"]
    memory_id = record["id"]
    text = record["text"]
    tags = record.get("tags") or []
    clean_metadata = record.get("metadata") or {}
    source = record.get("source", "api")
    normalized = record.get("normalized_text") or normalize_text(text)

    embedding = embed_text(text)

    if memory_type in SQLITE_MEMORY_TYPES:
        # Write to SQLite (memory.db) for migrated types
        store = _get_write_memory_store()
        store_id = store.insert({
            "id": memory_id,
            "text": text,
            "embedding": embedding,
            "source": source,
            "memory_type": memory_type,
            "created_at": record.get("timestamp", memory_id),
            "authorship": classify_authorship(memory_type, source, clean_metadata),
            "metadata": {
                **clean_metadata,
                "file_path": str(file_path),
                "normalized_text": normalized,
                "tags": tags,
                "source_field": source,
            },
        })
        if store_id != memory_id:
            # The index disambiguated a colliding id rather than dropping
            # the row (issue #210). Worth a line in the log: the record is
            # reachable, but its store key no longer equals its canonical
            # id, and anything that assumed those were the same for records
            # written by this path is now wrong.
            logger.warning(
                "[VAULT] indexed a %s record under a disambiguated store id: "
                "its canonical id was already claimed in memory.db",
                memory_type,
            )
    else:
        # Fallback to JSON index for non-migrated types
        index_path = vector_index.get_index_path(vault, memory_type)
        index_data = vector_index.load_index(index_path)

        index_data.append(
            {
                "id": memory_id,
                "timestamp": record.get("timestamp", memory_id),
                "type": memory_type,
                "text": text,
                "normalized_text": normalized,
                "source": source,
                "tags": tags,
                "file_path": str(file_path),
                "embedding": embedding,
                "metadata": clean_metadata,
            }
        )

        vector_index.save_index(index_path, index_data)


def write_memory(
    text: str,
    memory_type: str = "journal",
    source: str = "api",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Write a memory record to the vault and index it inline.

    memory_type must be a valid type from VALID_MEMORY_TYPES in
    src/memory/storage.py. Invalid types will raise ValueError
    at the storage layer (get_memory_dir validation).

    Unlike write_canonical_record(), text that should_skip_memory() rejects
    is not written at all. Every caller except the exchange recorder keeps
    that behavior (ADR-047).

    Raises VaultWriteBlocked when a vault swap could not be verified.
    Refusing loudly is deliberate: a silent return would be
    indistinguishable from a skipped write, and the record would be lost
    without anyone noticing.
    """
    reason = vault_writes_blocked()
    if reason:
        logger.error("[VAULT_BLOCK] refused memory write: %s", reason)
        raise VaultWriteBlocked(reason)

    if should_skip_memory(text, memory_type=memory_type):
        return None

    memory, file_path = write_canonical_record(
        text=text,
        memory_type=memory_type,
        source=source,
        tags=tags,
        metadata=metadata,
    )

    # Eval fixtures are written to disk like any other record but are only
    # indexed into the configured test vault. Indexing them elsewhere puts
    # synthetic records into someone's personal memory where retrieval cannot
    # tell them from real recollection -- see src/memory/eval_fixtures.py and
    # issue #211. Fails closed: an unidentifiable vault does not get them.
    # Checked before embedding, so a skipped fixture costs no model call.
    if not should_index_record(get_private_vault_path(), source, memory["metadata"]):
        logger.warning(
            "[VAULT] eval fixture written to disk but not indexed: vault is not "
            "the configured test vault (memory_type=%s source=%s)",
            memory_type,
            source,
        )
        return memory

    index_record(memory, file_path)
    return file_path
