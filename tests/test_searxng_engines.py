"""
tests/test_searxng_engines.py

SearXNG sends every query to every enabled engine, so the enabled set is a
privacy decision: only engines without IP-linked query logs or profiling.
`use_default_settings: true` alone leaves SearXNG's whole default list on
(google among them); keep_only is what narrows it.
"""

from __future__ import annotations

import yaml

from tests.conftest import REPO_ROOT

_SETTINGS = REPO_ROOT / "config" / "searxng" / "settings.yml"


def test_searxng_keeps_only_the_privacy_engines():
    settings = yaml.safe_load(_SETTINGS.read_text(encoding="utf-8"))
    keep_only = settings["use_default_settings"]["engines"]["keep_only"]
    assert sorted(keep_only) == ["brave", "duckduckgo", "startpage"]


def test_searxng_settings_carry_the_privacy_rule_comment():
    text = _SETTINGS.read_text(encoding="utf-8")
    assert "every query to every enabled engine" in text
    assert "IP-linked query logs" in text
