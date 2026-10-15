"""
tests/test_vault_guard.py

Tests for .claude/hooks/vault_guard.py (Read|Grep|Glob|Edit|Write on
private_vault/).

Every payload is synthetic hook JSON. No test touches the real vault: the
naming tests are string-only with CLAUDE_PROJECT_DIR pointed at an empty
tmp project, and the reach tests build a synthetic tmp project whose
private_vault/ holds placeholder files.

Absence tests (allow cases) are paired with positive controls: the same
payload denies once the condition it depends on is made true (a vault
directory exists, a matching file exists), and each tool gets an
end-to-end deny through the real hook process.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "vault_guard.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("vault_guard_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_hook()


def _payload(tool: str, cwd: str | None = None, **tool_input) -> dict:
    payload = {"tool_name": tool, "tool_input": tool_input}
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def _id(p: dict) -> str:
    return f"{p['tool_name']}:{json.dumps(p['tool_input'])[:70]}"


@pytest.fixture(autouse=True)
def _project_without_vault(tmp_path, monkeypatch):
    """Hermetic default: the project dir is an empty tmp dir, so the reach
    rule never sees the developer's checkout."""
    project = tmp_path / "empty_project"
    project.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project


# ---------------------------------------------------------------------------
# Rule 1: naming (string-only)
# ---------------------------------------------------------------------------

NAMING_DENY = [
    # Read -- Windows, forward-slash, Git Bash, POSIX, relative, case, Win32
    _payload("Read", file_path=r"C:\dev\proj\private_vault\memory\conversation\x.json"),
    _payload("Read", file_path="C:/dev/proj/private_vault/memory/x.json"),
    _payload("Read", file_path="/c/dev/proj/private_vault/memory/x.json"),
    _payload("Read", file_path="/home/user/proj/private_vault/x.json"),
    _payload("Read", file_path="../private_vault/x.json"),
    _payload("Read", file_path=r"..\..\private_vault\x.json"),
    _payload("Read", file_path=r"C:\dev\proj\PRIVATE_VAULT\x.json"),
    _payload("Read", file_path=r"C:\dev\proj\private_vault.\x.json"),
    _payload("Read", file_path=r"C:private_vault\x.json"),
    # Edit / Write (the pre-existing protection, now component-based)
    _payload("Write", file_path=r"C:\dev\proj\private_vault\memory\x.json"),
    _payload("Edit", file_path="/c/dev/proj/private_vault/memory/x.json", old_string="a", new_string="b"),
    # Grep -- path and glob
    _payload("Grep", pattern="x", path=r"C:\dev\proj\private_vault"),
    _payload("Grep", pattern="x", path="../private_vault/memory"),
    _payload("Grep", pattern="x", path=".", glob="private_vault/**"),
    _payload("Grep", pattern="x", path=".", glob="**/private_vault/**/*.json"),
    _payload("Grep", pattern="x", path=".", glob="priv*/**"),
    # Glob -- path and pattern
    _payload("Glob", pattern="**/*.json", path=r"C:\dev\proj\private_vault"),
    _payload("Glob", pattern="*", path="/c/dev/proj/private_vault/memory"),
    _payload("Glob", pattern="private_vault/**"),
    _payload("Glob", pattern="*_vault/*"),
    _payload("Glob", pattern=r"C:\dev\proj\private_vault\**\*"),
]

NAMING_ALLOW = [
    _payload("Read", file_path=r"C:\dev\proj\src\memory\storage.py"),
    _payload("Read", file_path="/c/dev/proj/docs/private_vault.md"),
    _payload("Read", file_path="/c/dev/proj/scripts/private_vault_notes.txt"),
    _payload("Write", file_path=r"C:\dev\proj\src\core\config.py"),
    _payload("Edit", file_path="/c/dev/proj/tests/test_vault.py", old_string="a", new_string="b"),
    _payload("Grep", pattern="private_vault", path="src"),
    _payload("Grep", pattern="x", path=".", glob="*.py"),
    _payload("Glob", pattern="**/*.py"),
    _payload("Glob", pattern="src/**/*.json"),
    _payload("WebFetch", url="https://example.com/private_vault/x"),
]


@pytest.mark.parametrize("payload", NAMING_DENY, ids=_id)
def test_naming_denies(payload):
    assert guard.evaluate(payload) is not None


@pytest.mark.parametrize("payload", NAMING_ALLOW, ids=_id)
def test_naming_allows(payload):
    assert guard.evaluate(payload) is None


def test_every_guarded_tool_has_deny_and_allow_cases():
    guarded = {"Read", "Grep", "Glob", "Edit", "Write"}
    assert guarded <= {p["tool_name"] for p in NAMING_DENY}
    assert guarded <= {p["tool_name"] for p in NAMING_ALLOW}


