from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json

from src.context.service import ContextService


@dataclass
class EvalCase:
    name: str
    query: str


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        name="reflective_patterns",
        query="What patterns have you noticed lately?",
    ),
    EvalCase(
        name="recent_work",
        query="What have I been working on recently?",
    ),
    EvalCase(
        name="timeline_recent",
        query="What happened in the last few days of Ember-2 work?",
    ),
    EvalCase(
        name="architecture_reflection",
        query="What does the architecture say about reflections?",
    ),
    EvalCase(
        name="current_focus",
        query="What am I actively working on?",
    ),
]


def clean_context_packet(packet_dict: dict) -> dict:
    for section in ["memory_items", "reflection_items"]:
        for item in packet_dict.get(section, []):
            metadata = item.get("metadata", {})
            metadata.pop("embedding", None)
            metadata.pop("file_path", None)
    return packet_dict


def run_eval(output_path: str = "logs/retrieval_eval/latest.json") -> Path:
    context_service = ContextService()
    results: list[dict] = []

    for case in EVAL_CASES:
        packet = context_service.build_context(case.query)
        packet_dict = clean_context_packet(asdict(packet))

        results.append(
            {
                "name": case.name,
                "query": case.query,
                "memory_count": len(packet.memory_items),
                "reflection_count": len(packet.reflection_items),
                "context_packet": packet_dict,
            }
        )

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_file


if __name__ == "__main__":
    path = run_eval()
    print(f"Wrote retrieval eval to: {path}")