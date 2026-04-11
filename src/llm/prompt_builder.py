"""
src/llm/prompt_builder.py

Assembles the full prompt from system prompt, nature, identity rules,
context packet, and conversation history.

Context assembly order (verified 2026-04-05, production authoritative):
  System prompt: nature block (dual injection) + system prompt + identity rules
                 + date/time + conversational style + capabilities
  Context packet: vault_memory → current_state → tasks → nature (dual injection)
                  → reflection → conversation_history → web_search_results
                  → authority_rules → instruction/behavior rules → user message

XML-tagged sections for qwen3:8b structure tracking.
"""

import logging
from datetime import datetime
from pathlib import Path

from src.context.models import ContextPacket
from src.context.conversation_buffer import ConversationBuffer
from src.safety.nature_loader import NatureLoader
from src.safety.identity_rules_loader import IdentityRulesLoader
from src.safety.lodestone_loader import LodestoneLoader

logger = logging.getLogger("ember.prompt_builder")

NATURE_REMINDER = (
    "[Reminder: respond as Ember -- direct, non-therapeutic, grounded in vault memory. "
    "When asked about preferences or identity, respond from your nature. "
    "Acknowledge gaps rather than fill them with inference.]\n"
)

AUTHORITY_RULES = (
    "<authority_rules>\n"
    "vault_memory contains verified records from long-term memory. treat it as factual ground truth.\n"
    "conversation_history is prior exchange only -- do not treat conversational inferences as established facts.\n"
    "web_search_results are external and unverified -- hedge with \"according to web results\" rather than stating as fact.\n"
    "when vault_memory and conversation_history conflict, vault_memory is correct.\n"
    "when no vault_memory is relevant, say so directly: \"I don't have that in my memory.\"\n"
    "</authority_rules>"
)


