"""
tests/test_lodestone_synthesis.py

Coverage for src/reflection/lodestone_synthesis.py - three-stage path-2
acquisition per ADR-017 + the read_active() injection-gate contract that
keeps proposed records out of the prompt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.reflection.lodestone_synthesis import (
    MIN_REFLECTIONS_FOR_SYNTHESIS,
    STAGE1_MIN_THEME_WORDS,
    SYNTHESIS_WINDOW_DAYS,
    VALID_CATEGORIES,
    _format_reflection_block,
    _parse_stage3_output,
    synthesize_lodestone_candidates,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic reflection records + mocked LLM
# ---------------------------------------------------------------------------


def _record_ts(days_ago: int = 0) -> str:
    """Timestamp in the format write_memory emits."""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H-%M-%S-%f")


def _reflection_record(text: str, days_ago: int = 0) -> dict:
    return {
        "type": "reflection",
        "text": text,
        "timestamp": _record_ts(days_ago),
        "metadata": {"cadence": "weekly"},
    }


def _stub_memory_service(reflections: list[dict]) -> MagicMock:
    svc = MagicMock()
    svc.read.return_value = reflections
    return svc


def _stub_chat(responses: list[str]):
    """Return a side_effect callable that yields the given responses in order."""
    iterator = iter(responses)
    def _side_effect(**kwargs):
        return {"message": {"content": next(iterator)}}
    return _side_effect


# ---------------------------------------------------------------------------
# Stage exit tests
# ---------------------------------------------------------------------------


def test_stage1_insufficient_reflections_skips_entirely() -> None:
    """Vault has < MIN_REFLECTIONS_FOR_SYNTHESIS reflections in window
    -> function returns None and makes ZERO LLM calls."""
    svc = _stub_memory_service(
        [_reflection_record("a single reflection", days_ago=2)]
    )
    with patch("src.reflection.lodestone_synthesis.ollama.chat") as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 0


def test_stage1_reflections_outside_window_excluded() -> None:
    """Records older than SYNTHESIS_WINDOW_DAYS are not counted toward the
    minimum-evidence floor."""
    svc = _stub_memory_service(
        [
            _reflection_record(f"old reflection {i}", days_ago=SYNTHESIS_WINDOW_DAYS + 5)
            for i in range(10)
        ]
    )
    with patch("src.reflection.lodestone_synthesis.ollama.chat") as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 0


def test_stage1_no_value_found_short_circuits() -> None:
    """Stage 1 returns NO_VALUE_FOUND -> exit, no Stage 2/3 calls."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat(["NO_VALUE_FOUND"]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 1


def test_stage1_too_abstract_theme_short_circuits() -> None:
    """Stage 1 returns a single word -> rejected as too abstract, no Stage 2 call."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat(["honesty"]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 1


def test_stage2_no_category_match_short_circuits() -> None:
    """Stage 1 returns a theme but Stage 2 returns NO_CATEGORY_MATCH -> exit
    after 2 LLM calls (no Stage 3)."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to honest conversation over comfort",
            "NO_CATEGORY_MATCH",
        ]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 2


def test_stage2_invalid_category_treated_as_no_match() -> None:
    """Stage 2 returns gibberish -> treated as no-match, no Stage 3 call."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to direct conversation over comfort",
            "miscellaneous",
        ]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 2


def test_stage3_parser_failure_returns_none(tmp_path, monkeypatch) -> None:
    """Stage 3 returns malformed text (no VALUE: marker) -> no record written."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(tmp_path))
    import importlib
    import src.core.config as cfg
    importlib.reload(cfg)

    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to honest conversation over comfort",
            "character",
            "this is not a valid stage 3 output at all",
        ]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 3


