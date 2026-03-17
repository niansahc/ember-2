from pathlib import Path
from src.context.models import ContextPacket


class PromptBuilder:
    def __init__(self):
        base_dir = Path(__file__).resolve().parents[2]
        prompt_path = base_dir / "prompts" / "ember_system_prompt.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    def build_prompt(self, context_packet: ContextPacket) -> str:
        if context_packet.memory_items:
            memory_lines = [item.content for item in context_packet.memory_items[:8]]
            memory_section = "\n\n".join(memory_lines)
        else:
            memory_section = "NO MEMORY FOUND"

        return f"""
You are Ember.

You must ONLY use the user's memory below.

RULES:
- Do NOT invent patterns.
- Do NOT generalize from outside knowledge.
- If something is not explicitly in memory, do not claim it.
- First extract exact details from memory.
- Then describe patterns ONLY if they clearly repeat.

STEP 1: Extract facts (quote or paraphrase what actually happened)
STEP 2: Identify patterns (only if repeated evidence exists)
STEP 3: Answer the user

--- MEMORY ---
{memory_section}

--- USER QUESTION ---
{context_packet.user_message}

--- RESPONSE FORMAT ---
Facts:
- ...

Patterns:
- ...

Answer:
"""