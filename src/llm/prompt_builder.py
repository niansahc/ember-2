from src.context.models import ContextPacket


class PromptBuilder:
    def build_prompt(self, context_packet: ContextPacket) -> str:
        """
        Convert a ContextPacket into a structured prompt string.
        """
        sections: list[str] = []

        sections.append(
            "Use the retrieved context below as your primary grounding source. "
            "Answer in plain, direct language. "
            "Be concrete, concise, and observational. "
            "Prefer describing patterns actually visible in the retrieved context over interpretation. "
            "Do not invent details that are not supported by the context. "
            "Do not ask follow-up questions. "
            "If the context is thin or repetitive, say so plainly."
        )

        if context_packet.memory_items:
            memory_lines = "\n".join(
                f"- {item.content}" for item in context_packet.memory_items[:5]
            )
            sections.append(f"Retrieved memories:\n{memory_lines}")

        if context_packet.reflection_items:
            reflection_lines = "\n".join(
                f"- {item.content}" for item in context_packet.reflection_items[:3]
            )
            sections.append(f"Retrieved reflections:\n{reflection_lines}")

        sections.append(f"Current user message:\n{context_packet.user_message}")

        return "\n\n".join(sections)
    