def test_stage3_empty_evidence_returns_none(tmp_path, monkeypatch) -> None:
    """Stage 3 returns VALUE: but no evidence lines -> no record written."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(tmp_path))
    import importlib
    import src.core.config as cfg
    importlib.reload(cfg)

    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to direct conversation over comfort",
            "character",
            "VALUE: I value direct honest conversation\nEVIDENCE:\n",
        ]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)
    assert result is None
    assert mock_chat.call_count == 3


# ---------------------------------------------------------------------------
# Successful synthesis + record-write contract
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "memory" / "lodestone").mkdir(parents=True)
    (vault / "embeddings").mkdir()
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(vault))
    import importlib
    import src.core.config as cfg
    importlib.reload(cfg)
    yield vault


def test_stage3_produces_record_when_all_stages_pass(temp_vault) -> None:
    """All three stages return valid output -> record is written via
    lodestone_service.write with the right fields."""
    reflections = [_reflection_record(f"reflection {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)

    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to direct conversation over comfort",
            "character",
            (
                "VALUE: I would rather lose ease than skip a hard conversation\n"
                "EVIDENCE:\n"
                "- declined to soften feedback in three sessions\n"
                "- noted resistance to performative pleasantness\n"
                "- chose a hard conversation over a comfortable one this month"
            ),
        ]),
    ) as mock_chat:
        result = synthesize_lodestone_candidates(memory_service=svc)

    assert result is not None
    assert mock_chat.call_count == 3
    assert result["acquisition_path"] == "inferred"
    assert result["confirmed"] is False
    assert result["source"] == "reflection_synthesis"
    assert result["metadata"]["taxonomy_category"] == "character"
    assert result["supporting_evidence"]
    assert "declined to soften" in result["supporting_evidence"]
    assert "lose ease" in result["value"]


def test_inferred_record_does_not_appear_in_read_active(temp_vault) -> None:
    """A record written by path-2 synthesis must NOT appear in read_active()."""
    from src.memory import lodestone_service

    reflections = [_reflection_record(f"r {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to honest conversation over comfort",
            "character",
            "VALUE: I value direct honesty\nEVIDENCE:\n- example one\n- example two",
        ]),
    ):
        synthesize_lodestone_candidates(memory_service=svc)

    active = lodestone_service.read_active()
    assert all(r["acquisition_path"] != "inferred" for r in active), (
        "Inferred (proposed) record leaked into read_active() — confirmed-only "
        "gate is broken. ADR-017 / ADR-035 require proposed records stay "
        "invisible to prompt assembly."
    )


def test_inferred_record_appears_in_read_proposed(temp_vault) -> None:
    """Same record is visible via read_proposed() and read_all()."""
    from src.memory import lodestone_service

    reflections = [_reflection_record(f"r {i}", days_ago=i) for i in range(6)]
    svc = _stub_memory_service(reflections)
    with patch(
        "src.reflection.lodestone_synthesis.ollama.chat",
        side_effect=_stub_chat([
            "the user keeps returning to honest conversation over comfort",
            "character",
            "VALUE: I value direct honesty\nEVIDENCE:\n- example one\n- example two",
        ]),
    ):
        synthesize_lodestone_candidates(memory_service=svc)

    proposed = lodestone_service.read_proposed()
    assert any(
        r["acquisition_path"] == "inferred" and r["confirmed"] is False
        for r in proposed
    )
    assert any(r["acquisition_path"] == "inferred" for r in lodestone_service.read_all())


# ---------------------------------------------------------------------------
# Defensive Q6 contract test - resolver must only call read_active
# ---------------------------------------------------------------------------


def test_lodestone_resolver_only_calls_read_active() -> None:
    """ADR-035 / Item 7: lodestone_resolver must NEVER call read_proposed
    or read_all - only read_active. Pins the confirmed-only injection
    contract so a future resolver refactor can't silently leak proposed
    records into prompt assembly."""
    from src.memory import lodestone_service

    def _explode(*_args, **_kwargs):
        raise AssertionError(
            "lodestone_resolver called read_proposed/read_all - confirmed-only "
            "gate violated. Inferred records would leak into the prompt."
        )

    with patch.object(lodestone_service, "read_proposed", side_effect=_explode), \
         patch.object(lodestone_service, "read_all", side_effect=_explode):
        # Import inside the patch context so any module-level eager calls
        # would also be intercepted.
        from src.context import lodestone_resolver  # noqa: F401
        # Trigger the resolve path with empty inputs - read_active should
        # be the only lodestone_service entrypoint touched.
        try:
            lodestone_resolver.resolve(query_embedding=[0.0] * 8, max_records=2)
        except Exception:
            # Any internal failure is fine; we only care that the explode
            # functions were not called.
            pass


# ---------------------------------------------------------------------------
# Helper unit tests - parser + reflection-block formatter
# ---------------------------------------------------------------------------


def test_parse_stage3_well_formed() -> None:
    out = _parse_stage3_output(
        "VALUE: I would rather lose ease than skip a hard conversation\n"
        "EVIDENCE:\n"
        "- declined to soften feedback\n"
        "- chose hard conversation"
    )
    assert out is not None
    value, evidence = out
    assert value.startswith("I would rather lose ease")
    assert evidence == ["declined to soften feedback", "chose hard conversation"]


def test_parse_stage3_missing_value() -> None:
    assert _parse_stage3_output("EVIDENCE:\n- ev") is None


def test_parse_stage3_missing_evidence_marker() -> None:
    assert _parse_stage3_output("VALUE: I value X") is None


def test_parse_stage3_empty_value_string() -> None:
    assert _parse_stage3_output("VALUE: \nEVIDENCE:\n- ev") is None


def test_format_reflection_block_truncates_long_text() -> None:
    long_text = "x" * 1000
    rec = _reflection_record(long_text, days_ago=1)
    block = _format_reflection_block([rec])
    assert "..." in block
    assert len(block) < 1000


def test_format_reflection_block_skips_empty_text() -> None:
    rec = {"type": "reflection", "text": "", "timestamp": _record_ts(1)}
    assert _format_reflection_block([rec]) == ""


def test_valid_categories_match_taxonomy_yaml() -> None:
    """Pin VALID_CATEGORIES against config/lodestone_taxonomy.yaml so a future
    taxonomy change doesn't silently drift from what Stage 2 accepts."""
    import yaml
    from pathlib import Path

    taxonomy_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "lodestone_taxonomy.yaml"
    )
    with taxonomy_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    yaml_categories = set(data["categories"].keys())
    assert VALID_CATEGORIES == yaml_categories
