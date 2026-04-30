"""tests/test_lodestone_injection.py

Regression tests for the lodestone living-layer injection gate.

prompt_builder._build_lodestone_living_section is the integration
point where confirmed lodestone records are rendered into the context
packet. The gate suppresses records with taxonomy_category="relational"
when suppress_relational=True is passed (typically by the
relational_honesty / flourishing_over_preference review path), so the
constitution layer, the lodestone layer, and the nature layer do not
all amplify the same relational signal in one prompt.

These tests pin the gate behavior using synthetic resolver output.
The resolver itself is patched at its module-level import location
(src.context.lodestone_resolver.resolve), bypassing vault I/O and the
embedding step entirely. No Ollama, no live vault.
"""

from __future__ import annotations

from unittest.mock import patch

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder


# Synthetic lodestone records. Match the dict shape produced by
# src.context.lodestone_resolver.resolve (see lodestone_resolver.py:91
# to_prompt_text uses rec.get("value", ...) and the suppression filter
# in prompt_builder.py:529-533 reads rec.get("taxonomy_category")).
_RELATIONAL_RECORD = {
    "id": "ls_rel_001",
    "type": "lodestone",
    "value": "honor relationships across distance and time",
    "taxonomy_category": "relational",
    "confirmed": True,
}

_DIRECTIONAL_RECORD = {
    "id": "ls_dir_001",
    "type": "lodestone",
    "value": "ship the work over polishing the surface",
    "taxonomy_category": "directional",
    "confirmed": True,
}


def _packet() -> ContextPacket:
    """Minimal packet -- the resolver is mocked, so user_message
    contents do not affect record selection."""
    return ContextPacket(user_message="anything")


def test_no_suppression_includes_both_records():
    """When suppress_relational is False (default), both records
    rendered by the resolver appear in the lodestone_living section."""
    pb = PromptBuilder()
    with patch(
        "src.context.lodestone_resolver.resolve",
        return_value=[_RELATIONAL_RECORD, _DIRECTIONAL_RECORD],
    ):
        rendered = pb._build_lodestone_living_section(
            _packet(), suppress_relational=False,
        )
    assert "<lodestone_living>" in rendered
    assert _RELATIONAL_RECORD["value"] in rendered
    assert _DIRECTIONAL_RECORD["value"] in rendered


def test_suppress_relational_filters_relational_record():
    """suppress_relational=True drops the taxonomy_category=relational
    record but keeps the directional one."""
    pb = PromptBuilder()
    with patch(
        "src.context.lodestone_resolver.resolve",
        return_value=[_RELATIONAL_RECORD, _DIRECTIONAL_RECORD],
    ):
        rendered = pb._build_lodestone_living_section(
            _packet(), suppress_relational=True,
        )
    assert "<lodestone_living>" in rendered
    assert _RELATIONAL_RECORD["value"] not in rendered
    assert _DIRECTIONAL_RECORD["value"] in rendered


def test_all_relational_with_suppression_returns_empty():
    """When the resolver returns only relational records and
    suppression is on, every record is filtered out and the section
    renders as empty string (no injection at all)."""
    pb = PromptBuilder()
    with patch(
        "src.context.lodestone_resolver.resolve",
        return_value=[_RELATIONAL_RECORD],
    ):
        rendered = pb._build_lodestone_living_section(
            _packet(), suppress_relational=True,
        )
    # to_prompt_text([]) returns "" -- no <lodestone_living> tag at all
    assert rendered == ""
