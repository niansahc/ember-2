import logging
from datetime import datetime
from pathlib import Path

from src.context.models import ContextPacket
from src.context.conversation_buffer import ConversationBuffer
from src.safety.nature_loader import NatureLoader

logger = logging.getLogger("ember.prompt_builder")


class PromptBuilder:
    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8").strip()

        self.conversation_buffer = ConversationBuffer()

        # Nature loader — singleton, loaded once at startup.
        # Nature block is injected into the context packet every turn
        # (not the system prompt) per ADR-016 persona stability research.
        try:
            self._nature_loader = NatureLoader()
            self._nature_loader.load()
        except Exception as exc:
            logger.warning("[PROMPT] Could not load nature document: %s", exc)
            self._nature_loader = None

    def build_prompt(self, context_packet: ContextPacket, style: str = "balanced") -> str:
        # Section order matches ADR-016 context assembly order:
        # system prompt → date → style → nature → state → tasks →
        # capabilities → reflections → web → memory → conversation →
        # instruction rules → user query
        sections: list[str] = [
            self.system_prompt,
            self._build_date_section(),
            self._build_style_section(style),
            self._build_nature_section(),
            self._build_state_section(context_packet),
            self._build_task_section(context_packet),
            self._build_capabilities_section(),
            self._build_reflection_section(context_packet),
            self._build_web_search_section(context_packet),
            self._build_context_section(context_packet),
            self._build_conversation_section(),
            self._build_instruction_section(),
            self._build_user_section(context_packet),
        ]

        return "\n\n".join(section for section in sections if section.strip())

    def _build_date_section(self) -> str:
        """Inject current date and time of day for temporal grounding."""
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 12:
            time_of_day = "morning"
        elif 12 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 21:
            time_of_day = "evening"
        else:
            time_of_day = "late night"
        return f"It's {now.strftime('%A')} {time_of_day}, {now.strftime('%B %d, %Y')}."

    STYLE_INSTRUCTIONS = {
        "casual": (
            "CONVERSATIONAL STYLE: CASUAL\n"
            "Respond conversationally. Keep responses concise and informal. "
            "Favor short answers and natural back-and-forth over long explanations. "
            "Skip unnecessary preamble."
        ),
        "thoughtful": (
            "CONVERSATIONAL STYLE: THOUGHTFUL\n"
            "Take a considered approach. Provide fuller context and reasoning "
            "where relevant. Responses may be longer when the topic warrants it. "
            "Prioritize depth and clarity over brevity."
        ),
    }

    def _build_style_section(self, style: str) -> str:
        """
        Inject a conversational style instruction based on user preference.

        "balanced" (default) injects nothing — current behavior is already balanced.
        Unknown values fall back to balanced (no injection).
        """
        return self.STYLE_INSTRUCTIONS.get(style, "")

    def _build_nature_section(self) -> str:
        """
        Render Ember's nature block for context packet injection.

        Injected every turn so nature tokens are always recent — not subject
        to attention dilution in the system prompt (ADR-016, PRISM/PERSIST).
        """
        if self._nature_loader is None:
            return ""
        try:
            return self._nature_loader.to_prompt_text()
        except Exception:
            return ""

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

    def _build_task_section(self, context_packet: ContextPacket) -> str:
        """
        Render active tasks into the ACTIVE TASKS section of the prompt.

        Format: - [status] title
                - [status] title (priority: high)

        If no active tasks, shows "None." so the model always sees the header.
        """
        if not context_packet.task_items:
            return "ACTIVE TASKS:\nNone."

        lines: list[str] = []

        for item in context_packet.task_items:
            if item.priority:
                lines.append(
                    f"- [{item.status}] {item.title} (priority: {item.priority})"
                )
            else:
                lines.append(f"- [{item.status}] {item.title}")

        return "ACTIVE TASKS:\n" + "\n".join(lines)

    def _build_capabilities_section(self) -> str:
        """Inject capability statements so Ember knows what she can do."""
        return (
            "CAPABILITIES:\n"
            "You can create tasks directly in the user's task list. "
            "When the user asks you to create, add, track, or remind them of something, create the task. "
            "Tasks you create appear in the sidebar immediately.\n"
            "You can create single tasks or multiple tasks in one message.\n"
            "Tasks are stored in the user's local vault. You have write access. "
            "Do not tell the user to add tasks themselves.\n"
            "If you have created tasks, confirm what was created. "
            "Do not confirm task creation unless the write actually succeeded."
        )

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
            return (
                "MEMORY CONTEXT:\n"
                "No relevant memory found for this query. "
                "Answer from your own knowledge and acknowledge if you are uncertain."
            )

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
