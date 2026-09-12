"""Tests for src/llm/adapter.py _get_num_ctx() resolution.

B-QUAL-001 regression: qwen3:8b was previously running at runtime KvSize=4096
because num_ctx was not being passed in chat options, so Ollama clamped to its
own internal default. Message-level truncation with `keep=4` dropped the system
prompt and the model collapsed to non-English token distribution mid-response.

The fix is two-part:
  - MODEL_CONTEXT_WINDOWS["qwen3:8b"] now matches the modelfile-declared 40960.
  - _get_num_ctx(model) resolves per-call model and applies an 80% safety
    factor for response-token headroom, with explicit user preference winning.
"""
from __future__ import annotations

from unittest.mock import patch

from src.llm.adapter import LLMAdapter


def _bare_adapter(model: str = "qwen3:8b") -> LLMAdapter:
    """Construct a bare adapter without invoking __init__ — _get_num_ctx is
    independent of provider clients."""
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = model
    return adapter


def test_get_num_ctx_returns_model_aware_default_for_qwen3_8b() -> None:
    """qwen3:8b at 40960 declared * 0.8 factor → 32768 prompt budget."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value=None):
        assert adapter._get_num_ctx() == 32768


def test_get_num_ctx_per_call_model_override_resolves_against_call_arg() -> None:
    """When _chat_ollama passes a per-call model, num_ctx must derive from
    that model, not self.model. Otherwise multi-model sessions get the
    wrong window. phi3:mini = 4096 * 0.8 = 3276."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value=None):
        assert adapter._get_num_ctx("phi3:mini") == 3276


def test_get_num_ctx_explicit_user_preference_wins_over_model_default() -> None:
    """User-set context_length must be honored even if the model's window is larger."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value=16384):
        assert adapter._get_num_ctx() == 16384


def test_get_num_ctx_unknown_model_falls_back_to_8192_base() -> None:
    """Models not in MODEL_CONTEXT_WINDOWS get the conservative 8192 base * 0.8 = 6553."""
    adapter = _bare_adapter("unknown-model:99b")
    with patch("src.core.preferences.get", return_value=None):
        assert adapter._get_num_ctx() == 6553


def test_get_num_ctx_clamps_below_2048_to_floor() -> None:
    """The [2048, 131072] clamp protects against pathological preferences."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value=512):
        assert adapter._get_num_ctx() == 2048


def test_get_num_ctx_invalid_preference_falls_back_to_model_default() -> None:
    """A non-int preference value falls through to the model-aware default,
    not to a hardcoded number."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value="garbage"):
        assert adapter._get_num_ctx() == 32768


def test_get_num_ctx_clamps_user_preference_to_model_declared_ceiling() -> None:
    """A user pref of 200000 on qwen3:8b must resolve to 40960 (the model's
    true declared context length), not 131072 (the prior hard upper clamp).
    Otherwise Ollama silently truncates at the real ceiling and the response
    degrades mid-sentence -- the original B-QUAL-001 / 2026-04-26 failure
    pattern that this clamp is meant to prevent."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value=200_000):
        assert adapter._get_num_ctx() == 40960


def test_get_num_ctx_clamps_above_model_ceiling_for_unknown_model() -> None:
    """Unknown models fall back to the conservative 8192 declared base.
    A high user pref must not lift the resolved value above that base
    just because the prior hard ceiling was 131072."""
    adapter = _bare_adapter("unknown-model:99b")
    with patch("src.core.preferences.get", return_value=999_999):
        assert adapter._get_num_ctx() == 8192


# --- Sweep-candidate entries and the default ceiling -----------------------
#
# Candidates were running at num_ctx 6553 (the unlisted-model fallback of
# 8192 * 0.8) against the incumbent's 32768, so every model-sweep result was
# scored at one fifth the incumbent's context budget. Adding the entries alone
# would have swung the bias the other way: a 262144-declared model resolves to
# 209715 under the bare 0.8 factor, a ~200k KV cache. The default is therefore
# capped, which lands every candidate on the incumbent's 32768.


def test_get_num_ctx_sweep_candidates_match_incumbent() -> None:
    """Every sweep candidate resolves to the same budget as qwen3:8b.

    Parity is the point: a ranking produced at unequal context budgets does
    not measure model quality.
    """
    incumbent = _bare_adapter("qwen3:8b")._get_num_ctx()
    with patch("src.core.preferences.get", return_value=None):
        for tag in (
            "qwen3:32b",
            "qwen3.6:35b-a3b",
            "glm-4.7-flash",
            "gemma4:26b",
            "qwen3.5:9b",
        ):
            assert _bare_adapter(tag)._get_num_ctx() == incumbent, tag


def test_get_num_ctx_default_is_capped_for_long_context_models() -> None:
    """A 262144-declared model defaults to the ceiling, not 209715."""
    adapter = _bare_adapter("gemma4:26b")
    with patch("src.core.preferences.get", return_value=None):
        assert adapter._get_num_ctx() == 32768


def test_get_num_ctx_ceiling_does_not_clamp_explicit_preference() -> None:
    """The ceiling applies to the computed default only.

    A user who explicitly asks for more than the ceiling still gets it, up to
    the model's declared window — that clamp is unchanged.
    """
    adapter = _bare_adapter("gemma4:26b")
    with patch("src.core.preferences.get", return_value=131072):
        assert adapter._get_num_ctx() == 131072


def test_get_num_ctx_ceiling_is_a_noop_for_the_shipped_model() -> None:
    """qwen3:8b already resolved to 32768, so production is unchanged."""
    adapter = _bare_adapter("qwen3:8b")
    with patch("src.core.preferences.get", return_value=None):
        assert adapter._get_num_ctx() == 32768
