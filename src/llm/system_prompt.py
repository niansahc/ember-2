from pathlib import Path


def load_system_prompt() -> str:
    project_root = Path(__file__).resolve().parents[2]
    prompt_path = project_root / "docs" / "prompts" / "Ember-2_SystemPrompt_v2fewshot.txt"
    return prompt_path.read_text(encoding="utf-8").strip()