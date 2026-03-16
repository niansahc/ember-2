from pathlib import Path
from src.context.models import ContextPacket


class PromptBuilder:

    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

   

    def build_prompt(self, context_packet: ContextPacket) -> str:

        sections: list[str] = [self.system_prompt]

        if context_packet.memory_items:
            memory_lines = "\n".join(
                f"- {item.content}" for item in context_packet.memory_items[:5]
            )
            sections.append(f"Retrieved memories:\n{memory_lines}")
        else:
            sections.append("Retrieved memories:\nNone relevant.")

        if context_packet.reflection_items:
            reflection_lines = "\n".join(
                f"- {item.content}" for item in context_packet.reflection_items[:3]
            )
            sections.append(f"Retrieved reflections:\n{reflection_lines}")
        else:
            sections.append("Retrieved reflections:\nNone relevant.")

        sections.append(f"Current user message:\n{context_packet.user_message}")

        prompt = "\n\n".join(sections)

      

        return prompt