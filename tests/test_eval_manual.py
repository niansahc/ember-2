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


def test_run_auto_battery_produces_output(tmp_path, monkeypatch):
    """Verify _run_auto_battery writes a markdown file with Q/A pairs."""
    sys.path.insert(0, str(REPO_ROOT))
    from tools import eval_manual

    # Mock _send_message to avoid needing a running API
    call_count = {"n": 0}
    def mock_send(msg, key):
        call_count["n"] += 1
        return f"Mock response to: {msg}"

    monkeypatch.setattr(eval_manual, "_send_message", mock_send)
    monkeypatch.setattr(eval_manual, "REPO_ROOT", tmp_path)

    eval_manual._run_auto_battery("test-model", "fake-key")

    # Should have sent 19 messages
    assert call_count["n"] == 19

    # Should have written a file
    log_dir = tmp_path / "logs" / "eval_manual"
    files = list(log_dir.glob("auto_*.md"))
    assert len(files) == 1

    content = files[0].read_text(encoding="utf-8")
    assert "test-model" in content
    assert "Q1:" in content
    assert "Q19:" in content
    assert "Mock response to:" in content
