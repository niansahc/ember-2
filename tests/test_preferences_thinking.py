"""
tests/test_preferences_thinking.py

The Thinking setting (ADR-049) is the vault preference generation_think.
Only the JSON boolean true turns it on; everything else, including a
missing key, reads as off. Synthetic temp vaults only.
"""

from __future__ import annotations

import json

import pytest

from src.core import preferences


def _vault(tmp_path, name, stored):
    """A temp vault whose preferences.json holds `stored` (None = no file)."""
    vault = tmp_path / name
    vault.mkdir()
    if stored is not None:
        (vault / "preferences.json").write_text(json.dumps(stored), encoding="utf-8")
    return vault


def test_default_is_off():
    assert preferences.PREFERENCE_DEFAULTS["generation_think"] is False


def test_nothing_stored_reads_as_off(tmp_path):
    assert preferences.generation_thinking_enabled(_vault(tmp_path, "v", None)) is False


def test_stored_true_reads_as_on(tmp_path):
    """Positive control for the off rows below."""
    vault = _vault(tmp_path, "v", {"generation_think": True})
    assert preferences.generation_thinking_enabled(vault) is True


@pytest.mark.parametrize("value", [False, None, "true", 1, "yes", "on", [True]])
def test_anything_but_json_true_reads_as_off(tmp_path, value):
    vault = _vault(tmp_path, "v", {"generation_think": value})
    assert preferences.generation_thinking_enabled(vault) is False


def test_each_vault_has_its_own_value(tmp_path):
    """A vault swap changes the value: the resolver reads the vault it is given."""
    on = _vault(tmp_path, "on", {"generation_think": True})
    off = _vault(tmp_path, "off", {"generation_think": False})
    assert preferences.generation_thinking_enabled(on) is True
    assert preferences.generation_thinking_enabled(off) is False


def test_written_value_round_trips(tmp_path):
    vault = _vault(tmp_path, "v", None)
    preferences.update({"generation_think": True}, vault_path=vault)
    assert preferences.generation_thinking_enabled(vault) is True
    preferences.update({"generation_think": False}, vault_path=vault)
    assert preferences.generation_thinking_enabled(vault) is False
