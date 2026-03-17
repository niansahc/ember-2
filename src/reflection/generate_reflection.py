from __future__ import annotations

import re

from src.memory.service import MemoryService


memory_service = MemoryService()


def generate_reflection(
    memory_type: str = "journal",
    limit: int = 50,
    store: bool = True,
    cadence: str = "daily",
):
    memories = memory_service.read(memory_type=memory_type, limit=limit * 3)

    candidates = []
    seen = set()

    for memory in memories:
        text = _extract_memory_text(memory).strip()
        if not text:
            continue

        normalized = _normalize_text(text)
        if not normalized or normalized in seen:
            continue

        if _should_skip_for_reflection(normalized):
            continue

        score = _reflection_priority_score(memory, normalized)
        if score <= 0:
            continue

        seen.add(normalized)
        candidates.append(
            {
                "text": text,
                "normalized": normalized,
                "score": score,
                "timestamp": memory.get("timestamp") or memory.get("created_at"),
                "source": memory.get("source", memory_type),
                "title": memory.get("title") or memory.get("metadata", {}).get("title"),
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = candidates[:8]

    if not selected:
        return {
            "summary": "No memories available for reflection.",
            "memory_count": 0,
            "source_type": memory_type,
        }

    selected_texts = [item["text"] for item in selected]
    combined_text = " | ".join(selected_texts)

    summary = f"Recent themes: {combined_text[:1000]}"

    reflection = {
        "summary": summary,
        "memory_count": len(selected),
        "source_type": memory_type,
    }

    if store:
        memory_service.write(
            text=summary,
            memory_type="reflection",
            source="reflection_engine",
            tags=["reflection", cadence],
            metadata={
                "cadence": cadence,
                "source_type": memory_type,
                "memory_count": len(selected),
            },
        )

    return reflection


def _extract_memory_text(memory: dict) -> str:
    if not isinstance(memory, dict):
        return ""

    if memory.get("text"):
        return str(memory["text"])

    if memory.get("content"):
        return str(memory["content"])

    metadata = memory.get("metadata", {})
    if isinstance(metadata, dict):
        if metadata.get("text"):
            return str(metadata["text"])
        if metadata.get("content"):
            return str(metadata["content"])

    return ""


def _reflection_priority_score(memory: dict, normalized_text: str) -> float:
    score = 1.0

    metadata = memory.get("metadata", {}) if isinstance(memory.get("metadata"), dict) else {}
    role = memory.get("role") or metadata.get("role")
    content_kind = memory.get("content_kind") or metadata.get("content_kind")

    if role == "user":
        score += 0.5
    elif role == "assistant":
        score -= 0.4
    elif role in {"tool", "system"}:
        score -= 1.0

    if content_kind == "experience":
        score += 0.6
    elif content_kind == "user_content":
        score += 0.2
    elif content_kind == "question":
        score -= 0.3
    elif content_kind == "answer":
        score -= 0.1

    if _looks_like_concrete_experience(normalized_text):
        score += 0.4

    if _looks_like_question(normalized_text):
        score -= 0.2

    if len(normalized_text) < 40:
        score -= 0.5
    elif len(normalized_text) > 1500:
        score -= 0.2

    return score


def _should_skip_for_reflection(text: str) -> bool:
    if not text:
        return True

    skip_markers = (
        "uvicorn src.api.main:app --reload",
        "info:",
        "traceback",
        "file \"c:\\",
        "line ",
        "module>",
        "(.venv)",
        "powershell",
        "ps c:\\",
        "warning:",
        "loading weights:",
        "bertmodel load report",
        "embeddings.position_ids",
        "press ctrl+c to quit",
        "application startup complete",
        "started server process",
        "started reloader process",
        "waiting for application startup",
    )

    if any(marker in text for marker in skip_markers):
        return True

    if text.startswith("assistant:") and _looks_like_code_or_debug(text):
        return True

    if text.startswith("user:") and _looks_like_code_or_debug(text):
        return True

    if "```" in text:
        return True

    if text.startswith("user: from ") or text.startswith("assistant: from "):
        return True

    if text.startswith("user: import ") or text.startswith("assistant: import "):
        return True

    if re.search(r"\bdef\s+\w+\(", text):
        return True

    return False


def _looks_like_code_or_debug(text: str) -> bool:
    markers = (
        "import ",
        "from src.",
        "def ",
        "class ",
        "return ",
        "traceback",
        "file \"",
        "line ",
        "uvicorn",
        "http/1.1",
        "ps c:\\",
        "(.venv)",
    )
    return any(marker in text for marker in markers)


def _looks_like_concrete_experience(text: str) -> bool:
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


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())