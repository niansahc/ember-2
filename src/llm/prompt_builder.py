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
        sections.append(self._build_user_section(context_packet))  # MOVE UP
        sections.append(self._build_instruction_section())         # MOVE UP
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
            "1. RECENT CONVERSATION is the primary source of truth.\n"
            "2. USER MESSAGE must be answered directly.\n"
            "3. MEMORY CONTEXT is secondary and must NOT override conversation.\n\n"
            "BEHAVIOR RULES:\n"
            "- Resolve references like 'that', 'those', 'it' using RECENT CONVERSATION.\n"
            "- NEVER invent prior context.\n"
            "- NEVER assume topics unless explicitly stated in conversation.\n"
            "- Do NOT pull in unrelated memory.\n"
            "- Only use memory if it directly supports the current question.\n"
            "- If memory conflicts with conversation, IGNORE memory.\n"
        )

    def _build_context_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.memory_items:
            return "MEMORY CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.memory_items[:4]:  # REDUCED
            lines.append(
                f"- ({item.item_type}) {item.content.strip()}"
            )

        return "MEMORY CONTEXT:\n" + "\n\n".join(lines)

    def _build_reflection_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.reflection_items:
            return "REFLECTION CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.reflection_items[:1]:  # REDUCED
            lines.append(f"- {item.content.strip()}")

        return "REFLECTION CONTEXT:\n" + "\n\n".join(lines)