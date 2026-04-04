from __future__ import annotations

import re

from src.memory.service import MemoryService


memory_service = MemoryService()


def generate_reflection(
    memory_types: list[str] | str = "journal",
    limit: int = 50,
    store: bool = True,
    cadence: str = "daily",
    prompt_template: str | None = None,
):
    # backwards compat: single string wrapped in list
    if isinstance(memory_types, str):
        memory_types = [memory_types]

    # read from all sources and pool before scoring
    all_memories: list[dict] = []
    for memory_type in memory_types:
        all_memories.extend(memory_service.read(memory_type=memory_type, limit=limit * 3))

    candidates = []
    seen = set()

    for memory in all_memories:
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
                "source": memory.get("source", memory.get("type", "unknown")),
                "title": memory.get("title") or memory.get("metadata", {}).get("title"),
            }
        )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = _select_diverse_candidates(candidates, limit=8)

    source_label = ", ".join(memory_types)

    if not selected:
        return {
            "summary": "No memories available for reflection.",
            "memory_count": 0,
            "source_type": source_label,
        }

    selected_texts = [item["text"] for item in selected]

    if prompt_template:
        # LLM synthesis path — format the template and call the model
        summary = _llm_synthesize(selected, prompt_template, source_label)
    else:
        # Legacy concatenation path (daily/weekly)
        combined_text = " | ".join(selected_texts)
        summary = f"Recent themes: {combined_text[:1000]}"

    reflection = {
        "summary": summary,
        "memory_count": len(selected),
        "source_type": source_label,
    }

    if store:
        memory_service.write(
            text=summary,
            memory_type="reflection",
            source="reflection_engine",
            tags=["reflection", cadence],
            metadata={
                "cadence": cadence,
                "source_type": source_label,
                "memory_count": len(selected),
            },
        )

    return reflection


def _llm_synthesize(selected: list[dict], prompt_template: str, source_label: str) -> str:
    """
    Format selected records into the prompt template and call the LLM
    for synthesis. Used by monthly reflection (ADR-016 prompt standards).

    Records are shuffled to counteract recency bias before formatting.
    """
    import random
    import ollama
    from src.core.config import get_ember_model

    # Shuffle to counteract recency bias (CLAUDE.md prompt writing standards)
    shuffled = list(selected)
    random.shuffle(shuffled)

    # Format records for the prompt
    record_lines = []
    for item in shuffled:
        source = item.get("source", "unknown")
        timestamp = item.get("timestamp", "")
        text = item["text"]
        record_lines.append(f"[{source} | {timestamp}] {text}")

    records_text = "\n\n".join(record_lines)

    # Compute window dates
    timestamps = [item.get("timestamp", "") for item in selected if item.get("timestamp")]
    sorted_ts = sorted(timestamps)
    window_start = sorted_ts[0][:10] if sorted_ts else "unknown"
    window_end = sorted_ts[-1][:10] if sorted_ts else "unknown"

    # Format the prompt
    prompt = prompt_template.format(
        record_count=len(selected),
        window_start=window_start,
        window_end=window_end,
        source_types=source_label,
        records=records_text,
    )

    # Call the LLM
    try:
        response = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.4, "num_predict": 800},
        )
        return response["message"]["content"].strip()
    except Exception as exc:
        # Fallback to concatenation if LLM fails
        combined = " | ".join(item["text"] for item in selected)
        return f"Monthly synthesis failed ({exc}). Records: {combined[:800]}"


def _select_diverse_candidates(
    candidates: list,
    limit: int,
    jaccard_threshold: float = 0.3,
) -> list:
    """
    Select up to `limit` candidates, skipping any whose token overlap
    (Jaccard similarity) with an already-selected candidate exceeds
    jaccard_threshold.

    Candidates must already be sorted by descending score. Higher-scoring
    candidates claim their slot first; near-duplicates that follow are
    skipped rather than penalised, preventing repetitive content from
    filling the top-8 even when it scores uniformly.
    """
    selected = []
    selected_token_sets = []

    for candidate in candidates:
        tokens = set(re.findall(r"\b[a-z0-9]{3,}\b", candidate["normalized"]))

        too_similar = False
        for existing_tokens in selected_token_sets:
            union = tokens | existing_tokens
            if union and len(tokens & existing_tokens) / len(union) > jaccard_threshold:
                too_similar = True
                break

        if too_similar:
            continue

        selected.append(candidate)
        selected_token_sets.append(tokens)

        if len(selected) >= limit:
            break

    return selected


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

    # Only grant experience bonus for substantive texts — short repetitive
    # instructions like "Shorter messages please. I've reminded you 5 times"
    # match "i've" but are not meaningful experiences.
    if len(normalized_text) > 100 and _looks_like_concrete_experience(normalized_text):
        score += 0.4

    if _looks_like_question(normalized_text):
        score -= 0.2

    if len(normalized_text) < 40:
        score -= 0.5
    elif len(normalized_text) > 1500:
        score -= 0.2

    # Small quality bonus for longer substantive content.
    if len(normalized_text) > 200:
        score += 0.15

    return score


def _should_skip_for_reflection(text: str) -> bool:
    if not text:
        return True

    skip_markers = (
        "uvicorn src.api.main:app --reload",
        "info:",
        "traceback",
        "file \"c:\\",
        ", line ",
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
        "shorter messages please",
        "shorter responses",
        "that's a long response",
        # Assistant filler that leaked into reflections
        "there is no earlier conversation",
        "no conversation summary",
        "i understand you're seeking",
        "i understand you are seeking",
        "seeking clarity",
        "response length",
        "let me clarify",
        "what would you like to discuss",
        "i'm here to support",
        "i'm here to help",
        "how can i assist",
    )

    if any(marker in text for marker in skip_markers):
        return True

    # File trees and directory listings use Unicode box-drawing characters.
    # These are never meaningful reflection content.
    if "├──" in text or "│" in text:
        return True

    # Short URL-only or URL-leading content is infrastructure noise.
    if "https://" in text and len(text) < 200:
        return True

    # Multi-turn exchanges embedded in a single record are not meaningful
    # as individual reflections — skip if a second speaker appears.
    if text.startswith("user:"):
        tail = text[len("user:"):]
        if "user:" in tail or "assistant:" in tail:
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

    # Short assistant filler starting with "I " + known filler patterns.
    # Not all "i " starts are filler — "i worked on the pipeline today" is real.
    # Only skip when the opening matches known assistant-voice patterns.
    if text.startswith("i ") and len(text) < 100:
        filler_starts = (
            "i can ", "i would ", "i think we ", "i'm happy to", "i'd be happy",
            "i understand ", "i appreciate ", "i see that", "i notice that",
            "i can help", "i'd suggest", "i'd recommend",
        )
        if any(text.startswith(s) for s in filler_starts):
            return True

    # Assistant voice detection: if more than 2 sentences start with "I ",
    # this is likely assistant-generated filler, not user experience content.
    sentences = re.split(r"[.!?]\s+", text)
    i_sentences = sum(1 for s in sentences if s.strip().startswith("i ") or s.strip().startswith("I "))
    if i_sentences > 2 and len(sentences) > 0 and i_sentences / len(sentences) > 0.5:
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
