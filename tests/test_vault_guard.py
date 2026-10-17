"""
tests/test_vault_guard.py

Tests for .claude/hooks/vault_guard.py (Read|Grep|Glob|Edit|Write on
private_vault/ and the configured PRIVATE_VAULT_PATH).

Every payload is synthetic hook JSON. No test touches the real vault or
the developer's .env: CLAUDE_PROJECT_DIR points at a tmp project and
PRIVATE_VAULT_PATH is removed from the environment (conftest pins it to a
throwaway vault). The naming tests are string-only; the reach tests build
a synthetic tmp project whose private_vault/ holds placeholder files; the
configured-vault tests give the tmp project a tmp .env pointing at a tmp
vault outside it.

Absence tests (allow cases) are paired with positive controls: the same
payload denies once the condition it depends on is made true (a vault
directory exists, a matching file exists), and each tool gets an
end-to-end deny through the real hook process.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import (
    HOOK_VAULT_NAME,
    hook_output,
    hook_payload,
    load_hook,
    payload_id,
    relpath,
    run_hook,
)

guard = load_hook("vault_guard.py")
GUARDED = set(guard.PATH_FIELDS)

# Hermetic default: the reach rule never sees the developer's checkout, and
# the configured vault comes only from a test's own tmp .env.
pytestmark = pytest.mark.usefixtures("hermetic_hook_project")


# ---------------------------------------------------------------------------
# Rule 1: naming (string-only)
# ---------------------------------------------------------------------------

NAMING_DENY = [
    # Read -- Windows, forward-slash, Git Bash, POSIX, relative, case, Win32
    hook_payload("Read", file_path=r"C:\dev\proj\private_vault\memory\conversation\x.json"),
    hook_payload("Read", file_path="C:/dev/proj/private_vault/memory/x.json"),
    hook_payload("Read", file_path="/c/dev/proj/private_vault/memory/x.json"),
    hook_payload("Read", file_path="/home/user/proj/private_vault/x.json"),
    hook_payload("Read", file_path="../private_vault/x.json"),
    hook_payload("Read", file_path=r"..\..\private_vault\x.json"),
    hook_payload("Read", file_path=r"C:\dev\proj\PRIVATE_VAULT\x.json"),
    hook_payload("Read", file_path=r"C:\dev\proj\private_vault.\x.json"),
    hook_payload("Read", file_path=r"C:private_vault\x.json"),
    # Edit / Write (the pre-existing protection, now component-based)
    hook_payload("Write", file_path=r"C:\dev\proj\private_vault\memory\x.json"),
    hook_payload("Edit", file_path="/c/dev/proj/private_vault/memory/x.json", old_string="a", new_string="b"),
    # Grep -- path and glob
    hook_payload("Grep", pattern="x", path=r"C:\dev\proj\private_vault"),
    hook_payload("Grep", pattern="x", path="../private_vault/memory"),
    hook_payload("Grep", pattern="x", path=".", glob="private_vault/**"),
    hook_payload("Grep", pattern="x", path=".", glob="**/private_vault/**/*.json"),
    hook_payload("Grep", pattern="x", path=".", glob="priv*/**"),
    # Glob -- path and pattern
    hook_payload("Glob", pattern="**/*.json", path=r"C:\dev\proj\private_vault"),
    hook_payload("Glob", pattern="*", path="/c/dev/proj/private_vault/memory"),
    hook_payload("Glob", pattern="private_vault/**"),
    hook_payload("Glob", pattern="*_vault/*"),
    hook_payload("Glob", pattern=r"C:\dev\proj\private_vault\**\*"),
]

NAMING_ALLOW = [
    hook_payload("Read", file_path=r"C:\dev\proj\src\memory\storage.py"),
    hook_payload("Read", file_path="/c/dev/proj/docs/private_vault.md"),
    hook_payload("Read", file_path="/c/dev/proj/scripts/private_vault_notes.txt"),
    hook_payload("Write", file_path=r"C:\dev\proj\src\core\config.py"),
    hook_payload("Edit", file_path="/c/dev/proj/tests/test_vault.py", old_string="a", new_string="b"),
    hook_payload("Grep", pattern="private_vault", path="src"),
    hook_payload("Grep", pattern="x", path=".", glob="*.py"),
    hook_payload("Glob", pattern="**/*.py"),
    hook_payload("Glob", pattern="src/**/*.json"),
    hook_payload("WebFetch", url="https://example.com/private_vault/x"),
]


@pytest.mark.parametrize("payload", NAMING_DENY, ids=payload_id)
def test_naming_denies(payload):
    assert guard.evaluate(payload) is not None


@pytest.mark.parametrize("payload", NAMING_ALLOW, ids=payload_id)
def test_naming_allows(payload):
    assert guard.evaluate(payload) is None


def test_every_guarded_tool_has_deny_and_allow_cases():
    assert GUARDED <= {p["tool_name"] for p in NAMING_DENY}
    assert GUARDED <= {p["tool_name"] for p in NAMING_ALLOW}


# ---------------------------------------------------------------------------
# Rule 2: reach (synthetic tmp project with a vault directory)
# ---------------------------------------------------------------------------

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
def test_reach_denies(in_repo_vault_project, tool, tool_input):
    payload = hook_payload(tool, cwd=str(in_repo_vault_project), **tool_input)
    assert guard.evaluate(payload) is not None


@pytest.mark.parametrize("tool,tool_input", REACH_ALLOW)
def test_reach_allows(in_repo_vault_project, tool, tool_input):
    payload = hook_payload(tool, cwd=str(in_repo_vault_project), **tool_input)
    assert guard.evaluate(payload) is None


def test_reach_from_an_ancestor_of_the_project(in_repo_vault_project):
    payload = hook_payload("Glob", cwd=str(in_repo_vault_project.parent), pattern="**/*.json")
    assert guard.evaluate(payload) is not None


@pytest.mark.parametrize("tool,tool_input", REACH_DENY)
def test_reach_is_off_without_a_vault_directory(hermetic_hook_project, tool, tool_input):
    """Absence counterpart of test_reach_denies: same payloads, no vault."""
    payload = hook_payload(tool, cwd=str(hermetic_hook_project), **tool_input)
    assert guard.evaluate(payload) is None


def test_grep_glob_positive_control_matching_vault_file(in_repo_vault_project):
    """`*.py` is allowed above only because no vault entry matches it."""
    payload = hook_payload("Grep", cwd=str(in_repo_vault_project), pattern="x", glob="*.py")
    assert guard.evaluate(payload) is None
    (in_repo_vault_project / "private_vault" / "stray.py").write_text("", encoding="utf-8")
    assert guard.evaluate(payload) == "*.py"


# The in-repo vault by location: a cwd inside it, where no field names it.
IN_VAULT_CWD = [
    ("Read", {"file_path": "rec.json"}),
    ("Grep", {"pattern": "x"}),
    ("Glob", {"pattern": "*"}),
]


@pytest.mark.parametrize("tool,tool_input", IN_VAULT_CWD)
def test_in_repo_vault_by_location_from_a_cwd_inside_it(in_repo_vault_project, tool, tool_input):
    cwd = in_repo_vault_project / "private_vault" / "memory"
    assert guard.evaluate(hook_payload(tool, cwd=str(cwd), **tool_input)) is not None


@pytest.mark.parametrize("tool,tool_input", IN_VAULT_CWD)
def test_in_repo_vault_by_location_is_off_without_the_directory(hermetic_hook_project, tool, tool_input):
    """Absence counterpart of the test above: same payloads, no vault."""
    cwd = hermetic_hook_project / "private_vault" / "memory"
    assert guard.evaluate(hook_payload(tool, cwd=str(cwd), **tool_input)) is None


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


def _run(payload) -> subprocess.CompletedProcess:
    """The real hook process, with the environment the fixtures set up."""
    return run_hook("vault_guard.py", payload)


@pytest.mark.parametrize("tool", sorted(E2E))
def test_positive_control_hook_process(tool):
    deny_input, allow_input = E2E[tool]
    denied = _run({"tool_name": tool, "tool_input": deny_input})
    assert denied.returncode == 0
    output = hook_output(denied)
    assert output["permissionDecision"] == "deny"
    assert "Vault Privacy Rule" in output["permissionDecisionReason"]
    allowed = _run({"tool_name": tool, "tool_input": allow_input})
    assert allowed.returncode == 0
    assert allowed.stdout.strip() == ""


@pytest.mark.parametrize("raw", ["", "not json", "[]", json.dumps({"tool_name": "Read"})])
def test_malformed_payload_allows_without_crashing(raw):
    result = _run(raw)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Configured vault: PRIVATE_VAULT_PATH from a tmp .env, outside the project
# ---------------------------------------------------------------------------

WINDOWS = sys.platform == "win32"


# name -> (tool, cwd, tool_input) built from the `configured_vault` paths.
CONFIG_DENY = {
    "read-native": lambda c: ("Read", c.project, {"file_path": str(c.vault / "zz_probe" / "none.json")}),
    "read-forward-slash": lambda c: ("Read", c.project, {"file_path": (c.vault / "zz_probe" / "none.json").as_posix()}),
    "read-upper-case": lambda c: ("Read", c.project, {"file_path": str(c.vault / "zz_probe" / "none.json").upper()}),
    "read-trailing-dot": lambda c: ("Read", c.project, {"file_path": f"{c.vault}.{os.sep}none.json"}),
    "read-relative": lambda c: ("Read", c.project, {"file_path": relpath(c.vault / "zz_probe" / "none.json", c.project)}),
    "read-relative-backslash": lambda c: ("Read", c.project, {"file_path": relpath(c.vault / "zz_probe" / "none.json", c.project, "\\")}),
    "read-relative-from-subdir": lambda c: ("Read", c.project / "src", {"file_path": relpath(c.vault / "memory" / "rec.json", c.project / "src")}),
    "read-cwd-inside-vault": lambda c: ("Read", c.vault / "memory", {"file_path": "rec.json"}),
    "edit": lambda c: ("Edit", c.project, {"file_path": str(c.vault / "memory" / "rec.json"), "old_string": "a", "new_string": "b"}),
    "edit-relative": lambda c: ("Edit", c.project, {"file_path": relpath(c.vault / "memory" / "rec.json", c.project), "old_string": "a", "new_string": "b"}),
    "write": lambda c: ("Write", c.project, {"file_path": relpath(c.vault / "zz_probe" / "new.json", c.project)}),
    "grep-path-vault": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.vault)}),
    "grep-path-relative": lambda c: ("Grep", c.project, {"pattern": "x", "path": relpath(c.vault / "memory", c.project)}),
    "grep-cwd-inside-vault": lambda c: ("Grep", c.vault, {"pattern": "x"}),
    "grep-reach-no-glob": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.outside)}),
    "grep-reach-unanchored-glob": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.outside), "glob": "*.json"}),
    "grep-reach-negated-glob": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.outside), "glob": "!*.md"}),
    "grep-reach-negated-anchored-glob": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.outside), "glob": "!docs/**"}),
    "grep-reach-anchored-glob": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.outside), "glob": "**/*.json"}),
    "glob-path-vault": lambda c: ("Glob", c.project, {"pattern": "*", "path": str(c.vault / "memory")}),
    "glob-absolute-pattern": lambda c: ("Glob", c.project, {"pattern": c.vault.as_posix() + "/**/*.json"}),
    "glob-relative-wildcard-pattern": lambda c: ("Glob", c.project, {"pattern": relpath(c.outside, c.project) + "/Ember*/**"}),
    "glob-vault-top-level": lambda c: ("Glob", c.project, {"pattern": relpath(c.vault, c.project) + "/*"}),
    "glob-reach": lambda c: ("Glob", c.project, {"pattern": "**/*.json", "path": str(c.outside)}),
    "glob-reach-wildcard-dir": lambda c: ("Glob", c.project, {"pattern": "*/memory/*", "path": str(c.outside)}),
}

WINDOWS_DENY = {
    "read-git-bash": lambda c: ("Read", c.project, {"file_path": "/" + c.vault.drive[0].lower() + c.vault.as_posix()[2:] + "/zz_probe/none.json"}),
    "read-wsl-mount": lambda c: ("Read", c.project, {"file_path": "/mnt/" + c.vault.drive[0].lower() + c.vault.as_posix()[2:] + "/zz_probe/none.json"}),
    "read-device-prefix": lambda c: ("Read", c.project, {"file_path": "\\\\?\\" + str(c.vault / "zz_probe" / "none.json")}),
    "read-drive-relative": lambda c: ("Read", c.project, {"file_path": c.vault.drive + relpath(c.vault / "zz_probe" / "none.json", c.project, "\\")}),
}

CONFIG_ALLOW = {
    "read-src": lambda c: ("Read", c.project, {"file_path": str(c.project / "src" / "a.py")}),
    "read-sibling-sharing-the-vault-prefix": lambda c: ("Read", c.project, {"file_path": str(c.outside / f"{HOOK_VAULT_NAME}_other" / "x.json")}),
    "read-beside-the-vault": lambda c: ("Read", c.project, {"file_path": str(c.outside / "notes.txt")}),
    "edit-src": lambda c: ("Edit", c.project, {"file_path": "src/a.py", "old_string": "a", "new_string": "b"}),
    "write-src": lambda c: ("Write", c.project, {"file_path": "src/new.py"}),
    "grep-project-root": lambda c: ("Grep", c.project, {"pattern": "x"}),
    "grep-anchored-glob-beside-the-vault": lambda c: ("Grep", c.project, {"pattern": "x", "path": str(c.outside), "glob": f"{HOOK_VAULT_NAME}_other/**"}),
    "glob-top-level-of-an-ancestor": lambda c: ("Glob", c.project, {"pattern": "*", "path": str(c.outside)}),
    "glob-src": lambda c: ("Glob", c.project, {"pattern": "src/**/*.py"}),
}


def _config_payload(case, c) -> dict:
    tool, cwd, tool_input = case(c)
    return hook_payload(tool, cwd=str(cwd), **tool_input)


@pytest.mark.parametrize("name", sorted(CONFIG_DENY))
def test_configured_vault_denies(name, configured_vault):
    assert guard.evaluate(_config_payload(CONFIG_DENY[name], configured_vault)) is not None


@pytest.mark.skipif(not WINDOWS, reason="Windows path forms")
@pytest.mark.parametrize("name", sorted(WINDOWS_DENY))
def test_configured_vault_denies_windows_forms(name, configured_vault):
    assert guard.evaluate(_config_payload(WINDOWS_DENY[name], configured_vault)) is not None


@pytest.mark.parametrize("name", sorted(CONFIG_ALLOW))
def test_configured_vault_allows(name, configured_vault):
    assert guard.evaluate(_config_payload(CONFIG_ALLOW[name], configured_vault)) is None


def test_every_guarded_tool_has_configured_deny_and_allow_cases():
    stub = SimpleNamespace(project=Path("p"), outside=Path("o"), vault=Path("o") / HOOK_VAULT_NAME)
    assert GUARDED <= {case(stub)[0] for case in CONFIG_DENY.values()}
    assert GUARDED <= {case(stub)[0] for case in CONFIG_ALLOW.values()}


@pytest.mark.parametrize("off", ["no-env-file", "no-key", "vault-missing"])
def test_configured_rules_are_off_without_a_configured_vault(off, configured_vault):
    """Absence counterpart of test_configured_vault_denies: every deny
    payload, built before the configuration is taken away."""
    payloads = {name: _config_payload(case, configured_vault) for name, case in CONFIG_DENY.items()}
    env = configured_vault.project / ".env"
    if off == "no-env-file":
        env.unlink()
    elif off == "no-key":
        env.write_text("OTHER_KEY=1\n", encoding="utf-8")
    else:
        shutil.rmtree(configured_vault.vault)
    assert [name for name, p in payloads.items() if guard.evaluate(p) is not None] == []


def test_process_environment_wins_over_the_env_file(configured_vault, monkeypatch):
    payload = _config_payload(CONFIG_DENY["read-relative"], configured_vault)
    other = configured_vault.outside / f"{HOOK_VAULT_NAME}_other"
    (configured_vault.project / ".env").write_text(f"PRIVATE_VAULT_PATH={other}\n", encoding="utf-8")
    assert guard.evaluate(payload) is None
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(configured_vault.vault))
    assert guard.evaluate(payload) is not None


def test_relative_value_resolves_against_the_project(configured_vault, monkeypatch, tmp_path):
    (configured_vault.project / ".env").write_text(
        f"PRIVATE_VAULT_PATH=../outside/{HOOK_VAULT_NAME}\n", encoding="utf-8"
    )
    elsewhere = tmp_path / "a" / "b"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    assert guard.evaluate(_config_payload(CONFIG_DENY["read-native"], configured_vault)) is not None


def test_configured_vault_inside_the_project_keeps_gitignore_rules(in_repo_vault_project):
    (in_repo_vault_project / ".env").write_text("PRIVATE_VAULT_PATH=private_vault\n", encoding="utf-8")
    root = str(in_repo_vault_project)
    assert len(guard.vault_dirs()) == 1  # the same directory twice
    assert guard.evaluate(hook_payload("Grep", cwd=root, pattern="x")) is None
    assert guard.evaluate(hook_payload("Grep", cwd=root, pattern="x", glob="*.json")) is not None


def test_configured_vault_through_a_link(configured_vault, tmp_path):
    link = tmp_path / "link_to_vault"
    try:
        if WINDOWS:
            import _winapi

            _winapi.CreateJunction(str(configured_vault.vault), str(link))
        else:
            os.symlink(configured_vault.vault, link, target_is_directory=True)
    except (OSError, AttributeError) as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")
    payload = hook_payload("Read", cwd=str(configured_vault.project), file_path=str(link / "memory" / "rec.json"))
    assert guard.evaluate(payload) is not None


@pytest.mark.skipif(not WINDOWS, reason="8.3 short names are a Windows feature")
def test_configured_vault_by_short_name(configured_vault):
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(configured_vault.vault), buf, 1024)
    if not buf.value or buf.value.lower() == str(configured_vault.vault).lower():
        pytest.skip("8.3 short names are disabled on this volume")
    payload = hook_payload("Read", cwd=str(configured_vault.project), file_path=buf.value + "\\zz_probe\\none.json")
    assert guard.evaluate(payload) is not None


def test_configured_vault_by_home_relative_path(configured_vault):
    try:
        rel = configured_vault.vault.relative_to(Path.home())
    except ValueError:
        pytest.skip("the tmp vault is not under the home directory here")
    payload = hook_payload("Read", cwd=str(configured_vault.project), file_path=f"~/{rel.as_posix()}/zz_probe/none.json")
    assert guard.evaluate(payload) is not None


# --- the .env mtime cache --------------------------------------------------

def test_cache_skips_the_parse_until_the_env_file_changes(configured_vault, monkeypatch):
    calls = []
    real = guard.env_file_value
    monkeypatch.setattr(guard, "env_file_value", lambda key, path: calls.append(key) or real(key, path))
    payload = _config_payload(CONFIG_DENY["read-native"], configured_vault)
    assert guard.evaluate(payload) is not None
    assert guard.evaluate(payload) is not None
    assert len(calls) == 1
    assert (configured_vault.project / ".claude" / guard._CACHE_NAME).is_file()

    other = configured_vault.outside / f"{HOOK_VAULT_NAME}_other"
    (configured_vault.project / ".env").write_text(f"PRIVATE_VAULT_PATH={other}\n", encoding="utf-8")
    assert guard.evaluate(payload) is None
    assert guard.evaluate(hook_payload("Read", cwd=str(configured_vault.project), file_path=str(other / "x.json"))) is not None
    assert len(calls) == 2


def test_cache_file_is_covered_by_env_guard():
    env_guard = load_hook("env_guard.py")
    assert env_guard.names_env_file(f".claude/{guard._CACHE_NAME}")
    assert env_guard.names_env_file(f".claude/{guard._CACHE_NAME}.1234.tmp")


@pytest.mark.parametrize(
    "env_text,message",
    [(None, ".env not found"), ("OTHER_KEY=1\n", "PRIVATE_VAULT_PATH is not set")],
    ids=["no-env-file", "no-key"],
)
def test_missing_configuration_is_noted_on_stderr(configured_vault, capsys, env_text, message):
    env = configured_vault.project / ".env"
    if env_text is None:
        env.unlink()
    else:
        env.write_text(env_text, encoding="utf-8")
    guard.evaluate(hook_payload("Read", cwd=str(configured_vault.project), file_path="src/a.py"))
    err = capsys.readouterr().err
    assert message in err
    assert "only the in-repo private_vault/ rules apply" in err


def test_no_stderr_note_when_configured(configured_vault, capsys):
    """Absence counterpart of the test above."""
    guard.evaluate(hook_payload("Read", cwd=str(configured_vault.project), file_path="src/a.py"))
    assert capsys.readouterr().err == ""


# --- end to end: one positive control per tool, and the path never printed --

# Deny cases use relative or reach forms: the deny reason echoes the
# agent's own input, so an absolute input would carry the path in itself.
CONFIG_E2E = {
    "Read": ("read-relative", "read-src"),
    "Grep": ("grep-reach-no-glob", "grep-project-root"),
    "Glob": ("glob-reach", "glob-src"),
    "Edit": ("edit-relative", "edit-src"),
    "Write": ("write", "write-src"),
}


def _leaks_vault_path(result: subprocess.CompletedProcess, c) -> bool:
    """The vault path appears in what the hook printed, in any escaping:
    native, JSON (`\\\\` in raw stdout) or repr. Every run of backslashes is
    folded to `/` before comparing against the forward-slash form."""
    text = re.sub(r"\\+", "/", result.stdout + result.stderr).lower()
    return c.vault.as_posix().lower() in text


@pytest.mark.parametrize("tool", sorted(CONFIG_E2E))
def test_configured_vault_hook_process(tool, configured_vault):
    deny_name, allow_name = CONFIG_E2E[tool]
    denied = _run(_config_payload(CONFIG_DENY[deny_name], configured_vault))
    assert denied.returncode == 0
    output = hook_output(denied)
    assert output["permissionDecision"] == "deny"
    assert "PRIVATE_VAULT_PATH" in output["permissionDecisionReason"]
    assert not _leaks_vault_path(denied, configured_vault)
    allowed = _run(_config_payload(CONFIG_ALLOW[allow_name], configured_vault))
    assert allowed.returncode == 0
    assert allowed.stdout.strip() == ""
    assert not _leaks_vault_path(allowed, configured_vault)


def test_leak_check_positive_control(configured_vault):
    """_leaks_vault_path sees the path when the payload itself carries it."""
    denied = _run(_config_payload(CONFIG_DENY["read-native"], configured_vault))
    assert _leaks_vault_path(denied, configured_vault)


# --- aliases and process-environment precedence (installer lean-review #43) -

def _link(target, link) -> None:
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_configured_vault_alias_keeps_its_own_spelling(in_repo_vault_project):
    """PRIVATE_VAULT_PATH names a junction or symlink alias of the in-project
    vault: both spellings are kept, so a glob through either one is denied."""
    project = in_repo_vault_project
    try:
        _link(project / "private_vault", project / "alias")
    except (OSError, AttributeError) as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")
    (project / ".env").write_text("PRIVATE_VAULT_PATH=alias\n", encoding="utf-8")
    cwd = str(project)
    vaults = guard.vault_dirs()
    assert len(vaults) == 1  # one directory...
    assert len(vaults[0].forms) >= 2  # ...under more than one spelling
    for pattern in ("alias/*", "al*/*", "alias/**", "private_vault/*"):
        assert guard.evaluate(hook_payload("Glob", cwd=cwd, pattern=pattern)) is not None, pattern
    # Controls: the same globs elsewhere, and no alias means no alias spelling.
    assert guard.evaluate(hook_payload("Glob", cwd=cwd, pattern="src/*")) is None
    assert guard.evaluate(hook_payload("Glob", cwd=cwd, pattern="sr*/*")) is None


def test_the_alias_spelling_is_lost_without_the_configuration(in_repo_vault_project):
    """Control: with no PRIVATE_VAULT_PATH naming the alias, a glob through
    it is not recognised by location (it has no name match either)."""
    project = in_repo_vault_project
    try:
        _link(project / "private_vault", project / "alias")
    except (OSError, AttributeError) as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")
    assert guard.evaluate(hook_payload("Glob", cwd=str(project), pattern="al*/*")) is None


@pytest.fixture
def two_roots(hermetic_hook_project, tmp_path, monkeypatch):
    """A tmp project whose .env builds PRIVATE_VAULT_PATH from ${ROOT}, and
    two roots, each holding a vault directory."""
    project = tmp_path / "ref_proj"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    (project / ".claude").mkdir(parents=True)
    roots = {}
    for name in ("a", "b"):
        roots[name] = tmp_path / f"root_{name}"
        (roots[name] / "vaults" / "live").mkdir(parents=True)
    (project / ".env").write_text(
        f"ROOT={roots['a'].as_posix()}\nPRIVATE_VAULT_PATH=${{ROOT}}/vaults/live\n", encoding="utf-8"
    )
    return SimpleNamespace(project=project, roots=roots)


def _reads(project, root) -> bool:
    target = str(root / "vaults" / "live" / "rec.json")
    return guard.evaluate(hook_payload("Read", cwd=str(project), file_path=target)) is not None


def test_a_process_variable_beats_the_file_in_a_reference(two_roots, monkeypatch):
    """ember-2 loads .env with load_dotenv's default override=False, so a
    process ROOT wins over the file's ROOT in `${ROOT}`. The guard protects
    the directory the app uses."""
    monkeypatch.setenv("ROOT", two_roots.roots["b"].as_posix())
    assert _reads(two_roots.project, two_roots.roots["b"])
    assert not _reads(two_roots.project, two_roots.roots["a"])


def test_the_file_value_is_used_when_the_process_has_no_such_variable(two_roots, monkeypatch):
    """Control: with no process ROOT the file's ROOT applies, so the test
    above is about the precedence and not about ROOT being read at all."""
    monkeypatch.delenv("ROOT", raising=False)
    assert _reads(two_roots.project, two_roots.roots["a"])
    assert not _reads(two_roots.project, two_roots.roots["b"])


def test_the_cache_follows_a_referenced_process_variable(two_roots, monkeypatch):
    """The cached value depends on the process environment, so changing a
    referenced variable must not serve the old vault."""
    monkeypatch.setenv("ROOT", two_roots.roots["b"].as_posix())
    assert _reads(two_roots.project, two_roots.roots["b"])  # cache written
    monkeypatch.setenv("ROOT", two_roots.roots["a"].as_posix())
    assert _reads(two_roots.project, two_roots.roots["a"])
    assert not _reads(two_roots.project, two_roots.roots["b"])


def test_the_cache_is_still_used_while_the_environment_is_unchanged(two_roots, monkeypatch):
    """Control: an unchanged environment is a cache hit, so the key change
    did not turn the cache off."""
    monkeypatch.setenv("ROOT", two_roots.roots["b"].as_posix())
    calls = []
    real = guard.env_file_value
    monkeypatch.setattr(guard, "env_file_value", lambda key, path: calls.append(key) or real(key, path))
    assert _reads(two_roots.project, two_roots.roots["b"])
    assert _reads(two_roots.project, two_roots.roots["b"])
    assert len(calls) == 1


# --- an unusable configured value is a deny, not "no vault" ----------------

@pytest.mark.parametrize("value", ["a\x00b", "x" * 40000], ids=["nul-byte", "over-path-limit"])
def test_an_unusable_configured_vault_value_fails_closed(hermetic_hook_project, value):
    (hermetic_hook_project / ".env").write_text(f"PRIVATE_VAULT_PATH={value}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        guard.vault_dirs()
    out = run_hook("vault_guard.py", hook_payload("Read", cwd=str(hermetic_hook_project), file_path="src/a.py"))
    assert hook_output(out)["permissionDecision"] == "deny"


def test_a_configured_vault_that_does_not_exist_is_just_absent(hermetic_hook_project, tmp_path):
    """Control: a well-formed value naming no directory is no vault, and the
    call is allowed."""
    (hermetic_hook_project / ".env").write_text(f"PRIVATE_VAULT_PATH={tmp_path / 'nope'}\n", encoding="utf-8")
    assert guard.vault_dirs() == []
    out = run_hook("vault_guard.py", hook_payload("Read", cwd=str(hermetic_hook_project), file_path="src/a.py"))
    assert out.stdout.strip() == ""


# Installer lean-review finding 5 does not apply: Python's fnmatch drops only
# the empty range, so a set holding a reversed range keeps its valid members.
@pytest.mark.parametrize("pattern", ["[z-ap]rivate_vault/*", "pri[z-av]ate_vault/*"])
def test_a_set_with_a_reversed_range_still_names_the_vault(pattern):
    assert guard.names_vault_path(pattern)


@pytest.mark.parametrize("pattern", ["[z-a]rivate_vault/*", "pri[z-a]ate_vault/*"])
def test_a_set_that_is_only_a_reversed_range_names_nothing(pattern):
    """Control: with no valid member left the component cannot match."""
    assert not guard.names_vault_path(pattern)
