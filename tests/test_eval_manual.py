"""
Tests for interactive manual eval CLI (tools/eval_manual.py).
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_script_loads_without_error():
    """Verify eval_manual.py can be imported without crashing."""
    result = subprocess.run(
        [sys.executable, "-c", "import tools.eval_manual"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=10,
    )
    assert result.returncode == 0, f"Import failed: {result.stderr}"


def test_help_flag_works():
    """Verify --help works."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "eval_manual.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--model" in result.stdout


def test_annotation_key_mapping():
    """Verify annotation codes map to expected labels."""
    # Import the module directly
    sys.path.insert(0, str(REPO_ROOT))
    from tools.eval_manual import ANNOTATION_KEY

    assert ANNOTATION_KEY["a"] == "accurate"
    assert ANNOTATION_KEY["h"] == "hallucination"
    assert ANNOTATION_KEY["s"] == "stale context"
    assert ANNOTATION_KEY["v"] == "voice wrong"
    assert ANNOTATION_KEY["t"] == "template collapse"
    assert len(ANNOTATION_KEY) == 5


def test_battery_has_19_questions():
    """Verify the battery contains exactly 19 questions."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools.eval_manual import BATTERY

    total = sum(len(b["questions"]) for b in BATTERY)
    assert total == 19


def test_battery_has_7_categories():
    """Verify the battery covers 7 categories (0-6)."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools.eval_manual import BATTERY

    assert len(BATTERY) == 7


# ---------------------------------------------------------------------------
# _get_annotation — multi-code support
# ---------------------------------------------------------------------------

def _import_eval_manual():
    sys.path.insert(0, str(REPO_ROOT))
    from tools import eval_manual
    return eval_manual


def test_get_annotation_single_code(monkeypatch):
    """A single-character annotation returns one (code, label) tuple
    in a one-element list."""
    eval_manual = _import_eval_manual()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "a")
    result = eval_manual._get_annotation()
    assert result == [("a", "accurate")]


def test_get_annotation_multi_code(monkeypatch):
    """Multi-character input like 'hv' returns multiple (code, label)
    tuples in input order."""
    eval_manual = _import_eval_manual()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "hv")
    result = eval_manual._get_annotation()
    assert result == [("h", "hallucination"), ("v", "voice wrong")]


def test_get_annotation_multi_code_three(monkeypatch):
    """Three-character input like 'sat' returns three tuples in order."""
    eval_manual = _import_eval_manual()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "sat")
    result = eval_manual._get_annotation()
    assert result == [
        ("s", "stale context"),
        ("a", "accurate"),
        ("t", "template collapse"),
    ]


def test_get_annotation_dedupes_repeats(monkeypatch):
    """Duplicate characters within one input are deduped, preserving
    first-seen order."""
    eval_manual = _import_eval_manual()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "hvh")
    result = eval_manual._get_annotation()
    assert result == [("h", "hallucination"), ("v", "voice wrong")]


