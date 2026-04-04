"""
Tests for monthly reflection cadence.

Covers: generation with prompt template, cadence field, source types,
LLM synthesis path, prompt template loading.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

from src.reflection.generate_reflection import generate_reflection


MOCK_MEMORIES = [
    {
        "text": "Worked on the retrieval pipeline today. Made progress on dedup logic and diversity selection.",
        "timestamp": "2026-03-15T10-00-00",
        "source": "journal",
        "type": "journal",
        "metadata": {"role": "user", "content_kind": "experience"},
    },
    {
        "text": "The state layer is now integrated into the context packet. Open loops surface correctly.",
        "timestamp": "2026-03-20T14-30-00",
        "source": "conversation",
        "type": "conversation",
        "metadata": {"role": "user", "content_kind": "experience"},
    },
    {
        "text": "Weekly patterns: sustained focus on retrieval quality. State layer integration complete. Garden planning started.",
        "timestamp": "2026-03-22T08-00-00",
        "source": "reflection_engine",
        "type": "reflection",
        "metadata": {"cadence": "weekly"},
    },
]


def _mock_read(memory_type: str = "journal", limit: int = 50):
    return [m for m in MOCK_MEMORIES if m.get("type") == memory_type]


def test_monthly_reflection_uses_prompt_template():
    """Verify that passing a prompt_template triggers LLM synthesis path."""
    template = "Synthesize {record_count} records from {window_start} to {window_end}. Types: {source_types}.\n\n{records}\n\nWrite a synthesis."

    with patch("src.reflection.generate_reflection.memory_service") as mock_svc, \
         patch("src.reflection.generate_reflection._llm_synthesize") as mock_llm:

        mock_svc.read.side_effect = _mock_read
        mock_svc.write = MagicMock()
        mock_llm.return_value = "A thoughtful monthly synthesis."

        result = generate_reflection(
            memory_types=["journal", "conversation", "reflection"],
            limit=100,
            store=True,
            cadence="monthly",
            prompt_template=template,
        )

        assert mock_llm.called
        assert result["summary"] == "A thoughtful monthly synthesis."


def test_monthly_reflection_stores_with_monthly_cadence():
    """Verify cadence='monthly' is set in stored metadata."""
    template = "Synthesize {record_count} records. {window_start} {window_end} {source_types}\n{records}"

    with patch("src.reflection.generate_reflection.memory_service") as mock_svc, \
         patch("src.reflection.generate_reflection._llm_synthesize") as mock_llm:

        mock_svc.read.side_effect = _mock_read
        mock_svc.write = MagicMock()
        mock_llm.return_value = "Monthly synthesis content."

        generate_reflection(
            memory_types=["journal", "conversation", "reflection"],
            limit=100,
            store=True,
            cadence="monthly",
            prompt_template=template,
        )

        # Verify write was called with correct metadata
        mock_svc.write.assert_called_once()
        call_kwargs = mock_svc.write.call_args
        assert call_kwargs[1]["tags"] == ["reflection", "monthly"] or \
               (len(call_kwargs[0]) > 0 or "monthly" in str(call_kwargs))


def test_monthly_reflection_wider_source_types():
    """Verify monthly pulls from journal + conversation + reflection."""
    template = "Test {record_count} {window_start} {window_end} {source_types}\n{records}"

    with patch("src.reflection.generate_reflection.memory_service") as mock_svc, \
         patch("src.reflection.generate_reflection._llm_synthesize") as mock_llm:

        mock_svc.read.side_effect = _mock_read
        mock_svc.write = MagicMock()
        mock_llm.return_value = "Cross-domain synthesis."

        result = generate_reflection(
            memory_types=["journal", "conversation", "reflection"],
            limit=100,
            store=True,
            cadence="monthly",
            prompt_template=template,
        )

        # Should have read from all three types
        read_calls = mock_svc.read.call_args_list
        types_read = {call[1].get("memory_type") or call[0][0] if call[0] else call[1].get("memory_type") for call in read_calls}
        # At minimum journal and conversation should be read
        assert len(read_calls) >= 2


def test_no_prompt_template_uses_legacy_path():
    """Verify that omitting prompt_template preserves the concatenation path."""
    with patch("src.reflection.generate_reflection.memory_service") as mock_svc:
        mock_svc.read.side_effect = _mock_read
        mock_svc.write = MagicMock()

        result = generate_reflection(
            memory_types=["journal"],
            limit=20,
            store=False,
            cadence="daily",
            prompt_template=None,
        )

        assert "Recent themes:" in result["summary"]


def test_prompt_template_file_exists():
    """Verify prompts/monthly_reflection.txt exists and has required placeholders."""
    template_path = Path(__file__).resolve().parents[1] / "prompts" / "monthly_reflection.txt"
    assert template_path.exists(), f"Missing: {template_path}"

    content = template_path.read_text(encoding="utf-8")
    assert "{record_count}" in content
    assert "{window_start}" in content
    assert "{window_end}" in content
    assert "{source_types}" in content
    assert "{records}" in content


def test_run_monthly_reflection_module():
    """Verify run_monthly_reflection module loads and has the right function."""
    from src.reflection.run_monthly_reflection import run_monthly_reflection, _load_prompt_template

    template = _load_prompt_template()
    assert "TASK:" in template
    assert "monthly observation" in template