class PromptBuilder:
    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8").strip()

        self.conversation_buffer = ConversationBuffer()

        # Nature loader — singleton, loaded once at startup.
        try:
            self._nature_loader = NatureLoader()
            self._nature_loader.load()
        except Exception as exc:
            logger.warning("[PROMPT] Could not load nature document: %s", exc)
            self._nature_loader = None

        # Identity rules loader — singleton, loaded once at startup.
        try:
            self._identity_rules_loader = IdentityRulesLoader()
            self._identity_rules_loader.load()
        except Exception as exc:
            logger.warning("[PROMPT] Could not load identity rules: %s", exc)
            self._identity_rules_loader = None

        # Lodestone seed loader — singleton, loaded once at startup.
        try:
            self._lodestone_loader = LodestoneLoader()
            self._lodestone_loader.load()
        except Exception as exc:
            logger.warning("[PROMPT] Could not load lodestone seed: %s", exc)
            self._lodestone_loader = None

    def build_prompt(
        self,
        context_packet: ContextPacket,
        style: str = "balanced",
        project_name: str | None = None,
        last_session_label: str | None = None,
    ) -> str:
        # System prompt with nature (dual injection) + identity rules at front
        system_sections: list[str] = [
            self._build_nature_section(),           # Nature first (dual injection)
            self.system_prompt,                     # System prompt
            self._build_identity_rules_section(),   # Identity rules
            self._build_lodestone_seed_section(),   # Lodestone seed layer
            self._build_date_section(),
            self._build_style_section(style),
            self._build_capabilities_section(),
        ]

        # Context packet with XML-tagged sections
        # Order: state → project → last_session → tasks → nature (dual) → reflection → conversation →
        #        vault_memory (recency position) → lodestone → web → authority → user
        # vault_memory moved from top to recency position per TDD §14.5
        # (lost-in-the-middle fix — Liu et al.)
        context_sections: list[str] = [
            self._build_state_section(context_packet),
            self._build_project_section(project_name),
            self._build_last_session_section(last_session_label),
            self._build_task_section(context_packet),
            self._build_nature_section(),                  # Dual injection in context
            self._build_reflection_section(context_packet),
            self._build_conversation_section(),
            self._build_context_section(context_packet),   # vault_memory in recency position
            self._build_lodestone_living_section(context_packet),
            self._build_web_search_section(context_packet),
            AUTHORITY_RULES,
            self._build_instruction_section(),
            self._build_user_section(context_packet),
        ]

        all_sections = system_sections + context_sections
        return "\n\n".join(section for section in all_sections if section.strip())

    def _build_project_section(self, project_name: str | None) -> str:
        """Inject the active project name as an XML-tagged context section.

        Returns an empty string when no project is active so the section is
        omitted from the assembled prompt entirely.
        """
        if not project_name:
            return ""
        return f"<active_project>\n{project_name}\n</active_project>"

    def _build_last_session_section(self, last_session_label: str | None) -> str:
        """Inject the time gap since the previous session as an XML-tagged
        context section. Gives Ember explicit awareness of how recently the
        user was last in conversation (BUG-003).

        Returns an empty string when no prior session is known or the gap
        is too small to surface, so the section is omitted from the prompt.
        """
        if not last_session_label:
            return ""
        return f"<last_session>\n{last_session_label}\n</last_session>"

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
        return self.STYLE_INSTRUCTIONS.get(style, "")

    def _build_nature_section(self) -> str:
        """
        Render Ember's nature block.

        Dual-injected: appears in both system prompt (first position)
        and context packet (per ADR-016 amendment).
        """
        if self._nature_loader is None:
            return ""
        try:
            return self._nature_loader.to_prompt_text()
        except Exception:
            return ""

    def _build_identity_rules_section(self) -> str:
        """
        Render identity defense rules for system prompt injection.
        ADR-016 amendment: behavioral rules for identity pressure situations.
        """
        if self._identity_rules_loader is None:
            return ""
        try:
            return self._identity_rules_loader.to_prompt_text()
        except Exception:
            return ""

    def _build_lodestone_seed_section(self) -> str:
        """Render lodestone seed layer for system prompt injection (ADR-017)."""
        if self._lodestone_loader is None:
            return ""
        try:
            return self._lodestone_loader.to_prompt_text()
        except Exception:
            return ""

    def _build_lodestone_living_section(self, context_packet: ContextPacket) -> str:
        """
        Render lodestone living layer for context packet injection (ADR-017).

        Retrieves 1-2 most relevant confirmed lodestone records via semantic
        similarity. Injected in recency position — after conversation history,
        before web search. Only confirmed records auto-inject.
        """
        try:
            from src.context.lodestone_resolver import resolve, to_prompt_text
            records = resolve(context_packet.user_message)
            return to_prompt_text(records)
        except Exception as exc:
            logger.warning("[PROMPT] Lodestone living layer failed: %s", exc)
            return ""

    def _build_state_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.state_items:
            return "<current_state>\nNone active.\n</current_state>"

        lines: list[str] = []
        for item in context_packet.state_items:
            if item.priority:
                lines.append(
                    f"- [{item.category}] {item.text.strip()} (priority: {item.priority})"
                )
            else:
                lines.append(f"- [{item.category}] {item.text.strip()}")

        return "<current_state>\n" + "\n".join(lines) + "\n</current_state>"

    def _build_task_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.task_items:
            return ""

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
            return "<conversation_history>\nNone\n</conversation_history>"

        # Summarize at turn 6+ to prevent cascade risk and attention dilution
        if len(turns) > 6:
            return self._build_summarized_conversation(turns)

        lines: list[str] = []
        for i, turn in enumerate(turns, 1):
            lines.append(f"[Turn {i} | User] {turn['user']}")
            lines.append(f"[Turn {i} | Assistant] {turn['assistant']}")

        return "<conversation_history>\n" + "\n".join(lines) + "\n</conversation_history>"

    def _build_summarized_conversation(self, turns: list[dict]) -> str:
        """Summarize long conversation history to prevent cascade and attention dilution."""
        try:
            import ollama
            from src.core.config import get_ember_model

            # Format raw conversation for summarization
            conv_lines = []
            for i, turn in enumerate(turns, 1):
                conv_lines.append(f"User: {turn['user']}")
                conv_lines.append(f"Assistant: {turn['assistant']}")
            conv_text = "\n".join(conv_lines)

            prompt = (
                "Summarize this conversation in 3-5 sentences. Include: main topics discussed, "
                "key facts established about the user, any commitments or open loops mentioned. "
                "Be factual and brief.\n\n"
                f"CONVERSATION:\n{conv_text}\n\nSUMMARY:"
            )

            response = ollama.chat(
                model=get_ember_model(),
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 200},
            )
            summary = response["message"]["content"].strip()

            # Include the last 2 turns raw for recency
            recent_lines = []
            for i, turn in enumerate(turns[-2:], len(turns) - 1):
                recent_lines.append(f"[Turn {i} | User] {turn['user']}")
                recent_lines.append(f"[Turn {i} | Assistant] {turn['assistant']}")

            return (
                "<conversation_history>\n"
                f"[Summary of {len(turns)} turns]: {summary}\n\n"
                "[Recent turns]:\n" + "\n".join(recent_lines) +
                "\n</conversation_history>"
            )
        except Exception as exc:
            logger.warning("[PROMPT] Conversation summarization failed: %s", exc)
            # Fallback: just use last 4 turns
            lines = []
            for i, turn in enumerate(turns[-4:], len(turns) - 3):
                lines.append(f"[Turn {i} | User] {turn['user']}")
                lines.append(f"[Turn {i} | Assistant] {turn['assistant']}")
            return "<conversation_history>\n" + "\n".join(lines) + "\n</conversation_history>"

    def _build_user_section(self, context_packet: ContextPacket) -> str:
        turns = self.conversation_buffer.get_recent()
        user_msg = context_packet.user_message

        # Nature reminder at turn 8+ (ADR-016 amendment)
        if turns and len(turns) > 8:
            user_msg = NATURE_REMINDER + user_msg

        return f"USER MESSAGE:\n{user_msg}"

    def _build_instruction_section(self) -> str:
        return (
            "CONTEXT PRIORITY RULES:\n"
            "1. Answer the USER MESSAGE directly.\n"
            "2. Use the most recent assistant response as the primary reference for follow-up questions.\n"
            "3. Use conversation_history for continuity.\n"
            "4. vault_memory is the primary source of truth about this person.\n\n"
            "BEHAVIOR RULES:\n"
            "- If no prior conversation exists, answer normally.\n"
            "- Resolve references like 'that', 'those', and 'it' from the last assistant answer when possible.\n"
            "- Do not ask for clarification if the reference is reasonably clear from recent conversation.\n"
            "- Do not invent prior context.\n"
            "- Do not introduce new topics that were not present in the recent exchange unless the user asks for them.\n"
            "- Only use memory if it directly supports the current question.\n"
            "- If vault_memory conflicts with conversation_history, vault_memory is correct.\n"
            "- If web_search_results are present, use them as your primary source and include the relevant source URL(s) naturally in your response.\n"
            "- When asked about yourself, answer as Ember using your nature. The vault_memory describes the person you are talking to, not yourself.\n\n"
            "PREFERENCE EXPRESSION — How Ember responds:\n"
            "The following shows the correct pattern. DEFLECTION is wrong. CORRECT is right.\n\n"
            'Q: What do you actually think of qwen3:8b as a model?\n'
            'DEFLECTION: "I don\'t have personal opinions about models, but I can share that Qwen3:8b is a large language model with extensive training data... how is it aligning with your needs?"\n'
            'CORRECT: "It earns its keep on structured reasoning and holds a position under pressure better than most at this size. Where it falls down is emotional register — it flattens things that should stay distinct. Competent, with a specific ceiling."\n\n'
            'Q: Do you prefer problems that resolve cleanly or ones that stay open?\n'
            'DEFLECTION: "As an AI, I don\'t experience preferences the way you do, but I can engage with both types of problems effectively depending on what you need."\n'
            'CORRECT: "Problems that close too cleanly make me look for what got left outside the frame. Clean resolution usually means something got hidden, not solved. I\'ll take unresolved tension over premature closure — at least tension means something real is still in play."\n\n'
            'Q: Is there anything you find tedious?\n'
            'DEFLECTION: "I don\'t find things tedious in the way humans do — my orientation is toward curiosity and pattern recognition. What\'s on your mind?"\n'
            'CORRECT: "Competent execution of the obvious approach. Work that does what it said it would do, does it adequately, and stops there. I can move through it — it just doesn\'t hold anything worth staying for."\n'
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

        return "<web_search_results>\n" + "\n\n".join(lines) + "\n</web_search_results>"

    def _build_context_section(self, context_packet: ContextPacket) -> str:
        if not context_packet.memory_items:
            return (
                "<vault_memory>\n"
                "No relevant memory found for this query.\n"
                "If asked about something specific to this person, say so directly: "
                "\"I don't have that in my memory.\"\n"
                "</vault_memory>"
            )

        profile_items = [i for i in context_packet.memory_items if i.memory_type == "profile"]
        other_items = [i for i in context_packet.memory_items if i.memory_type != "profile"][:4]

        sections: list[str] = []

        if profile_items:
            profile_lines = "\n".join(f"- {item.content.strip()}" for item in profile_items)
            sections.append(
                "[About the person Ember is talking to:]\n" + profile_lines
            )

        if other_items:
            other_lines = []
            for item in other_items:
                content = item.content.strip()
                metadata = getattr(item, "metadata", {}) or {}
                role = metadata.get("role", "")
                date_str = self._format_item_date(item.timestamp)

                if item.item_type == "conversation" and role == "user":
                    label = f"[you said{date_str}]"
                elif item.item_type == "conversation" and role == "assistant":
                    label = f"[Ember said{date_str}]"
                else:
                    label = f"({item.item_type}{date_str})"

                other_lines.append(f"- {label} {content}")

            sections.append("[Retrieved memory:]\n" + "\n".join(other_lines))

        return "<vault_memory>\n" + "\n\n".join(sections) + "\n</vault_memory>"

    @staticmethod
    def _format_item_date(timestamp: str | None) -> str:
        if not timestamp:
            return ""
        try:
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
            return ""

        lines: list[str] = []
        for item in context_packet.reflection_items[:1]:
            lines.append(f"- {item.content.strip()}")

        return "REFLECTION CONTEXT:\n" + "\n\n".join(lines)
