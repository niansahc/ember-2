"""
tests/test_secret_dump_guard.py

Regression tests for .claude/hooks/secret_dump_guard.py (issue #188).

PreToolUse prevention of known dump-command forms against private_vault/
paths -- not redaction, and not a guarantee. See the hook's own module
docstring for why. .env is owned by env_guard.py (tests/test_env_guard.py).

Matching is tested in-process through _matches_dump_command(); a few
end-to-end cases run the hook the way the harness does (JSON on stdin,
a deny decision or silence on stdout).

No test touches the real vault or the developer's .env: CLAUDE_PROJECT_DIR
points at a tmp project and PRIVATE_VAULT_PATH is removed from the
environment. The configured-vault tests give the tmp project a tmp .env
pointing at a tmp vault outside it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
HOOK_PATH = HOOKS_DIR / "secret_dump_guard.py"


def _load_hook():
    # The hook imports names_vault_path from its sibling vault_guard.py, the
    # way it resolves when the harness runs it as a script.
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    spec = importlib.util.spec_from_file_location("secret_dump_guard_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_hook()


@pytest.fixture(autouse=True)
def _project_without_vault(tmp_path, monkeypatch):
    """Hermetic default: an empty tmp project and no inherited
    PRIVATE_VAULT_PATH, so no test reads the developer's checkout or .env."""
    project = tmp_path / "empty_project"
    project.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("PRIVATE_VAULT_PATH", raising=False)
    return project


def _run_hook(raw: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PRIVATE_VAULT_PATH"}
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=raw,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


DENY = [
    "xxd private_vault/memory/x.json",
    "cat private_vault/memory/x.json",
    # The #188 incident form, against a vault path.
    "tail -c 200 private_vault/memory/x.json | xxd",
    "hexdump private_vault/some_file.json",
    "Get-Content private_vault/memory/x.json",
    "type private_vault/memory/x.json",
    "less private_vault/notes.json",
    # shlex POSIX mode used to eat backslashes (C:xprivate_vaulta.json).
    r"cat C:\dev\proj\private_vault\memory\a.json",
    r"C:\Windows\System32\more.com C:\x\private_vault\a.json",
    "/usr/bin/cat /c/dev/proj/private_vault/a.json",
    "CAT.EXE private_vault/a.json",
    "GET-CONTENT private_vault/a.json",
    r"gc C:\dev\proj\private_vault\a.json",
    # Vault named without a trailing slash (the old substring check missed it).
    "cat ../private_vault",
    # Recursive content searches that name the vault.
    "grep -r KEY private_vault/",
    "grep -rn KEY ./private_vault",
    "egrep -R 'a|b' private_vault/memory",
    "rg KEY private_vault",
    "rg -uu KEY /c/dev/proj/private_vault",
    r"findstr /s /i KEY C:\dev\proj\private_vault\*.json",
    r"Select-String -Path C:\dev\proj\private_vault\*\*.json -Pattern KEY",
    # Named in one pipe stage, read in another.
    r"Get-ChildItem -Recurse C:\dev\proj\private_vault | Select-String KEY",
    "gci private_vault -r | sls KEY",
    "find private_vault -name '*.json' | xargs grep KEY",
    "ls private_vault | head",
    "printf KEY | grep -rf - private_vault/",
]

# Over-blocking guard. Each is an absence check; DENY above is the
# positive control (same function, same vault path, dump utility instead).
ALLOW = [
    "cat README.md",
    # 'category'/'heading' must not match the 'cat'/'head' blocklist.
    "echo 'category heading' private_vault/x",
    # Prose inside a quoted commit message is not a command.
    (
        'git commit -m "$(cat <<\'EOF\'\n'
        "fix: mentions private_vault/ in the commit body as prose text\n"
        "EOF\n"
        ')"'
    ),
    # Utility and path in different commands must not cross-contaminate.
    "cat README.md && echo done private_vault/x",
    "rg KEY src; Test-Path private_vault",
    "cat README.md\ntest -d private_vault",
    # A quoted pipe is part of a word, not a pipeline boundary.
    "echo 'cat | x' private_vault/x",
    # Existence checks are not listings.
    "Test-Path private_vault/memory",
    "test -d private_vault && echo present",
    # Searches that do not name the vault.
    "rg KEY src",
    "grep -rn PRIVATE_VAULT_PATH src/core",
    # A name that merely contains the directory name.
    "cat docs/private_vault.md",
    # .env moved to env_guard.py; the old substring match denied the example.
    "cat .env.example",
]


# Listing and counting a vault: entry names are record IDs. Each template
# runs once against a vault path (deny) and once against src/ (allow, the
# absence counterpart), so the rule is about the path, not the utility.
LISTING = [
    "ls {}",
    "ls -la {}/memory/",
    "dir {}",
    "gci {}",
    "Get-ChildItem -Path {} -Name",
    "tree {}",
    "find {} -name '*.json'",
    "wc -l {}/memory/x.json",
    "ls {} | wc -l",
    "ls {} | head",
]


@pytest.mark.parametrize("template", LISTING)
def test_denies_listing_a_vault(template):
    assert guard._matches_dump_command(template.format("private_vault")) is not None


@pytest.mark.parametrize("template", LISTING)
def test_allows_the_same_listing_elsewhere(template):
    """Absence counterpart of test_denies_listing_a_vault."""
    assert guard._matches_dump_command(template.format("src")) is None


@pytest.mark.parametrize("command", DENY)
def test_denies_known_dump_forms(command):
    assert guard._matches_dump_command(command) is not None


@pytest.mark.parametrize("command", ALLOW)
def test_allows_safe_patterns(command):
    assert guard._matches_dump_command(command) is None


def test_hook_process_denies_with_reason():
    payload = json.dumps({"tool_name": "PowerShell", "tool_input": {"command": "gc private_vault/a.json"}})
    result = _run_hook(payload)
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "gc" in output["permissionDecisionReason"]
    assert "#188" in output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "raw",
    ["", json.dumps({"tool_name": "Bash", "tool_input": {}})],
    ids=["empty-stdin", "missing-command"],
)
def test_malformed_input_does_not_crash(raw):
    result = _run_hook(raw)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Vaults by location: the in-repo private_vault/ and PRIVATE_VAULT_PATH
