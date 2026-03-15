from src.context.models import ContextPacket


class PromptBuilder:
    def build_prompt(self, context_packet: ContextPacket) -> str:
        """
        Convert a ContextPacket into a simple prompt string.
        Placeholder implementation for now.
        """
        sections: list[str] = []

        if context_packet.memory_items:
            memory_lines = "\n".join(
                f"- {item.content}" for item in context_packet.memory_items
            )
            sections.append(f"Relevant memories:\n{memory_lines}")

        if context_packet.reflection_items:
            reflection_lines = "\n".join(
                f"- {item.content}" for item in context_packet.reflection_items
            )
            sections.append(f"Relevant reflections:\n{reflection_lines}")

        sections.append(f"User message:\n{context_packet.user_message}")

        return "\n\n".join(sections)