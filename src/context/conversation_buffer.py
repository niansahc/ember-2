from __future__ import annotations

import logging
from collections import OrderedDict

logger = logging.getLogger("ember.conversation_buffer")

# B-MEM-005: bound the hedged_record_ids tracker so long sessions don't grow
# the set without limit. LRU eviction keeps the most-recently hedged records.
_HEDGED_RECORD_IDS_MAX = 50

# Approximate token counts for common Ollama models.
# Used to update context_window when the active model changes via POST /model.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "llama3.1:8b": 8192,
    "qwen2.5:14b": 32768,
    "qwen3:8b":    40960,  # B-QUAL-001 / 2026-04-26: matches modelfile-declared context length (verified via `ollama show qwen3:8b`).
    "mistral:7b":  8192,
    "phi3:mini":   4096,
}

COMPRESSION_THRESHOLD = 1500  # fixed token count — keeps context packet within budget on any model


def _estimate_tokens(text: str) -> int:
    """Word-count approximation: words * 1.3 ≈ tokens. No tokenizer dependency."""
    return int(len(text.split()) * 1.3)


# Patterns indicating the user wants Ember to stop asking questions.
# Checked against the user message (lowercased) on every add_turn.
QUESTION_OBJECTION_MARKERS: tuple[str, ...] = (
    "stop asking",
    "don't ask me",
    "don't ask questions",
    "quit asking",
    "no more questions",
    "stop with the questions",
    "i don't want questions",
    "please don't ask",
    "enough questions",
    "stop ending with questions",
    "don't end with a question",
)

# Patterns indicating the user wants to drop a topic.
# Checked against the user message (lowercased) on every add_turn.
TOPIC_DECLINE_MARKERS: tuple[str, ...] = (
    "i don't want to talk about",
    "i don't want to discuss",
    "drop it",
    "let's move on",
    "can we change the subject",
    "i'd rather not",
    "stop bringing that up",
    "let it go",
    "not interested in that",
    "i said no",
    "i already said",
    "please stop",
    "enough about",
    "can we not",
)


