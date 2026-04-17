import json
from pathlib import Path

from src.ingest.models import NormalizedDocument


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
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)

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
                    created_at=str(created) if created else None,
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