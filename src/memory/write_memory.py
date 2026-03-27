import re
from datetime import datetime
from typing import Any

from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


storage = MemoryStorage()
vector_index = VectorIndex()


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def should_skip_memory(text: str, memory_type: str = "journal") -> bool:
    normalized = normalize_text(text)

    if not normalized:
        return True

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

    if normalized.startswith("{") or normalized.startswith("["):
        return True

    if "```" in text:
        return True

    return False


def flatten_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}

    flattened = {}

    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            flattened[key] = value
        elif isinstance(value, list):
            flattened[key] = [
                item for item in value if isinstance(item, (str, int, float, bool))
            ][:20]
        else:
            flattened[key] = str(value)

    return flattened


def write_memory(
    text: str,
    memory_type: str = "journal",
    source: str = "api",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """
    Write a memory record to the vault.

    memory_type must be a valid type from VALID_MEMORY_TYPES in
    src/memory/storage.py. Invalid types will raise ValueError
    at the storage layer (get_memory_dir validation).
    """
    if should_skip_memory(text, memory_type=memory_type):
        return None

    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    memory_id = timestamp
    normalized = normalize_text(text)
    clean_metadata = flatten_metadata(metadata)

    memory = {
        "id": memory_id,
        "timestamp": timestamp,
        "type": memory_type,
        "text": text,
        "normalized_text": normalized,
        "source": source,
        "tags": tags or [],
        "metadata": clean_metadata,
    }

    file_path = memory_dir / f"{timestamp}.json"
    storage.write_json(file_path, memory)

    index_path = vector_index.get_index_path(vault, memory_type)
    index_data = vector_index.load_index(index_path)

    embedding = embed_text(text)

    index_data.append(
        {
            "id": memory_id,
            "timestamp": timestamp,
            "type": memory_type,
            "text": text,
            "normalized_text": normalized,
            "source": source,
            "tags": tags or [],
            "file_path": str(file_path),
            "embedding": embedding,
            "metadata": clean_metadata,
        }
    )

    vector_index.save_index(index_path, index_data)

    return file_path