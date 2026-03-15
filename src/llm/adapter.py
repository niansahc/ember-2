import ollama

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder
from src.llm.system_prompt import load_system_prompt


class LLMAdapter:
    def __init__(
        self,
        model: str = "phi3:mini",
        prompt_builder: PromptBuilder | None = None,
    ):
        self.model = model
        self.prompt_builder = prompt_builder or PromptBuilder()

    def generate_response(self, context_packet: ContextPacket) -> str:
        prompt = self.prompt_builder.build_prompt(context_packet)
        system_prompt = load_system_prompt()

        response = ollama.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        return response["message"]["content"]