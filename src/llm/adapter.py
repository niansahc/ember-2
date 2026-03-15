from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder


class LLMAdapter:
    def __init__(self, prompt_builder: PromptBuilder | None = None) -> None:
        self.prompt_builder = prompt_builder or PromptBuilder()

    def generate_response(self, context_packet: ContextPacket) -> str:
        """
        Placeholder LLM response generation.
        Later this will call Ollama or another local model backend.
        """
        prompt = self.prompt_builder.build_prompt(context_packet)
        item_count = len(context_packet.all_items())

        if item_count == 0:
            return f"No context found.\n\nPrompt preview:\n{prompt}"

        return (
            f"I found {item_count} context item(s).\n\n"
            f"Prompt preview:\n{prompt}"
        )
    