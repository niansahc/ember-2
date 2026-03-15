import re
from datetime import datetime

from datetime import datetime

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

def write_memory(text, memory_type="journal", source="api", tags=None, metadata=None):
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    memory_id = timestamp
    normalized = normalize_text(text)

    memory = {
        "id": memory_id,
        "timestamp": timestamp,
        "type": memory_type,
        "text": text,
        "normalized_text": normalized,
        "source": source,
        "tags": tags or [],
        "metadata": metadata or {},
    }


    file_path = memory_dir / f"{timestamp}.json"

    storage.write_json(file_path, memory)

    index_path = vector_index.get_index_path(vault, memory_type)
    index_data = vector_index.load_index(index_path)

    embedding = embed_text(text)

    index_data.append({
        "id": memory_id,
        "timestamp": timestamp,
        "type": memory_type,
        "text": text,
        "normalized_text": normalized,
        "source": source,
        "file_path": str(file_path),
        "embedding": embedding
    })

    vector_index.save_index(index_path, index_data)

    return file_path