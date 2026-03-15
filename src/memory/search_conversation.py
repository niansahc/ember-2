from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


storage = MemoryStorage()
vector_index = VectorIndex()


def search_conversation_memories(query, top_k=5):
    """
    Semantic search over conversation memories.
    """
    vault = get_private_vault_path()
    query_embedding = embed_text(query)

    results = vector_index.search(
        vault_path=vault,
        memory_type="conversation",
        query_embedding=query_embedding,
        top_k=top_k,
    )

    memories = []

    for result in results:
        memory_path = result["path"]
        memory = storage.read_json(memory_path)

        memories.append({
            "score": result["score"],
            "memory": memory
        })

    return memories