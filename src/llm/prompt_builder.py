from pathlib import Path

from src.context.models import ContextPacket
from src.context.conversation_buffer import ConversationBuffer


class PromptBuilder:
    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8").strip()

        self.conversation_buffer = ConversationBuffer()

    def build_prompt(self, context_packet: ContextPacket) -> str:
        sections: list[str] = [self.system_prompt]

        sections.append(self._build_conversation_section())
        sections.append(self._build_context_section(context_packet))
        sections.append(self._build_reflection_section(context_packet))
        sections.append(self._build_instruction_section())
        sections.append(self._build_user_section(context_packet))

        return "\n\n".join(section for section in sections if section.strip())

    def _build_conversation_section(self) -> str:
        recent = self.conversation_buffer.format_for_prompt()
        return f"RECENT CONVERSATION:\n{recent}"

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
            "- ALWAYS answer the user's most recent question.\n"
            "- Resolve references like 'that', 'those', 'it' using RECENT CONVERSATION.\n"
            "- Do NOT ask for clarification if the answer exists in recent conversation.\n"
            "- Treat RECENT CONVERSATION as the primary source for continuity.\n"
            "- Use retrieved context when it is relevant and specific.\n"
            "- Prefer concrete memory evidence over generic assumptions.\n"
            "- If context is weak, mixed, or incomplete, say so plainly.\n"
            "- Do not claim memory support for things that are not actually present in context.\n"
            "- Reflections are helpful summaries, but raw memory items are the primary evidence."
        )

    def _build_user_section(self, context_packet: ContextPacket) -> str:
        return f"USER MESSAGE:\n{context_packet.user_message}"