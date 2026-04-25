"""
tests/test_run_monthly_reflection.py

Cover the wiring between monthly reflection and ADR-017 path-2 lodestone
synthesis. The synthesis call is post-reflection-write and non-fatal.
"""

from __future__ import annotations

from unittest.mock import patch


def test_monthly_runner_invokes_lodestone_synthesis_after_reflection() -> None:
    """run_monthly_reflection calls synthesize_lodestone_candidates exactly
    once after the reflection record is written. Order matters: synthesis
    consumes the most recent reflection."""
    call_order: list[str] = []

    def _stub_generate(*args, **kwargs):
        call_order.append("reflection")
        return {"summary": "monthly stub", "memory_count": 5, "source_type": "test"}

    def _stub_synthesize(*args, **kwargs):
        call_order.append("synthesis")
        return None

    with patch(
        "src.reflection.run_monthly_reflection.generate_reflection",
        side_effect=_stub_generate,
    ), patch(
        "src.reflection.run_monthly_reflection.synthesize_lodestone_candidates",
        side_effect=_stub_synthesize,
    ) as mock_synth:
        from src.reflection.run_monthly_reflection import run_monthly_reflection
        result = run_monthly_reflection()

    assert call_order == ["reflection", "synthesis"], (
        "Synthesis must run AFTER reflection write so it consumes the "
        "newly-written record alongside the prior month."
    )
    assert mock_synth.call_count == 1
    assert result["summary"] == "monthly stub"


def test_monthly_runner_attaches_proposed_lodestone_id_when_synthesis_succeeds() -> None:
    """When synthesis writes a record, its id is attached to the reflection
    result for downstream observability."""
    def _stub_generate(*args, **kwargs):
        return {"summary": "monthly stub", "memory_count": 5}

    def _stub_synthesize(*args, **kwargs):
        return {"id": "2026-04-25T10-00-00-000000", "value": "I value X"}

    with patch(
        "src.reflection.run_monthly_reflection.generate_reflection",
        side_effect=_stub_generate,
    ), patch(
        "src.reflection.run_monthly_reflection.synthesize_lodestone_candidates",
        side_effect=_stub_synthesize,
    ):
        from src.reflection.run_monthly_reflection import run_monthly_reflection
        result = run_monthly_reflection()

    assert result.get("proposed_lodestone_id") == "2026-04-25T10-00-00-000000"


def test_monthly_runner_omits_proposed_id_when_synthesis_returns_none() -> None:
    """The common short-circuit case (Stage 1 or 2 exit) returns None.
    Reflection result should not carry proposed_lodestone_id in that case."""
    def _stub_generate(*args, **kwargs):
        return {"summary": "monthly stub", "memory_count": 5}

    with patch(
        "src.reflection.run_monthly_reflection.generate_reflection",
        side_effect=_stub_generate,
    ), patch(
        "src.reflection.run_monthly_reflection.synthesize_lodestone_candidates",
        return_value=None,
    ):
        from src.reflection.run_monthly_reflection import run_monthly_reflection
        result = run_monthly_reflection()

    assert "proposed_lodestone_id" not in result


def test_monthly_runner_swallows_synthesis_exceptions() -> None:
    """Synthesis failures must not break the monthly reflection path -
    the reflection itself succeeded and its result must be returned
    intact even if the post-step crashes."""
    def _stub_generate(*args, **kwargs):
        return {"summary": "monthly stub", "memory_count": 5}

    def _stub_synthesize_explode(*args, **kwargs):
        raise RuntimeError("Ollama unreachable")

    with patch(
        "src.reflection.run_monthly_reflection.generate_reflection",
        side_effect=_stub_generate,
    ), patch(
        "src.reflection.run_monthly_reflection.synthesize_lodestone_candidates",
        side_effect=_stub_synthesize_explode,
    ):
        from src.reflection.run_monthly_reflection import run_monthly_reflection
        result = run_monthly_reflection()

    assert result["summary"] == "monthly stub"
    assert "proposed_lodestone_id" not in result
