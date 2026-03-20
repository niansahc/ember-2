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
        sections.append(self._build_user_section(context_packet))
        sections.append(self._build_instruction_section())
        sections.append(self._build_context_section(context_packet))
        sections.append(self._build_reflection_section(context_packet))

        return "\n\n".join(section for section in sections if section.strip())

    def _build_conversation_section(self) -> str:
        turns = self.conversation_buffer.get_recent()

        if not turns:
            return "RECENT CONVERSATION:\nNone"

        lines = []
        for i, turn in enumerate(turns, 1):
            lines.append(f"[Turn {i} | User] {turn['user']}")
            lines.append(f"[Turn {i} | Assistant] {turn['assistant']}")

        return "RECENT CONVERSATION:\n" + "\n".join(lines)

    def _build_user_section(self, context_packet: ContextPacket) -> str:
        return f"USER MESSAGE:\n{context_packet.user_message}"

    def _build_instruction_section(self) -> str:
        return (
            "CONTEXT PRIORITY RULES:\n"
            "1. Answer the USER MESSAGE directly.\n"
            "2. Use RECENT CONVERSATION for continuity when available.\n"
            "3. MEMORY CONTEXT is optional and should not override conversation.\n\n"
            "BEHAVIOR RULES:\n"
            "- If no relevant conversation exists, answer normally.\n"
            "- Resolve references like 'that', 'those', 'it' using RECENT CONVERSATION.\n"
            "- Do NOT ask for clarification if the reference can be resolved.\n"
            "- Do NOT invent prior context.\n"
            "- Only use memory if it directly supports the current question.\n"
        )

    def _build_context_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.memory_items:
            return "MEMORY CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.memory_items[:4]:
            lines.append(
                f"- ({item.item_type}) {item.content.strip()}"
            )

        return "MEMORY CONTEXT:\n" + "\n\n".join(lines)

    def _build_reflection_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.reflection_items:
            return "REFLECTION CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.reflection_items[:1]:
            lines.append(f"- {item.content.strip()}")

        return "REFLECTION CONTEXT:\n" + "\n\n".join(lines)