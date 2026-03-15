import ollama

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder


class LLMAdapter:
    def __init__(
        self,
        model: str = "qwen3:8b",
        prompt_builder: PromptBuilder | None = None,
    ):
        self.model = model
        self.prompt_builder = prompt_builder or PromptBuilder()

    def generate_response(self, context_packet: ContextPacket) -> str:
        prompt = self.prompt_builder.build_prompt(context_packet)

        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": context_packet.user_message},
            ],
            options={
                "temperature": 0.7
            }
        )

        return response["message"]["content"]