from datetime import datetime
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
            self._build_date_section(),
            self._build_state_section(context_packet),
            self._build_reflection_section(context_packet),
            self._build_web_search_section(context_packet),
            self._build_context_section(context_packet),
            self._build_conversation_section(),
            self._build_instruction_section(),
            self._build_user_section(context_packet),
        ]

        return "\n\n".join(section for section in sections if section.strip())

    def _build_date_section(self) -> str:
        """Inject current date for temporal grounding."""
        return f"TODAY: {datetime.now().strftime('%A, %B %d, %Y')}."

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
            "4. MEMORY CONTEXT is secondary and must not override recent conversation.\n\n"
            "BEHAVIOR RULES:\n"
            "- If no prior conversation exists, answer normally.\n"
            "- Resolve references like 'that', 'those', and 'it' from the last assistant answer when possible.\n"
            "- Do not ask for clarification if the reference is reasonably clear from recent conversation.\n"
            "- Do not invent prior context.\n"
            "- Do not introduce new topics that were not present in the recent exchange unless the user asks for them.\n"
            "- Only use memory if it directly supports the current question.\n"
            "- If memory conflicts with recent conversation, trust recent conversation.\n"
            "- If WEB SEARCH RESULTS are present, use them as your primary source and include the relevant source URL(s) naturally in your response.\n"
            "- When asked about yourself, answer as Ember using your system prompt identity. The MEMORY CONTEXT describes the person you are talking to, not yourself.\n"
        )

    def _build_web_search_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.web_items:
            return ""

        lines = []
        for i, item in enumerate(context_packet.web_items, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("snippet", "")
            lines.append(f"[{i}] {title}\n    {url}\n    {snippet}")

        return "WEB SEARCH RESULTS:\n" + "\n\n".join(lines)

    def _build_context_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.memory_items:
            return "MEMORY CONTEXT:\nNone relevant."

        profile_items = [i for i in context_packet.memory_items if i.memory_type == "profile"]
        other_items = [i for i in context_packet.memory_items if i.memory_type != "profile"][:4]

        sections: list[str] = []

        if profile_items:
            profile_lines = "\n\n".join(f"- {item.content.strip()}" for item in profile_items)
            sections.append(
                "[Context about the person Ember is talking to — this is who Ember knows, not who Ember is:]\n" + profile_lines
            )

        if other_items:
            other_lines = []
            for item in other_items:
                content = item.content.strip()
                metadata = getattr(item, "metadata", {}) or {}
                role = metadata.get("role", "")
                date_str = self._format_item_date(item.timestamp)

                # Label conversation turns by role so the model knows whose words are whose.
                # This prevents assistant self-echo: without labels, Ember attributes
                # her own prior responses back to the user as things "you said."
                if item.item_type == "conversation" and role == "user":
                    label = f"[you said{date_str}]"
                elif item.item_type == "conversation" and role == "assistant":
                    label = f"[Ember said{date_str}]"
                else:
                    label = f"({item.item_type}{date_str})"

                other_lines.append(f"- {label} {content}")

            sections.append("[Context:]\n" + "\n\n".join(other_lines))

        return "MEMORY CONTEXT:\n" + "\n\n".join(sections)

    @staticmethod
    def _format_item_date(timestamp: str | None) -> str:
        """Format a timestamp into a short date string for context labels.
        Returns ', Mar 27' or '' if no parseable timestamp."""
        if not timestamp:
            return ""
        try:
            # Handle Ember's hyphenated timestamps: 2026-03-27T15-18-02
            clean = timestamp.replace("Z", "").split("+")[0]
            if "T" in clean:
                date_part = clean.split("T")[0]
            else:
                date_part = clean[:10]
            dt = datetime.strptime(date_part, "%Y-%m-%d")
            return f", {dt.strftime('%b %d')}"
        except (ValueError, TypeError):
            return ""

    def _build_reflection_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.reflection_items:
            return "REFLECTION CONTEXT:\nNone relevant."

        lines: list[str] = []

        for item in context_packet.reflection_items[:1]:
            lines.append(f"- {item.content.strip()}")

        return "REFLECTION CONTEXT:\n" + "\n\n".join(lines)
