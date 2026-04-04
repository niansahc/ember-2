"""
src/reflection/run_monthly_reflection.py

Monthly reflection runner. Uses the monthly_reflection.txt prompt template
for LLM-driven synthesis across journal, conversation, and reflection records.

See ADR-016 prompt writing standards and TDD §32.6 for design rationale.
"""

from pathlib import Path

from src.reflection.generate_reflection import generate_reflection


def _load_prompt_template() -> str:
    """Load the monthly reflection prompt template."""
    template_path = Path(__file__).resolve().parents[2] / "prompts" / "monthly_reflection.txt"
    return template_path.read_text(encoding="utf-8")


def run_monthly_reflection():
    return generate_reflection(
        memory_types=["journal", "conversation", "reflection"],
        limit=100,
        store=True,
        cadence="monthly",
        prompt_template=_load_prompt_template(),
    )


if __name__ == "__main__":
    result = run_monthly_reflection()
    print(result)
