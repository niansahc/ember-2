from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger("ember.conversation_buffer")

# Issue #133: how many conversations keep a live buffer at once. A user with
# a handful of open conversations, plus an eval harness interleaving two or
# three sessions, stays well inside this. Evicting the least recently used
# buffer loses only that conversation's in-memory history, which before #133
# was lost on every session switch anyway.
_MAX_LIVE_BUFFERS = 16

# B-MEM-005: bound the hedged_record_ids tracker so long sessions don't grow
# the set without limit. LRU eviction keeps the most-recently hedged records.
_HEDGED_RECORD_IDS_MAX = 50

# Approximate token counts for common Ollama models.
# Used by LLMAdapter._get_num_ctx to size the Ollama context budget.
#
# Lookup is a strict exact-tag dict get in both readers, with no normalization,
# so keys must be the exact installed tag. A quantized pull such as
# "glm-4.7-flash:q4_K_M" does not match a "glm-4.7-flash" entry and would fall
# back to the 8192 default - two of the sweep candidates below are installed
# under quant-suffixed tags for exactly this reason. Add the tag you actually
# run, not the base name.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "llama3.1:8b":              8192,
    "qwen2.5:14b":              32768,
    "qwen3:8b":                 40960,   # B-QUAL-001 / 2026-04-26: matches modelfile-declared context length (verified via `ollama show qwen3:8b`).
    "mistral:7b":               8192,
    "phi3:mini":                4096,
    # Model-sweep candidates. These ran at the 8192 fallback (num_ctx 6553)
    # against the incumbent's 32768, so every sweep result was scored at one
    # fifth the incumbent's context budget. These four tags are the exact
    # installed strings, not the library base names.
    "qwen3.5:9b":               262144,  # 2026-09-12: matches modelfile-declared context length (verified via `ollama show qwen3.5:9b`).
    "qwen3:32b":                40960,   # 2026-09-12: matches modelfile-declared context length (verified via `ollama show qwen3:32b`).
    "qwen3.6:35b-a3b":          262144,  # 2026-09-12: matches modelfile-declared context length (verified via `ollama show qwen3.6:35b-a3b`).
    "gemma4:26b-a4b-it-q4_K_M": 262144,  # 2026-09-12: matches modelfile-declared context length (verified via `ollama show gemma4:26b-a4b-it-q4_K_M`).
    "glm-4.7-flash:q4_K_M":     202752,  # 2026-09-12: matches modelfile-declared context length (verified via `ollama show glm-4.7-flash:q4_K_M`). The library page displays this as "198K".
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
    """Turn history and session-sticky flags for ONE conversation.

    A buffer belongs to exactly one (vault, session) pair, assigned by
    ConversationBufferStore at creation and never changed afterwards (issue
    #133). Nothing in here resets on a session change: a different session
    gets a different buffer, so there is nothing to reset. The legacy default
    buffer on PromptBuilder (session_id None) serves callers that have no
    session, such as direct prompt-builder use.
    """

    def __init__(
        self,
        max_turns: int = 20,
        *,
        session_id: str | None = None,
    ) -> None:
        self.buffer: list[dict] = []
        self.max_turns = max_turns
        # Identity of the conversation this buffer holds. Read by the
        # compression thread at spawn time, never at write time, so a summary
        # is always attributed to the session whose turns it compresses.
        self.session_id = session_id
        # Guards list mutation. Turns from one session are sequential in
        # practice, but compression runs on a background thread and pops and
        # prepends while the next turn may be appending.
        self._lock = threading.Lock()
        # Single-flight guard for compression: a second compression of the
        # same buffer while one is mid-LLM-call is skipped, not queued.
        self.compression_lock = threading.Lock()
        # Session-sticky flags. Set when the user objects to a behavior.
        # Persist for the lifetime of this buffer (= one conversation).
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

    def add_turn(self, user: str, assistant: str) -> None:
        user_lower = user.lower()
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            n = max(1, len(self.buffer) // 2)
            oldest = self.buffer[:n]
            self.buffer = self.buffer[n:]
            return oldest

    def restore_turns(self, turns: list[dict]) -> None:
        """Put popped turns back at the head, in their original order.
        Used when compression fails after pop_oldest_half."""
        with self._lock:
            self.buffer = list(turns) + self.buffer

    def inject_summary_turn(self, summary: str) -> None:
        """Prepend a synthetic turn representing the compressed conversation history.

        is_summary=True: Recalling Too Well Phase 1, item 8. Without this
        flag the prompt builder rendered this turn as "Ember: {summary}",
        misattributing user misconceptions from the compressed half of the
        conversation to Ember, and feeding the "use the most recent
        assistant response as primary reference" instruction rule.
        """
        with self._lock:
            self.buffer.insert(0, {
                "user": "[Earlier conversation summary]",
                "assistant": summary,
                "is_summary": True,
            })

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


def vault_key(vault_path) -> str | None:
    """Normalize a vault path into a store key, resolved the same way as
    config and store_cache. None stays None, so callers that could not
    resolve a vault share one keyspace instead of failing."""
    if vault_path is None:
        return None
    return str(Path(vault_path).resolve())


class ConversationBufferStore:
    """Per-conversation buffers keyed by (vault, session) (issue #133).

    Before this existed the process held one ConversationBuffer and cleared it
    when add_turn saw a new session id. add_turn runs after generation, so the
    first prompt of every new session was built from the previous session's
    turns, interleaved sessions each saw the other's last turn, and a vault
    swap left the old vault's turns in the next prompt. Keying by vault as well
    as session means a swap can never surface the old vault's conversation,
    even under a reused session id.
    """

    def __init__(self, max_buffers: int = _MAX_LIVE_BUFFERS) -> None:
        self._buffers: OrderedDict[tuple[str | None, str], ConversationBuffer] = OrderedDict()
        self._max_buffers = max_buffers
        self._lock = threading.Lock()

    def get(self, vault_path, session_id: str) -> ConversationBuffer:
        """Return the buffer for this conversation, creating it if needed."""
        key = (vault_key(vault_path), session_id)
        evicted: list[str] = []
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = ConversationBuffer(session_id=session_id)
                self._buffers[key] = buf
                while len(self._buffers) > self._max_buffers:
                    evicted.append(self._buffers.popitem(last=False)[0][1])
            else:
                self._buffers.move_to_end(key)
        for evicted_session in evicted:
            logger.info(
                "[BUFFER] Evicted least recently used buffer session=%s",
                evicted_session,
            )
        return buf

    def find(self, vault_path, session_id: str) -> ConversationBuffer | None:
        """Return the buffer for this conversation if one is live, without
        creating one. Readers outside a turn (delete, reflect) use this."""
        with self._lock:
            return self._buffers.get((vault_key(vault_path), session_id))

    def clear(self) -> int:
        """Drop every buffer. Called on vault swap. Returns the count."""
        with self._lock:
            count = len(self._buffers)
            self._buffers.clear()
            return count
