from src.retrieval.embedding_model import get_embedding_model


def embed_text(text: str):
    model = get_embedding_model()
    embedding = model.encode(text)

    return embedding.tolist()