# ---------------------------------------------------------------------------

@pytest.fixture
def in_repo(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / "private_vault" / "memory").mkdir(parents=True)
    (project / "private_vault" / "memory" / "rec.json").write_text("{}", encoding="utf-8")
    (project / "src").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project


IN_REPO_DENY = [
    "cd private_vault && cat memory/rec.json",
    "Set-Location private_vault/memory; gc rec.json",
    "grep -r KEY .",           # grep ignores .gitignore
    "grep -rn KEY",            # no path: the cwd
    "rg -uu KEY .",            # .gitignore switched off
    "rg --no-ignore KEY",
    "Get-ChildItem -Recurse | Select-String KEY",
    "find . -name '*.json' | xargs grep KEY",
    # Recursive listings from the project root enumerate vault entries.
    "find . -name '*.json'",
    "ls -R",
    "Get-ChildItem -Recurse",
    "tree",
    "dir /s /b",
    "cd private_vault && ls",
]

IN_REPO_ALLOW = [
    "rg KEY .",                # rg honours .gitignore: the vault is skipped
    "rg KEY",
    "grep -r KEY src",
    "Get-ChildItem src -Recurse | Select-String KEY",
    "cd src && cat a.py",
    "ls",                      # top level: only the vault directory's own name
    "find src -name '*.py'",
    "gci src -Recurse",
    "cd src && ls",
]


@pytest.mark.parametrize("command", IN_REPO_DENY)
def test_in_repo_vault_by_location(in_repo, command):
    assert guard._matches_dump_command(command, str(in_repo)) is not None


@pytest.mark.parametrize("command", IN_REPO_DENY)
def test_in_repo_vault_by_location_is_off_without_the_directory(_project_without_vault, command):
    """Absence counterpart of the test above: same commands, no vault."""
    assert guard._matches_dump_command(command, str(_project_without_vault)) is None


