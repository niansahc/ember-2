from src.context.models import ContextPacket


class LLMAdapter:
    def generate_response(self, context_packet: ContextPacket) -> str:
        """
        Placeholder LLM response generation.
        Later this will call Ollama or another local model backend.
        """
        item_count = len(context_packet.all_items())

        if item_count == 0:
            return f"You said: {context_packet.user_message}"

        memory_preview = "\n".join(
            f"- {item.content[:120]}" for item in context_packet.memory_items[:3]
        )

        return (
            f"You said: {context_packet.user_message}\n\n"
            f"I found {item_count} context item(s).\n"
            f"Top memory hits:\n{memory_preview}"
        )