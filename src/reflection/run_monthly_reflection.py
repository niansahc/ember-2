"""
src/reflection/run_monthly_reflection.py

Monthly reflection runner. Uses the monthly_reflection.txt prompt template
for LLM-driven synthesis across journal, conversation, and reflection records.

After the monthly reflection record is written, runs ADR-017 path-2
lodestone synthesis as a post-step (monthly cadence only - signal density
across a month justifies the multi-LLM-call cost).

See ADR-016 prompt writing standards and TDD §32.6 for design rationale.
"""

import logging
from pathlib import Path

from src.reflection.generate_reflection import generate_reflection
from src.reflection.lodestone_synthesis import synthesize_lodestone_candidates

logger = logging.getLogger("ember.reflection.monthly")


def _load_prompt_template() -> str:
    """Load the monthly reflection prompt template."""
    template_path = Path(__file__).resolve().parents[2] / "prompts" / "monthly_reflection.txt"
    return template_path.read_text(encoding="utf-8")


def run_monthly_reflection():
    reflection = generate_reflection(
        memory_types=["journal", "conversation", "reflection"],
        limit=100,
        store=True,
        cadence="monthly",
        prompt_template=_load_prompt_template(),
    )

    # ADR-017 path 2: attempt inferred-value synthesis after the monthly
    # reflection has been written. Most runs short-circuit at Stage 1 or 2.
    # Wrapped: synthesis failures must not break the monthly reflection.
    try:
        proposed = synthesize_lodestone_candidates()
        if proposed is not None:
            reflection["proposed_lodestone_id"] = proposed["id"]
    except Exception as exc:
        logger.warning("[LODESTONE_SYNTHESIS] post-reflection pass failed (non-fatal): %s", exc)

    return reflection


if __name__ == "__main__":
    result = run_monthly_reflection()
    print(result)