@pytest.mark.parametrize("command", IN_REPO_ALLOW)
def test_in_repo_vault_allows(in_repo, command):
    assert guard._matches_dump_command(command, str(in_repo)) is None


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """A tmp project whose tmp .env points at a tmp vault outside it."""
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / "src").mkdir()
    outside = tmp_path / "outside"
    vault = outside / "EmberVaultX"
    (vault / "memory").mkdir(parents=True)
    (vault / "memory" / "rec.json").write_text("{}", encoding="utf-8")
    (outside / "notes.txt").write_text("", encoding="utf-8")
    (project / ".env").write_text(f"PRIVATE_VAULT_PATH={vault}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    rel = os.path.relpath(vault, project).replace("\\", "/")
    rel_outside = os.path.relpath(outside, project).replace("\\", "/")
    return SimpleNamespace(
        project=project,
        outside=outside,
        fmt=dict(
            vault=str(vault),
            vault_posix=vault.as_posix(),
            rel=rel,
            rel_bs=rel.replace("/", "\\"),
            outside=str(outside),
            rel_outside=rel_outside,
            rel_outside_bs=rel_outside.replace("/", "\\"),
        ),
    )


# (command template, cwd: "project" or "outside")
CONFIG_DENY = [
    ("cat {vault}/memory/rec.json", "project"),
    ("cat {vault_posix}/zz_probe/none.json", "project"),
    ("Get-Content {rel}/memory/rec.json", "project"),
    (r"type {rel_bs}\memory\rec.json", "project"),
    ("cat {rel_outside}/Ember*/memory/rec.json", "project"),
    ("sls KEY -Path:{rel}/memory/rec.json", "project"),
    ("Select-String -Path {vault}/*/*.json -Pattern KEY", "project"),
    ("cd {rel} && cat memory/rec.json", "project"),
    ("Set-Location {vault}; gc memory/rec.json", "project"),
    ("grep -r KEY {rel_outside}", "project"),
    ("rg KEY {outside}", "project"),
    (r"findstr /s KEY {rel_outside_bs}\*.json", "project"),
    (r"Get-ChildItem -Recurse {rel_outside_bs} | Select-String KEY", "project"),
    ("find {rel_outside} -name '*.json' | xargs grep KEY", "project"),
    ("grep -r KEY", "outside"),
    ("rg KEY", "outside"),
    # Listings and counts.
    ("ls {rel}", "project"),
    (r"dir {vault}", "project"),
    (r"gci {rel_bs}\memory", "project"),
    ("tree {rel}", "project"),
    ("wc -l {rel}/memory/rec.json", "project"),
    ("find {rel_outside}", "project"),
    ("ls -R {rel_outside}", "project"),
    (r"Get-ChildItem -Recurse {rel_outside_bs}", "project"),
    (r"dir /s {rel_outside_bs}", "project"),
    ("tree {rel_outside}", "project"),
    ("find .", "outside"),
    ("cd {rel} && ls", "project"),
    ("Set-Location {vault}; Get-ChildItem", "project"),
]

CONFIG_ALLOW = [
    ("cat src/a.py", "project"),
    ("grep -r KEY src", "project"),
    ("rg KEY", "project"),
    ("ls {rel_outside}", "project"),                # top level: only the vault's own name
    ("gci {rel_outside}", "project"),
    ("Test-Path {rel}", "project"),
    ("wc -l {rel_outside}/notes.txt", "project"),
    ("cat {rel_outside}/notes.txt", "project"),
    ("grep KEY {rel_outside}/*.txt", "project"),    # not recursive
    ("Get-ChildItem {rel_outside} | Select-String KEY", "project"),
    ("cd {rel_outside} && cat notes.txt", "project"),
]


def _config_case(case, c) -> tuple[str, str]:
    template, cwd = case
    return template.format(**c.fmt), str(c.project if cwd == "project" else c.outside)


@pytest.mark.parametrize("case", CONFIG_DENY, ids=lambda case: case[0])
def test_configured_vault_denies(configured, case):
    assert guard._matches_dump_command(*_config_case(case, configured)) is not None


@pytest.mark.parametrize("case", CONFIG_ALLOW, ids=lambda case: case[0])
def test_configured_vault_allows(configured, case):
    assert guard._matches_dump_command(*_config_case(case, configured)) is None


@pytest.mark.parametrize("off", ["no-env-file", "vault-missing"])
@pytest.mark.parametrize("case", CONFIG_DENY, ids=lambda case: case[0])
def test_configured_rules_are_off_without_a_configured_vault(configured, case, off):
    """Absence counterpart of test_configured_vault_denies: same commands."""
    command, cwd = _config_case(case, configured)
    if off == "no-env-file":
        (configured.project / ".env").unlink()
    else:
        shutil.rmtree(configured.outside / "EmberVaultX")
    assert guard._matches_dump_command(command, cwd) is None


def test_configured_vault_hook_process_uses_payload_cwd(configured):
    payload = {"tool_name": "Bash", "tool_input": {"command": "grep -r KEY"}, "cwd": str(configured.outside)}
    env = {k: v for k, v in os.environ.items() if k != "PRIVATE_VAULT_PATH"}
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env={**env, "CLAUDE_PROJECT_DIR": str(configured.project)},
    )
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    reason = output["permissionDecisionReason"].lower()
    assert "embervaultx" not in reason
    # Same command from the project root: no reach, no output.
    payload["cwd"] = str(configured.project)
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env={**env, "CLAUDE_PROJECT_DIR": str(configured.project)},
    )
    assert result.stdout.strip() == ""
