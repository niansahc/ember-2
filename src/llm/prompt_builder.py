from pathlib import Path

from src.context.models import ContextPacket


class PromptBuilder:
    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    def build_prompt(self, context_packet: ContextPacket) -> str:
        sections: list[str] = []

        if context_packet.memory_items:
            memory_lines = []
            for item in context_packet.memory_items[:8]:
                memory_lines.append(
                    f"[{item.item_type} | score={item.score:.3f}]\n{item.content}"
                )
            sections.append("Retrieved context:\n" + "\n\n".join(memory_lines))
        else:
            sections.append("Retrieved context:\nNone relevant.")

        if context_packet.reflection_items:
            reflection_lines = []
            for item in context_packet.reflection_items[:3]:
                reflection_lines.append(f"[reflection]\n{item.content}")
            sections.append("Retrieved reflections:\n" + "\n\n".join(reflection_lines))
        else:
            sections.append("Retrieved reflections:\nNone relevant.")

        sections.append(
            "Instructions:\nUse retrieved context when it is relevant and specific. "
            "Ground claims in the retrieved material. If context is weak or missing, say so plainly."
        )

        sections.append(f"User message:\n{context_packet.user_message}")

        return "\n\n".join(sections)