"""
tests/test_should_skip_for_reflection.py

Unit tests for _should_skip_for_reflection() in
src/reflection/generate_reflection.py.

IMPORTANT: _should_skip_for_reflection() receives pre-normalized
(lowercased, whitespace-collapsed) text — it is always called as
_should_skip_for_reflection(_normalize_text(raw_text)) in the
reflection generator. Test inputs must be lowercase to match real
call-site behavior.

Covers every filter branch:
- empty text
- skip_markers (server noise, formatting complaints)
- Unicode box-drawing characters (file trees)
- short URLs
- multi-turn exchanges
- code fences
- code/debug prefixes
- import/from prefixes
- def statements

Includes regression tests for the five additions made in v0.7.x:
- "shorter messages please"
- "shorter responses"
- "that's a long response"
- Unicode box-drawing characters (├──, │)
- short https:// URLs
- multi-turn exchanges starting with "user:"

And a fix to "line " → ", line " to stop matching "pipeline".
"""

from src.reflection.generate_reflection import _should_skip_for_reflection


# ---------------------------------------------------------------------------
# Empty / falsy
# ---------------------------------------------------------------------------

def test_empty_string_is_skipped():
    assert _should_skip_for_reflection("") is True


# ---------------------------------------------------------------------------
# skip_markers — server / runtime noise
# ---------------------------------------------------------------------------

def test_uvicorn_marker_is_skipped():
    assert _should_skip_for_reflection("uvicorn src.api.main:app --reload") is True


def test_traceback_marker_is_skipped():
    assert _should_skip_for_reflection("traceback (most recent call last): something failed") is True


def test_line_in_traceback_is_skipped():
    assert _should_skip_for_reflection('file "c:\\users\\foo\\bar.py", line 42, in run') is True


def test_loading_weights_is_skipped():
    assert _should_skip_for_reflection("loading weights: 100%|####| 103/103") is True


def test_application_startup_complete_is_skipped():
    assert _should_skip_for_reflection("application startup complete") is True


def test_waiting_for_application_startup_is_skipped():
    assert _should_skip_for_reflection("waiting for application startup.") is True


def test_warning_marker_is_skipped():
    assert _should_skip_for_reflection("warning: something deprecated in module x") is True


def test_pipeline_word_does_not_trigger_line_marker():
    # Regression: "line " was too broad and matched "pipeline today".
    # Fixed to ", line " — "pipeline" must not be skipped.
    text = "i worked through the retrieval pipeline today and fixed a long-standing bug in the context service."
    assert _should_skip_for_reflection(text) is False


# ---------------------------------------------------------------------------
# skip_markers — formatting complaints (regression: added in v0.7.x)
# ---------------------------------------------------------------------------

def test_shorter_messages_please_is_skipped():
    assert _should_skip_for_reflection("user: shorter messages please. i've reminded you 5 times in this conversation.") is True


def test_shorter_responses_is_skipped():
    assert _should_skip_for_reflection("user: shorter responses. remember i have adhd and long responses are not accessible.") is True


def test_thats_a_long_response_is_skipped():
    assert _should_skip_for_reflection("user: that's a long response again. please respond with bullet points first.") is True


def test_shorter_messages_mid_sentence_is_skipped():
    # marker match is substring — catches it anywhere in the text
    assert _should_skip_for_reflection("i keep saying shorter messages please but it never sticks.") is True


# ---------------------------------------------------------------------------
# Unicode box-drawing characters (regression: added in v0.7.x)
# ---------------------------------------------------------------------------

def test_file_tree_with_branch_is_skipped():
    text = "src/\n├── __init__.py\n├── api/\n│   └── main.py"
    assert _should_skip_for_reflection(text) is True


def test_file_tree_with_pipe_only_is_skipped():
    text = "context/\n│   ├── formatter.py\n│   └── service.py"
    assert _should_skip_for_reflection(text) is True


# ---------------------------------------------------------------------------
# Short URLs (regression: added in v0.7.x)
# ---------------------------------------------------------------------------

def test_short_url_only_is_skipped():
    assert _should_skip_for_reflection("https://chastainblanc.tail682db9.ts.net (tailnet only)") is True


def test_url_with_proxy_config_is_skipped():
    text = "https://chastainblanc.tail682db9.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:3000"
    assert _should_skip_for_reflection(text) is True


def test_long_text_with_url_passes():
    # URL present but text is over 200 chars — substantive content that happens to mention a URL.
    text = (
        "i set up the tailnet endpoint for ember-2 today so i can access it remotely from my phone. "
        "the url https://chastainblanc.tail682db9.ts.net proxies to the local fastapi instance on port 3000. "
        "this means i can use open webui from anywhere on my tailnet without exposing anything publicly."
    )
    assert len(text) >= 200
    assert _should_skip_for_reflection(text) is False


# ---------------------------------------------------------------------------
# Multi-turn exchanges (regression: added in v0.7.x)
# ---------------------------------------------------------------------------

def test_user_prefix_with_second_user_is_skipped():
    text = "user: what have i been working on? user: okay thanks that helps"
    assert _should_skip_for_reflection(text) is True


def test_user_prefix_with_assistant_in_tail_is_skipped():
    text = "user: ember-2 assistant: i understand you're seeking clarity without reassurance. that's a solid approach."
    assert _should_skip_for_reflection(text) is True


def test_single_user_turn_passes():
    # Starts with "user:" but no second speaker — passes this check.
    text = "user: i took a break and meal planned and now i am back and feeling much better about the work ahead."
    assert _should_skip_for_reflection(text) is False


# ---------------------------------------------------------------------------
# Code fences
# ---------------------------------------------------------------------------

def test_code_fence_is_skipped():
    text = "here is the fix:\n```python\ndef hello():\n    print('hello')\n```"
    assert _should_skip_for_reflection(text) is True


# ---------------------------------------------------------------------------
# Code/debug prefixes
# ---------------------------------------------------------------------------

def test_assistant_prefix_with_import_is_skipped():
    assert _should_skip_for_reflection("assistant: import os\nfrom pathlib import path") is True


def test_user_prefix_with_def_is_skipped():
    assert _should_skip_for_reflection("user: def run():\n    pass") is True


def test_user_from_import_is_skipped():
    assert _should_skip_for_reflection("user: from src.memory import memoryservice") is True


def test_assistant_from_import_is_skipped():
    assert _should_skip_for_reflection("assistant: from pathlib import path") is True


def test_user_import_is_skipped():
    assert _should_skip_for_reflection("user: import json") is True


def test_def_statement_anywhere_is_skipped():
    assert _should_skip_for_reflection("here is the updated function: def process(items): return items") is True


# ---------------------------------------------------------------------------
# Substantive content that should pass
# ---------------------------------------------------------------------------

def test_personal_update_passes():
    text = "i took a break, meal planned, and came back less enraged. the estrogen patch timing was the issue, not the work."
    assert _should_skip_for_reflection(text) is False


def test_work_reflection_passes():
    text = "spent the day debugging the retrieval pipeline. the _relevance_hits gate was dropping valid results because it matched exact tokens instead of semantic meaning."
    assert _should_skip_for_reflection(text) is False


def test_frustration_with_substance_passes():
    text = "you told me to use phi3.mini because it's faster and you wrote the prompt. i need to be more careful about taking model recommendations at face value."
    assert _should_skip_for_reflection(text) is False
