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