class ConversationBuffer:
    def __init__(self, max_turns: int = 20, context_window: int = 8192) -> None:
        self.buffer: list[dict] = []
        self.max_turns = max_turns
        self.context_window = context_window
        # Session-sticky flags. Set when the user objects to a behavior.
        # Persist for the lifetime of this buffer (= one API process).
        self.question_suppressed: bool = False
        self.declined_topics: list[str] = []
        # B-MEM-005: track which retrieved record IDs the model has already
        # been instructed to hedge this session. Bounded LRU so long sessions
        # don't grow without limit.
        self.hedged_record_ids: OrderedDict[str, None] = OrderedDict()
        # B-MEM-005 / S1: stages record IDs at prompt-build time. Committed to
        # hedged_record_ids by commit_pending_hedge() after the coaching filter
        # finalizes the response — so failed LLM calls or stripped hedges don't
        # leave spurious marks that suppress confidence blocks on later turns.
        self.pending_hedge_record_ids: list[str] = []
        # Session tracking: the buffer is a process-level singleton, but only
        # one UI session at a time is "the current conversation". When the
        # session_id changes between add_turn calls, prior turns belong to a
        # different conversation and must not bleed into the new one.
        self.current_session_id: str | None = None

    def add_turn(
        self, user: str, assistant: str, session_id: str | None = None,
    ) -> None:
        # Cross-session reset: if a non-None session_id arrives that differs
        # from the prior one, the prior turns are not history for this
        # conversation. Clear the buffer (and session-sticky state) to
        # prevent cross-session pollution. session_id=None preserves the
        # legacy 2-arg behavior so existing callers/tests are unaffected.
        if session_id is not None and session_id != self.current_session_id:
            if self.buffer or self.current_session_id is not None:
                logger.info(
                    "[BUFFER] Session changed (%s -> %s); clearing %d turns.",
                    self.current_session_id, session_id, len(self.buffer),
                )
            self.buffer = []
            self.question_suppressed = False
            self.declined_topics = []
            self.hedged_record_ids.clear()
            self.pending_hedge_record_ids = []
            self.current_session_id = session_id

        user_lower = user.lower()
        self._check_question_objection(user_lower)
        self._check_topic_decline(user_lower)
        self.buffer.append({"user": user, "assistant": assistant})
        if len(self.buffer) > self.max_turns:
            self.buffer.pop(0)

    def _check_question_objection(self, user_lower: str) -> None:
        """Detect user objection to questions and set sticky flag."""
        if self.question_suppressed:
            return
        if any(marker in user_lower for marker in QUESTION_OBJECTION_MARKERS):
            self.question_suppressed = True

    def _check_topic_decline(self, user_lower: str) -> None:
        """Detect user declining a topic and record it."""
        for marker in TOPIC_DECLINE_MARKERS:
            if marker in user_lower:
                # Extract the rest of the sentence after the marker as the topic.
                idx = user_lower.index(marker) + len(marker)
                topic = user_lower[idx:].strip().rstrip(".!?,")
                if topic and len(topic) > 2:
                    self.declined_topics.append(topic)
                break

    def get_recent(self) -> list[dict]:
        return list(self.buffer)

    def token_count(self) -> int:
        """Estimate total tokens across all turns in the buffer."""
        total = 0
        for turn in self.buffer:
            total += _estimate_tokens(turn["user"])
            total += _estimate_tokens(turn["assistant"])
        return total

    def needs_compression(self) -> bool:
        """Return True when conversation history tokens exceed the fixed threshold."""
        return self.token_count() > COMPRESSION_THRESHOLD

    def pop_oldest_half(self) -> list[dict]:
        """Remove and return the oldest half of turns for summarization."""
        n = max(1, len(self.buffer) // 2)
        oldest = self.buffer[:n]
        self.buffer = self.buffer[n:]
        return oldest

    def inject_summary_turn(self, summary: str) -> None:
        """Prepend a synthetic turn representing the compressed conversation history."""
        self.buffer.insert(0, {
            "user": "[Earlier conversation summary]",
            "assistant": summary,
        })

    def set_context_window(self, model: str) -> None:
        """Update the context window size when the active model changes."""
        if model in MODEL_CONTEXT_WINDOWS:
            self.context_window = MODEL_CONTEXT_WINDOWS[model]

    def mark_hedge_emitted(self, record_ids: list[str]) -> None:
        """Mark these record IDs as having been hedged this session.

        Bounded LRU: if the set grows beyond _HEDGED_RECORD_IDS_MAX, the
        oldest entries are evicted first. Re-marking an existing ID moves
        it to the most-recent position.
        """
        for rid in record_ids:
            if not rid:
                continue
            if rid in self.hedged_record_ids:
                self.hedged_record_ids.move_to_end(rid)
            else:
                self.hedged_record_ids[rid] = None
                while len(self.hedged_record_ids) > _HEDGED_RECORD_IDS_MAX:
                    self.hedged_record_ids.popitem(last=False)

    def was_hedged(self, record_id: str) -> bool:
        """Return True if this record was hedged earlier in the session.
        Hits move the entry to the most-recent position (LRU touch)."""
        if not record_id or record_id not in self.hedged_record_ids:
            return False
        self.hedged_record_ids.move_to_end(record_id)
        return True

    def set_pending_hedge(self, record_ids: list[str]) -> None:
        """Stage record IDs for hedge marking. Overwrites any prior pending
        state from a previous prompt build. Committed by commit_pending_hedge()
        after the response is finalized."""
        self.pending_hedge_record_ids = list(record_ids)

    def commit_pending_hedge(self) -> None:
        """Promote pending hedge IDs into hedged_record_ids. Called by
        openai_adapter after the coaching filter completes — confirms the
        response was actually delivered before marking records as hedged."""
        if self.pending_hedge_record_ids:
            self.mark_hedge_emitted(self.pending_hedge_record_ids)
            self.pending_hedge_record_ids = []

    def format_for_prompt(self) -> str:
        if not self.buffer:
            return "NO RECENT CONVERSATION"
        lines = []
        for turn in self.buffer:
            lines.append(f"User: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines)
