import re

from src.ingest.models import ChunkedDocument, NormalizedDocument


def chunk_document(
    doc: NormalizedDocument,
    size: int = 1200,
    overlap: int = 150,
):
    if doc.source == "chatgpt" and "messages" in doc.metadata:
        return _chunk_chatgpt_document(doc)

    text = doc.content.strip()
    chunks = []
    start = 0
    i = 0

    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(
                ChunkedDocument(
                    source=doc.source,
                    doc_id=doc.doc_id,
                    chunk_id=f"{doc.doc_id}_chunk_{i}",
                    title=doc.title,
                    created_at=doc.created_at,
                    content=chunk,
                    metadata={**doc.metadata, "chunk_index": i},
                )
            )

        if end >= len(text):
            break

        start = end - overlap
        i += 1

    return chunks


def _chunk_chatgpt_document(doc: NormalizedDocument) -> list[ChunkedDocument]:
    raw_messages = doc.metadata.get("messages", [])
    chunks: list[ChunkedDocument] = []

    for raw_index, message in enumerate(raw_messages):
        text = str(message).strip()

        if not text:
            continue

        normalized = _normalize_text(text)

        if _should_skip_chatgpt_message(normalized):
            continue

        role = _detect_role(normalized)

        chunks.append(
            ChunkedDocument(
                source=doc.source,
                doc_id=doc.doc_id,
                chunk_id=f"{doc.doc_id}_chunk_{len(chunks)}",
                title=doc.title,
                created_at=doc.created_at,
                content=text,
                metadata={
                    **doc.metadata,
                    "chunk_index": len(chunks),
                    "raw_message_index": raw_index,
                    "role": role,
                    "content_kind": _classify_content_kind(normalized, role),
                },
            )
        )

    return chunks


def _should_skip_chatgpt_message(text: str) -> bool:
    if not text:
        return True

    if len(text) < 30:
        return True

    if _looks_like_trivial_chat_control(text):
        return True

    if _looks_like_json_payload(text):
        return True

    if _looks_like_asset_pointer_payload(text):
        return True

    if _looks_like_tool_trace(text):
        return True

    if _looks_like_prompt_scaffolding(text):
        return True

    if _looks_like_low_value_assistant_filler(text):
        return True

    if _looks_like_shell_or_runtime_output(text):
        return True

    if _looks_like_mixed_user_assistant_artifact(text):
        return True

    return False


def _detect_role(text: str) -> str:
    if text.startswith("user:"):
        return "user"
    if text.startswith("assistant:"):
        return "assistant"
    if text.startswith("tool:"):
        return "tool"
    if text.startswith("system:"):
        return "system"
    return "unknown"


def _classify_content_kind(text: str, role: str) -> str:
    if _looks_like_question(text):
        return "question"
    if role == "assistant" and _looks_like_instructional_answer(text):
        return "answer"
    if role == "user" and _looks_like_experience_or_status(text):
        return "experience"
    if role == "user":
        return "user_content"
    if role == "assistant":
        return "assistant_content"
    return "other"


def _looks_like_json_payload(text: str) -> bool:
    if text.startswith("{") or text.startswith("["):
        return True

    json_markers = (
        '"id":',
        '"object":',
        '"created":',
        '"model":',
        '"choices":',
        '"messages":',
        '"memory_items":',
        '"reflection_items":',
        '"conversation_id":',
        '"chunk_id":',
        '"asset_pointer":',
        '"content_type":',
        '"image_asset_pointer"',
        '"metadata": {',
        '"size_bytes":',
        '"width":',
        '"height":',
    )
    return any(marker in text for marker in json_markers)


def _looks_like_asset_pointer_payload(text: str) -> bool:
    markers = (
        "sediment://file_",
        "asset_pointer",
        "image_asset_pointer",
        "watermarked_asset_pointer",
        "container_pixel_height",
        "container_pixel_width",
        "size_bytes",
        '"width":',
        '"height":',
    )
    return any(marker in text for marker in markers)


def _looks_like_tool_trace(text: str) -> bool:
    markers = (
        "tool:",
        "clarifying the question",
        "mapping out the user's request",
        "openai policies",
        "analysis:",
        "recipient=",
        "function call",
    )
    return any(marker in text for marker in markers)


def _looks_like_trivial_chat_control(text: str) -> bool:
    markers = (
        "change the title of this chat",
        "rename this chat",
        "title of this chat",
        "can we change the title of this chat",
    )
    return any(marker in text for marker in markers)


def _looks_like_prompt_scaffolding(text: str) -> bool:
    markers = (
        "### task:",
        "generate 1-3 broad tags",
        "generate 1-3 specific tags",
        "based on the following chat history",
        "return valid json",
        "tag categorizing the main themes",
    )
    return any(marker in text for marker in markers)


def _looks_like_low_value_assistant_filler(text: str) -> bool:
    if not text.startswith("assistant:"):
        return False

    filler_markers = (
        "you're very welcome",
        "i'm here to help",
        "if you'd like",
        "could you clarify",
        "can you clarify",
        "tell me more",
        "consider sharing details",
        "this would help refine",
    )
    return any(marker in text for marker in filler_markers)


def _looks_like_shell_or_runtime_output(text: str) -> bool:
    markers = (
        "(.venv)",
        "ps c:\\",
        "uvicorn ",
        "info:",
        "traceback",
        "file \"c:\\",
        "line ",
        "application startup complete",
        "started server process",
        "started reloader process",
        "waiting for application startup",
        "press ctrl+c to quit",
        "loading weights:",
        "bertmodel load report",
        "embeddings.position_ids",
        "warning:",
        "http/1.1",
    )
    return any(marker in text for marker in markers)


def _looks_like_mixed_user_assistant_artifact(text: str) -> bool:
    if not text.startswith("user:"):
        return False

    artifact_markers = (
        "\nassistant:",
        " as an ai, i don't have personal experiences or memories.",
        "\ni understand you're",
        "\nlet's dive into",
        "\ni'm here to support you",
    )
    return any(marker in text for marker in artifact_markers)


def _looks_like_question(text: str) -> bool:
    markers = (
        "?",
        "what ",
        "why ",
        "how ",
        "could you",
        "can you",
        "would you",
        "should i",
        "do i",
    )
    return any(marker in text for marker in markers)


def _looks_like_instructional_answer(text: str) -> bool:
    markers = (
        "here's",
        "here is",
        "replace",
        "run this",
        "commit",
        "next step",
        "do this",
    )
    return any(marker in text for marker in markers)


def _looks_like_experience_or_status(text: str) -> bool:
    markers = (
        "i am",
        "i'm",
        "i was",
        "i have",
        "i've",
        "i feel",
        "i felt",
        "today",
        "yesterday",
        "this week",
        "lately",
        "worked on",
        "started",
        "finished",
        "noticed",
        "experiencing",
        "having",
        "trying",
    )
    return any(marker in text for marker in markers)


def _normalize_text(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text
