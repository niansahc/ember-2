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
        # Section order matches TDD context packet order:
        # system prompt → state → reflections → source memories →
        # recent conversation → instruction rules → user query
        sections: list[str] = [
            self.system_prompt,
            self._build_state_section(context_packet),
            self._build_reflection_section(context_packet),
            self._build_context_section(context_packet),
            self._build_conversation_section(),
            self._build_instruction_section(),
            self._build_user_section(context_packet),
        ]

        return "\n\n".join(section for section in sections if section.strip())

    def _build_state_section(self, context_packet: ContextPacket) -> str:
        """
        Render current state items into the STATE section of the prompt.

        Each item is formatted as:
          - [category] text
          - [category] text (priority: high)

        If no state items are present, returns a "None active" placeholder
        so the model always sees the section header.
        """
        if not context_packet.state_items:
            return "CURRENT STATE:\nNone active."

        lines: list[str] = []

        for item in context_packet.state_items:
            if item.priority:
                lines.append(
                    f"- [{item.category}] {item.text.strip()} (priority: {item.priority})"
                )
            else:
                lines.append(f"- [{item.category}] {item.text.strip()}")

        return "CURRENT STATE:\n" + "\n".join(lines)

    def _build_conversation_section(self) -> str:
        turns = self.conversation_buffer.get_recent()

        if not turns:
            return "RECENT CONVERSATION:\nNone"

        lines: list[str] = []
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
            "2. Use the most recent assistant response as the primary reference for follow-up questions.\n"
            "3. Use RECENT CONVERSATION for continuity.\n"
            "4. BACKGROUND CONTEXT is secondary and must not override recent conversation.\n"
            "5. Synthesize background context naturally into responses — never enumerate, list, or recite it back to the user.\n\n"
            "BEHAVIOR RULES:\n"
            "- If no prior conversation exists, answer normally.\n"
            "- Resolve references like 'that', 'those', and 'it' from the last assistant answer when possible.\n"
            "- Do not ask for clarification if the reference is reasonably clear from recent conversation.\n"
            "- Do not invent prior context.\n"
            "- Do not introduce new topics that were not present in the recent exchange unless the user asks for them.\n"
            "- Only use memory if it directly supports the current question.\n"
            "- If memory conflicts with recent conversation, trust recent conversation.\n"
        )

    def _build_context_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.memory_items:
            return ""

        lines: list[str] = []

        for item in context_packet.memory_items[:4]:
            lines.append(item.content.strip())

        return "BACKGROUND CONTEXT (use to inform responses, do not recite):\n" + "\n\n".join(lines)

    def _build_reflection_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.reflection_items:
            return "REFLECTION CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.reflection_items[:1]:
            lines.append(f"- {item.content.strip()}")

        return "REFLECTION CONTEXT:\n" + "\n\n".join(lines)