def test_get_annotation_invalid_then_valid(monkeypatch, capsys):
    """An invalid character causes a re-prompt; the next valid input
    is accepted. The invalid input is shown in the error message."""
    eval_manual = _import_eval_manual()
    inputs = iter(["xy", "h"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = eval_manual._get_annotation()
    assert result == [("h", "hallucination")]
    captured = capsys.readouterr().out
    assert "xy" in captured
    assert "Invalid" in captured


def test_get_annotation_too_many_codes(monkeypatch, capsys):
    """More than 4 characters re-prompts."""
    eval_manual = _import_eval_manual()
    inputs = iter(["ahsvt", "a"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = eval_manual._get_annotation()
    assert result == [("a", "accurate")]
    captured = capsys.readouterr().out
    assert "Too many" in captured


def test_get_annotation_note_path(monkeypatch):
    """The 'n' input enters note mode and returns a single ('note', text)
    tuple in a one-element list."""
    eval_manual = _import_eval_manual()
    inputs = iter(["n", "this is a note"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    result = eval_manual._get_annotation()
    assert result == [("note", "this is a note")]


# ---------------------------------------------------------------------------
# Auto battery mode (--auto)
# ---------------------------------------------------------------------------

def test_auto_battery_flag_accepted():
    """Verify --auto flag is accepted by the argument parser."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "eval_manual.py"), "--auto", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    # --help exits 0 and shows --auto in the output
    assert result.returncode == 0
    assert "--auto" in result.stdout


def test_run_auto_battery_produces_metadata_only(tmp_path, monkeypatch):
    """Verify _run_auto_battery writes metadata (latency, word count)
    but NOT response text to the output file. Per Vault Privacy Rule."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools import eval_manual

    call_count = {"n": 0}
    def mock_send(msg, key):
        call_count["n"] += 1
        return f"Mock response to: {msg}"

    monkeypatch.setattr(eval_manual, "_send_message", mock_send)
    monkeypatch.setattr(eval_manual, "REPO_ROOT", tmp_path)

    eval_manual._run_auto_battery("test-model", "fake-key")

    assert call_count["n"] == 19

    log_dir = tmp_path / "logs" / "eval_manual"
    files = list(log_dir.glob("auto_*.md"))
    assert len(files) == 1

    content = files[0].read_text(encoding="utf-8")
    assert "test-model" in content
    assert "Q1:" in content
    assert "Q19:" in content
    # Metadata IS saved
    assert "latency:" in content
    assert "words:" in content
    # Response text is NOT saved (vault privacy)
    assert "Mock response to:" not in content


# ---------------------------------------------------------------------------
# Compare mode (--compare)
# ---------------------------------------------------------------------------


def test_compare_flag_accepted():
    """Verify --compare flag is accepted by the argument parser."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "eval_manual.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--compare" in result.stdout


def test_compare_cloud_model_is_haiku():
    sys.path.insert(0, str(REPO_ROOT))
    from tools.eval_manual import COMPARE_CLOUD_MODEL
    assert COMPARE_CLOUD_MODEL == "claude-haiku-4-5-20251001"


def test_run_auto_battery_collect_returns_19_results(monkeypatch):
    """_run_auto_battery_collect returns a list of 19 result dicts."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools import eval_manual

    def mock_send(msg, key):
        return "Mock response."

    monkeypatch.setattr(eval_manual, "_send_message", mock_send)

    results = eval_manual._run_auto_battery_collect("test-model", "fake-key")
    assert len(results) == 19
    assert all("latency" in r for r in results)
    assert all("word_count" in r for r in results)
    assert all("question" in r for r in results)


def test_run_compare_produces_comparison_log(tmp_path, monkeypatch):
    """_run_compare saves a comparison metadata file and restores the
    original model."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools import eval_manual

    switch_calls: list[str] = []

    def mock_send(msg, key):
        return "Mock response."

    def mock_switch(model, key):
        switch_calls.append(model)
        return "previous-model"

    monkeypatch.setattr(eval_manual, "_send_message", mock_send)
    monkeypatch.setattr(eval_manual, "_switch_model", mock_switch)
    monkeypatch.setattr(eval_manual, "REPO_ROOT", tmp_path)

    eval_manual._run_compare("fake-key", "qwen3:8b")

    # Should have switched to local, then cloud, then back to local
    assert switch_calls[0] == "qwen3:8b"
    assert switch_calls[1] == "claude-haiku-4-5-20251001"
    assert switch_calls[2] == "qwen3:8b"

    log_dir = tmp_path / "logs" / "eval_manual"
    files = list(log_dir.glob("compare_*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "SIDE-BY-SIDE" in content
    assert "qwen3" in content.lower()
    assert "haiku" in content.lower()
    # Response text not in file (vault privacy)
    assert "Mock response" not in content


def test_run_compare_no_direct_anthropic_imports():
    """The compare path must use Ember's provider dispatch, not
    direct Anthropic API calls."""
    source = (REPO_ROOT / "tools" / "eval_manual.py").read_text(encoding="utf-8")
    assert "import anthropic" not in source
    assert "Anthropic(" not in source
