from __future__ import annotations

# Approximate token counts for common Ollama models.
# Used to update context_window when the active model changes via POST /model.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "llama3.1:8b": 8192,
    "qwen2.5:14b": 32768,
    "qwen3:8b":    32768,
    "mistral:7b":  8192,
    "phi3:mini":   4096,
}

COMPRESSION_THRESHOLD = 1500  # fixed token count — keeps context packet within budget on any model


def _estimate_tokens(text: str) -> int:
    """Word-count approximation: words * 1.3 ≈ tokens. No tokenizer dependency."""
    return int(len(text.split()) * 1.3)


class ConversationBuffer:
    def __init__(self, max_turns: int = 20, context_window: int = 8192) -> None:
        self.buffer: list[dict] = []
        self.max_turns = max_turns
        self.context_window = context_window

    def add_turn(self, user: str, assistant: str) -> None:
        self.buffer.append({"user": user, "assistant": assistant})
        if len(self.buffer) > self.max_turns:
            self.buffer.pop(0)

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

    def format_for_prompt(self) -> str:
        if not self.buffer:
            return "NO RECENT CONVERSATION"
        lines = []
        for turn in self.buffer:
            lines.append(f"User: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines)
