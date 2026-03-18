from pathlib import Path

from src.context.models import ContextPacket


class PromptBuilder:
    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8").strip()

    def build_prompt(self, context_packet: ContextPacket) -> str:
        sections: list[str] = [self.system_prompt]

        sections.append(self._build_context_section(context_packet))
        sections.append(self._build_reflection_section(context_packet))
        sections.append(self._build_instruction_section())

        return "\n\n".join(section for section in sections if section.strip())

    def _build_context_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.memory_items:
            return "MEMORY CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.memory_items[:6]:
            lines.append(
                f"- ({item.item_type}) {item.content.strip()}"
            )

        return "MEMORY CONTEXT:\n" + "\n\n".join(lines)

    def _build_reflection_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.reflection_items:
            return "REFLECTION CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.reflection_items[:2]:
            lines.append(f"- {item.content.strip()}")

        return "REFLECTION CONTEXT:\n" + "\n\n".join(lines)

    def _build_instruction_section(self) -> str:
        return (
            "CONTEXT USAGE RULES:\n"
            "- Use retrieved context when it is relevant and specific.\n"
            "- Prefer concrete memory evidence over generic assumptions.\n"
            "- If context is weak, mixed, or incomplete, say so plainly.\n"
            "- Do not claim memory support for things that are not actually present in context.\n"
            "- Reflections are helpful summaries, but raw memory items are the primary evidence."
        )