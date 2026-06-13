import json
from datetime import datetime, timezone
from pathlib import Path

from src.core.jsonio import safe_read_json
from src.ingest.models import NormalizedDocument


def _normalize_chatgpt_timestamp(create_time) -> str | None:
    """Convert ChatGPT's create_time (Unix epoch float) to the vault's
    canonical ISO 8601 form ``YYYY-MM-DDTHH-MM-SS`` (hyphenated time).

    Fix 4 (2026-04-27): previously stored as ``str(create_time)`` — the
    raw epoch string ("1715284775.822009"). The renderer in
    src/llm/prompt_builder.py:_format_item_age tried to parse this as ISO,
    failed, and returned an empty string — so age hedges silently
    disappeared on imported records. Returning ISO here makes the renderer
    do the right thing for new imports; the renderer also gains an
    epoch-string fallback for records that were imported before this fix.
    """
    if create_time is None:
        return None
    try:
        epoch = float(create_time)
    except (TypeError, ValueError):
        # Already a string in some other format — pass through unchanged
        # so the renderer can decide.
        return str(create_time)
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    except (OSError, OverflowError, ValueError):
        return None


def _flatten_part(part):
    if isinstance(part, str):
        return part

    if isinstance(part, dict):
        if "text" in part and isinstance(part["text"], str):
            return part["text"]

        if "content" in part and isinstance(part["content"], str):
            return part["content"]

        return json.dumps(part, ensure_ascii=False)

    return str(part)


def load_chatgpt_export(folder_path: str):
    folder = Path(folder_path)
    docs = []

    for file in folder.glob("conversations-*.json"):
        # No default: a corrupt export must fail loudly (JsonIoError) rather
        # than silently importing zero conversations and looking successful.
        data = safe_read_json(file)

        for i, convo in enumerate(data):
            title = convo.get("title", f"chat_{file.stem}_{i}")
            created = convo.get("create_time")
            mapping = convo.get("mapping", {})

            messages = []
            roles = []

            for node in mapping.values():
                msg = node.get("message")
                if not msg:
                    continue

                author = msg.get("author", {}).get("role", "unknown")
                parts = msg.get("content", {}).get("parts", [])

                if not parts:
                    continue

                text = "\n".join(_flatten_part(p) for p in parts).strip()

                if not text:
                    continue

                messages.append(f"{author.title()}: {text}")
                # Task #25: store per-message role from the ChatGPT JSON
                # source of truth. The chunker can use this directly
                # instead of re-detecting from the text prefix.
                roles.append(author)

            if not messages:
                continue

            docs.append(
                NormalizedDocument(
                    source="chatgpt",
                    doc_id=f"{file.stem}_{i}",
                    title=title,
                    created_at=_normalize_chatgpt_timestamp(created),
                    content="\n\n".join(messages),
                    metadata={
                        "type": "chatgpt_export",
                        "file": file.name,
                        "messages": messages,
                        "roles": roles,
                    },
                )
            )

    return docs