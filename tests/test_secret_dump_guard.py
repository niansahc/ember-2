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

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from tests.conftest import HOOK_VAULT_NAME, hook_output, load_hook, relpath, run_hook

guard = load_hook("secret_dump_guard.py")

# Hermetic default: no test reads the developer's checkout or .env.
pytestmark = pytest.mark.usefixtures("hermetic_hook_project")


def _run_hook(payload) -> subprocess.CompletedProcess:
    """The real hook process, with the environment the fixtures set up."""
    return run_hook("secret_dump_guard.py", payload)


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
    output = hook_output(result)
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

IN_REPO_DENY = [
    "cd private_vault && cat memory/rec.json",
    "Set-Location private_vault/memory; gc rec.json",
    "grep -r KEY .",           # grep ignores .gitignore
    "grep -rn KEY",            # no path: the cwd
    "rg -uu KEY .",            # .gitignore switched off
    "rg -u build .",
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
    "rg build .",              # a `u` in a positional is not -u
    "grep -r KEY src",
    "Get-ChildItem src -Recurse | Select-String KEY",
    "cd src && cat a.py",
    "ls",                      # top level: only the vault directory's own name
    "find src -name '*.py'",
    "gci src -Recurse",
    "cd src && ls",
]


@pytest.mark.parametrize("command", IN_REPO_DENY)
def test_in_repo_vault_by_location(in_repo_vault_project, command):
    assert guard._matches_dump_command(command, str(in_repo_vault_project)) is not None


@pytest.mark.parametrize("command", IN_REPO_DENY)
def test_in_repo_vault_by_location_is_off_without_the_directory(hermetic_hook_project, command):
    """Absence counterpart of the test above: same commands, no vault."""
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is None


@pytest.mark.parametrize("command", IN_REPO_ALLOW)
def test_in_repo_vault_allows(in_repo_vault_project, command):
    assert guard._matches_dump_command(command, str(in_repo_vault_project)) is None


@pytest.fixture
def configured(configured_vault):
    """configured_vault, plus the command-template fields built from it."""
    c = configured_vault
    return SimpleNamespace(
        project=c.project,
        outside=c.outside,
        vault=c.vault,
        fmt=dict(
            vault=str(c.vault),
            vault_posix=c.vault.as_posix(),
            rel=relpath(c.vault, c.project),
            rel_bs=relpath(c.vault, c.project, "\\"),
            outside=str(c.outside),
            rel_outside=relpath(c.outside, c.project),
            rel_outside_bs=relpath(c.outside, c.project, "\\"),
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
def test_configured_rules_are_off_without_a_configured_vault(configured, off):
    """Absence counterpart of test_configured_vault_denies: every deny
    command, against the same tree with the configuration taken away."""
    cases = [_config_case(case, configured) for case in CONFIG_DENY]
    if off == "no-env-file":
        (configured.project / ".env").unlink()
    else:
        shutil.rmtree(configured.vault)
    assert [c for c, cwd in cases if guard._matches_dump_command(c, cwd) is not None] == []


def test_configured_vault_hook_process_uses_payload_cwd(configured):
    payload = {"tool_name": "Bash", "tool_input": {"command": "grep -r KEY"}, "cwd": str(configured.outside)}
    result = _run_hook(payload)
    output = hook_output(result)
    assert output["permissionDecision"] == "deny"
    reason = output["permissionDecisionReason"].lower()
    assert HOOK_VAULT_NAME.lower() not in reason
    # Same command from the project root: no reach, no output.
    payload["cwd"] = str(configured.project)
    assert _run_hook(payload).stdout.strip() == ""