# ---------------------------------------------------------------------------
# Rule 2: reach (synthetic tmp project with a vault directory)
# ---------------------------------------------------------------------------

@pytest.fixture
def project_with_vault(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / "private_vault" / "memory").mkdir(parents=True)
    (project / "private_vault" / "top.json").write_text("{}", encoding="utf-8")
    (project / "private_vault" / "memory" / "rec.json").write_text("{}", encoding="utf-8")
    (project / "src").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project


# (tool, tool_input) evaluated with cwd = project root
REACH_DENY = [
    ("Glob", {"pattern": "**/*.json"}),
    ("Glob", {"pattern": "**/*"}),
    ("Glob", {"pattern": "*/memory/*"}),
    ("Grep", {"pattern": "x", "glob": "*.json"}),   # matches top.json
    ("Grep", {"pattern": "x", "glob": "mem*"}),     # matches the memory/ dir name
    ("Grep", {"pattern": "x", "glob": "**/*.json"}),
]

REACH_ALLOW = [
    ("Glob", {"pattern": "src/**/*.py"}),
    ("Glob", {"pattern": "*.json"}),                 # top level of the root only
    ("Glob", {"pattern": "**/*", "path": "src"}),    # root below the vault's parent
    ("Grep", {"pattern": "x"}),                      # no glob: .gitignore applies
    ("Grep", {"pattern": "x", "glob": "!*.py"}),     # negated: no whitelist
    ("Grep", {"pattern": "x", "glob": "*.py"}),      # no matching vault entry
    ("Grep", {"pattern": "x", "glob": "**/*.json", "path": "src"}),
]


@pytest.mark.parametrize("tool,tool_input", REACH_DENY)
def test_reach_denies(project_with_vault, tool, tool_input):
    payload = _payload(tool, cwd=str(project_with_vault), **tool_input)
    assert guard.evaluate(payload) is not None


@pytest.mark.parametrize("tool,tool_input", REACH_ALLOW)
def test_reach_allows(project_with_vault, tool, tool_input):
    payload = _payload(tool, cwd=str(project_with_vault), **tool_input)
    assert guard.evaluate(payload) is None


def test_reach_from_an_ancestor_of_the_project(project_with_vault):
    payload = _payload("Glob", cwd=str(project_with_vault.parent), pattern="**/*.json")
    assert guard.evaluate(payload) is not None


@pytest.mark.parametrize("tool,tool_input", REACH_DENY)
def test_reach_is_off_without_a_vault_directory(_project_without_vault, tool, tool_input):
    """Absence counterpart of test_reach_denies: same payloads, no vault."""
    payload = _payload(tool, cwd=str(_project_without_vault), **tool_input)
    assert guard.evaluate(payload) is None


def test_grep_glob_positive_control_matching_vault_file(project_with_vault):
    """`*.py` is allowed above only because no vault entry matches it."""
    payload = _payload("Grep", cwd=str(project_with_vault), pattern="x", glob="*.py")
    assert guard.evaluate(payload) is None
    (project_with_vault / "private_vault" / "stray.py").write_text("", encoding="utf-8")
    assert guard.evaluate(payload) == "*.py"


# ---------------------------------------------------------------------------
# End-to-end through the real hook process, one positive control per tool
# ---------------------------------------------------------------------------

E2E = {
    "Read": ({"file_path": r"C:\x\private_vault\a.json"}, {"file_path": r"C:\x\src\a.py"}),
    "Grep": ({"pattern": "x", "path": "/c/x/private_vault"}, {"pattern": "x", "path": "/c/x/src"}),
    "Glob": ({"pattern": "private_vault/**"}, {"pattern": "src/**"}),
    "Edit": (
        {"file_path": "/c/x/private_vault/a.json", "old_string": "a", "new_string": "b"},
        {"file_path": "/c/x/src/a.py", "old_string": "a", "new_string": "b"},
    ),
    "Write": ({"file_path": "../private_vault/a.json"}, {"file_path": "../src/a.py"}),
}


def _run(payload, project: Path) -> subprocess.CompletedProcess:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)},
    )


@pytest.mark.parametrize("tool", sorted(E2E))
def test_positive_control_hook_process(tool, _project_without_vault):
    deny_input, allow_input = E2E[tool]
    denied = _run({"tool_name": tool, "tool_input": deny_input}, _project_without_vault)
    assert denied.returncode == 0
    output = json.loads(denied.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "Vault Privacy Rule" in output["permissionDecisionReason"]
    allowed = _run({"tool_name": tool, "tool_input": allow_input}, _project_without_vault)
    assert allowed.returncode == 0
    assert allowed.stdout.strip() == ""


@pytest.mark.parametrize("raw", ["", "not json", "[]", json.dumps({"tool_name": "Read"})])
def test_malformed_payload_allows_without_crashing(raw, _project_without_vault):
    result = _run(raw, _project_without_vault)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
