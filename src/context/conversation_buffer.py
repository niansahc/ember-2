from collections import deque

class ConversationBuffer:
    def __init__(self, max_turns: int = 6):
        self.buffer = deque(maxlen=max_turns)

    def add_turn(self, user: str, assistant: str):
        self.buffer.append({
            "user": user,
            "assistant": assistant
        })

    def get_recent(self):
        return list(self.buffer)

    def format_for_prompt(self) -> str:
        if not self.buffer:
            return "NO RECENT CONVERSATION"

        lines = []
        for turn in self.buffer:
            lines.append(f"User: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        
        return "\n".join(lines)
    