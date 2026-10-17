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


# A vault named only inside a flag value. The hermetic project has no vault
# directory and no configured vault, so only the name rule can catch these.
FLAG_VALUE_DENY = [
    "grep --file=private_vault/x.json notes.txt",
    "grep -r --exclude-dir=src KEY --file=private_vault/x",
    "rg KEY --ignore-file=../private_vault/x",
    "Get-Content -Path:private_vault/a.json",
    r"gc -LiteralPath:C:\dev\proj\private_vault\a.json",
    "Select-String -Pattern KEY -Path:/c/dev/proj/PRIVATE_VAULT/memory/a.json",
    "ls --directory=private_vault",
]


@pytest.mark.parametrize("command", FLAG_VALUE_DENY)
def test_denies_vault_named_in_a_flag_value_without_a_vault_directory(hermetic_hook_project, command):
    assert not (hermetic_hook_project / "private_vault").exists()
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize("command", FLAG_VALUE_DENY)
def test_allows_the_same_flag_value_elsewhere(hermetic_hook_project, command):
    """Absence counterpart: the same flag forms naming src/ are allowed, so
    the deny above is about the vault name, not the flag syntax."""
    elsewhere = command.replace("private_vault", "src").replace("PRIVATE_VAULT", "src").replace("priv*", "src")
    assert guard._matches_dump_command(elsewhere, str(hermetic_hook_project)) is None


def test_hook_process_denies_flag_value_without_a_vault_directory(hermetic_hook_project):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "grep --file=private_vault/x.json notes.txt"},
        "cwd": str(hermetic_hook_project),
    }
    output = hook_output(_run_hook(payload))
    assert output["permissionDecision"] == "deny"


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


# ---------------------------------------------------------------------------
# Substitution, wrappers, nested shells and keywords. The hermetic project has
# no vault directory, so only the name rule can catch these. Each DENY form
# also runs with src/ in place of the vault (absence counterpart), so the deny
# is about the vault name, not the syntax.
# ---------------------------------------------------------------------------

WRAPPED_DENY = [
    "echo $(cat private_vault/a.json)",
    "x=$(cat private_vault/a.json)",
    "if true; then cat private_vault/a.json; fi",
    "FOO=1 cat private_vault/a.json",
    "sudo cat private_vault/a.json",
    "env cat private_vault/a.json",
    'bash -c "cat private_vault/a.json"',
    "PowerShell echo (gc private_vault/a.json)",
    "echo `cat private_vault/a.json`",
    'sh -c "cat private_vault/a.json"',
    'pwsh -Command "gc private_vault/a.json"',
    'powershell -Command "gc private_vault/a.json"',
    "time cat private_vault/a.json",
    "nice cat private_vault/a.json",
    "nohup cat private_vault/a.json",
    # flags of the wrappers, stacked wrappers, more keywords
    "sudo -u root cat private_vault/a.json",
    "env -i FOO=1 cat private_vault/a.json",
    "nice -n 5 cat private_vault/a.json",
    "FOO=1 BAR=2 sudo env time cat private_vault/a.json",
    "while read l; do cat private_vault/a.json; done",
    "{ cat private_vault/a.json; }",
    "! cat private_vault/a.json",
    "x=1; sudo cat private_vault/a.json",
    "cat private_vault/a.json | sudo tee out.txt",
    # nested shells
    'bash -lc "cat private_vault/a.json"',
    'zsh -c "cat private_vault/a.json"',
    'pwsh -NoProfile -ExecutionPolicy Bypass -Command "gc private_vault/a.json"',
    'pwsh -NoProfile -c "gc private_vault/a.json"',
    'cmd /c "type private_vault/a.json"',
    'cmd.exe /k type private_vault/a.json',
    'eval "cat private_vault/a.json"',
    'bash -c "echo $(cat private_vault/a.json)"',
    "bash -c \"bash -c 'cat private_vault/a.json'\"",
    # substitution forms
    'echo "$(cat private_vault/a.json)"',
    'echo "a $(echo $(cat private_vault/a.json)) b"',
    "echo $(echo `cat private_vault/a.json`)",
    "cat <(cat private_vault/a.json)",
    "diff <(cat private_vault/a.json) other",
    "tee >(cat private_vault/a.json)",
    # heredocs: a script is executed, a bare delimiter is expanded
    "bash <<'EOF'\ncat private_vault/a.json\nEOF\n",
    "cat <<EOF\n$(cat private_vault/a.json)\nEOF\n",
    # named in one stage, read by xargs in another
    "echo private_vault/a.json | sudo xargs cat",
    # any command naming a vault, not only the readers
    "python private_vault/x.py",
    "git add private_vault",
    "rm private_vault/a.json",
    "cp private_vault/a.json out.json",
]


@pytest.mark.parametrize("command", WRAPPED_DENY)
def test_denies_readers_behind_wrappers_and_substitution(hermetic_hook_project, command):
    assert not (hermetic_hook_project / "private_vault").exists()
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize("command", WRAPPED_DENY)
def test_allows_the_same_wrapped_forms_elsewhere(hermetic_hook_project, command):
    """Absence counterpart: the same syntax over src/ is allowed."""
    elsewhere = command.replace("private_vault", "src")
    assert guard._matches_dump_command(elsewhere, str(hermetic_hook_project)) is None


# Single quotes are literal; the same text in double quotes is substituted.
def test_single_quoted_substitution_is_literal(hermetic_hook_project):
    cwd = str(hermetic_hook_project)
    assert guard._matches_dump_command("echo '$(cat private_vault/a.json)'", cwd) is None
    assert guard._matches_dump_command('echo "$(cat private_vault/a.json)"', cwd) is not None
    assert guard._matches_dump_command("echo '`cat private_vault/a.json`'", cwd) is None
    assert guard._matches_dump_command("echo `cat private_vault/a.json`", cwd) is not None


def test_quoted_heredoc_body_is_literal_text(hermetic_hook_project):
    cwd = str(hermetic_hook_project)
    prose = "cat <<'EOF'\nsee private_vault/a.json and $(cat private_vault/a.json)\nEOF\n"
    assert guard._matches_dump_command(prose, cwd) is None
    # Control: the same body behind a bare delimiter runs its substitution.
    assert guard._matches_dump_command(prose.replace("'EOF'", "EOF"), cwd) is not None


def _nest(inner: str, levels: int) -> str:
    command = inner
    for _ in range(levels):
        command = f"echo $({command})"
    return command


def test_nesting_is_walked_to_the_depth_cap_and_denied_past_it(hermetic_hook_project):
    cwd = str(hermetic_hook_project)
    assert guard._matches_dump_command(_nest("echo hi", 3), cwd) is None
    assert guard._matches_dump_command(_nest("echo hi", 8), cwd) is None
    assert guard._matches_dump_command(_nest("cat private_vault/a.json", 8), cwd) == "cat"
    # Past the cap the walk stops and denies, whatever is inside.
    assert guard._matches_dump_command(_nest("echo hi", 9), cwd) == "nested command"
    assert guard._matches_dump_command(_nest("cat private_vault/a.json", 9), cwd) is not None


@pytest.mark.parametrize(
    "command",
    [
        "echo $(cat private_vault/a.json)",
        "sudo cat private_vault/a.json",
        'bash -c "cat private_vault/a.json"',
        "PowerShell echo (gc private_vault/a.json)",
    ],
)
def test_hook_process_denies_wrapped_forms(hermetic_hook_project, command):
    cwd = str(hermetic_hook_project)
    denied = _run_hook({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})
    assert hook_output(denied)["permissionDecision"] == "deny"
    allowed = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": command.replace("private_vault", "src")}, "cwd": cwd}
    )
    assert allowed.returncode == 0
    assert allowed.stdout.strip() == ""


def test_hook_process_denies_nesting_past_the_cap(hermetic_hook_project):
    payload = {"tool_name": "Bash", "tool_input": {"command": _nest("echo hi", 9)}, "cwd": str(hermetic_hook_project)}
    assert hook_output(_run_hook(payload))["permissionDecision"] == "deny"
    payload["tool_input"]["command"] = _nest("echo hi", 3)
    assert _run_hook(payload).stdout.strip() == ""


# Print and test commands are checked for redirection targets only.
PRINT_DENY = [
    "echo hi > private_vault/out.txt",
    "printf x >> private_vault/out.txt",
    "Write-Output hi > private_vault/out.txt",
    "Write-Host hi > private_vault/out.txt",
    "test -d x 2> private_vault/err",
    "mkdir -p x > private_vault/log",
    "echo x | tee private_vault/o.txt",
]

PRINT_ALLOW = [
    "echo private_vault/a.json",
    "printf '%s' private_vault",
    "Write-Host private_vault",
    "Write-Output private_vault/a.json",
    "test -d private_vault",
    "[ -d private_vault ] && echo present",
    "[[ -d private_vault ]]",
    "Test-Path private_vault/memory",
    "mkdir private_vault",
    "mkdir -p private_vault/memory",
    "cd private_vault",
    "FOO=1 echo private_vault",
    "sudo test -d private_vault",
]


@pytest.mark.parametrize("command", PRINT_DENY)
def test_denies_a_vault_named_as_a_redirection_target(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize("command", PRINT_DENY)
def test_allows_the_same_redirection_elsewhere(hermetic_hook_project, command):
    assert guard._matches_dump_command(command.replace("private_vault", "src"), str(hermetic_hook_project)) is None


@pytest.mark.parametrize("command", PRINT_ALLOW)
def test_allows_print_and_test_commands_naming_a_vault(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is None


def test_print_command_feeding_a_reader_is_still_denied(hermetic_hook_project):
    """Control for PRINT_ALLOW: the same echo, piped into a reader, is a read."""
    cwd = str(hermetic_hook_project)
    assert guard._matches_dump_command("echo private_vault/a.json", cwd) is None
    assert guard._matches_dump_command("echo private_vault/a.json | xargs cat", cwd) is not None


# ---------------------------------------------------------------------------
# The pattern operand of a content search is text, not a path; a wildcard names
# a vault only with the literal text "vault" in it
# ---------------------------------------------------------------------------

PATTERN_ALLOW = [
    "rg private_vault src",
    'grep -rn "private_vault" .',
    "cat p*",
    "ls *t",
    "rg -e private_vault src",
    "rg --regexp=private_vault src",
    "rg -C 2 private_vault src",
    "rg -n --glob '*.py' private_vault src",
    "grep -rn -e private_vault src",
    "egrep -r private_vault src",
    "ag private_vault src",
    "ack private_vault src",
    "Select-String -Pattern private_vault -Path src/a.txt",
    "Select-String -Pattern:private_vault src/a.txt",
    "sls private_vault src/a.txt",
    "findstr /c:private_vault src/a.txt",
    "findstr /s private_vault src/a.txt",
    "cat p?",
    "ls p[a-z]*",
    "cat --input=priv*/x.json",
    "ls private_vau*",
]

# Same utilities with the vault as a path operand. PATTERN_ALLOW's controls.
PATTERN_DENY = [
    "rg KEY private_vault",
    "rg private_vault private_vault",
    "rg -e KEY private_vault",
    "rg -C 2 KEY private_vault",
    "grep -f private_vault/pat.txt src",
    "grep -rn KEY private_vault",
    "ag KEY private_vault",
    "Select-String -Pattern KEY -Path private_vault/a.txt",
    "Select-String private_vault -Path private_vault/a.txt",
    "findstr /c:KEY private_vault/a.txt",
    "findstr /s KEY private_vault/a.txt",
    "cat private_vault/a.json",
    "ls *vault*",
    "ls *vault",
    "cat p*vault/a.json",
    "cat --input=*vault*/x.json",
]


@pytest.mark.parametrize("command", PATTERN_ALLOW)
def test_pattern_text_and_plain_wildcards_do_not_name_a_vault(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is None


@pytest.mark.parametrize("command", PATTERN_DENY)
def test_a_vault_operand_is_still_denied(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize(
    "name, expected",
    [
        ("private_vault", True),
        ("a/PRIVATE_VAULT/b", True),
        ("*vault*", True),
        ("p*vault", True),
        ("priv*", False),
        ("p*", False),
        ("*t", False),
        ("private_vau?", False),
        ("docs/private_vault.md", False),
        ("src", False),
        (None, False),
    ],
)
def test_names_vault_operand(name, expected):
    vault_guard = load_hook("vault_guard.py")
    assert vault_guard.names_vault_operand(name) is expected


def test_a_real_vault_still_catches_plain_wildcards(in_repo_vault_project):
    """Control for the wildcard rule: with a vault directory in the cwd, the
    same globs match it by location."""
    cwd = str(in_repo_vault_project)
    for command in ("cat p*", "ls *t", "cat priv*/top.json", "cat --input=priv*/top.json", "ls private_vau*"):
        assert guard._matches_dump_command(command, cwd) is not None, command
    # Pattern text stays text, vault or not; a search with no root walks the cwd.
    assert guard._matches_dump_command("grep -rn private_vault src", cwd) is None
    assert guard._matches_dump_command("rg private_vault src", cwd) is None
    assert guard._matches_dump_command("grep -rn private_vault", cwd) is not None


# ---------------------------------------------------------------------------
# Flag values are not search roots; a root that does not exist adds the cwd
# ---------------------------------------------------------------------------

# From an ancestor of an out-of-project vault (rg/ag cannot skip it by
# .gitignore). Each is a recursive search or listing with no root of its own.
ANCESTOR_DENY = [
    "rg -C 2 KEY",
    'rg -g "*.json" KEY',
    "grep -rn -A 2 KEY",
    "Get-ChildItem -Recurse -Depth 3",
    "rg --context 2 KEY",
    "rg -A2 -g '*.json' KEY",
    "rg --glob '*.json' --max-count 3 KEY",
    "rg -t py KEY",
    "rg -j 4 KEY",
    "rg -u -C 2 KEY",
    "grep -rn --include '*.json' KEY",
    "grep -rn --exclude-dir node_modules KEY",
    "grep -rn -m 3 KEY",
    "grep -rnA 2 KEY",
    "ag -A 2 KEY",
    "Get-ChildItem -Recurse -Include *.json",
    "Get-ChildItem -Recurse -Filter *.json",
    "gci -r -Exclude x",
    "gci -Recurse -Depth:3",
    "gci -Recurse -Dep 3",
    "gci -Recurse -Incl *.json",
    "tree -L 2",
    # Unchanged controls: no flag value involved.
    "rg KEY",
    "grep -rn KEY",
    "Get-ChildItem -Recurse",
]

# Roots that do not exist: they may be a flag value this parser missed.
MISSING_ROOT_DENY = [
    "rg KEY nosuchdir",
    "grep -rn KEY nosuchdir",
    "ag KEY nosuchdir",
]


@pytest.mark.parametrize("command", ANCESTOR_DENY)
def test_flag_values_are_not_roots(configured, command):
    assert guard._matches_dump_command(command, str(configured.outside)) is not None


@pytest.mark.parametrize("command", ANCESTOR_DENY)
def test_the_same_search_with_a_real_root_is_allowed(configured, command):
    """Absence counterpart: the same flags, with an existing root that is not
    an ancestor of the vault."""
    src = str(configured.project / "src")
    assert guard._matches_dump_command(f"{command} {src}", str(configured.outside)) is None


@pytest.mark.parametrize("command", MISSING_ROOT_DENY)
def test_a_root_that_does_not_exist_adds_the_cwd(configured, command):
    assert guard._matches_dump_command(command, str(configured.outside)) is not None
    existing = command.replace("nosuchdir", str(configured.project / "src"))
    assert guard._matches_dump_command(existing, str(configured.outside)) is None


def test_missing_root_rule_is_limited_to_the_search_family(configured):
    """find and the listers name their roots strictly: a missing one is just
    a missing path."""
    cwd = str(configured.outside)
    assert guard._matches_dump_command("find nosuchdir -name x", cwd) is None
    assert guard._matches_dump_command("Get-ChildItem -Recurse nosuchdir", cwd) is None
    # Control: the same utilities with no root walk the cwd.
    assert guard._matches_dump_command("find -name x", cwd) is not None


IN_PROJECT_U_DENY = [
    "rg -u -C 2 KEY",
    'rg -uu -g "*.json" KEY',
    "rg --no-ignore -A 2 KEY",
    "grep -rn -A 2 KEY",
    "Get-ChildItem -Recurse -Depth 3",
]


@pytest.mark.parametrize("command", IN_PROJECT_U_DENY)
def test_flag_values_with_the_in_project_vault(in_repo_vault_project, command):
    assert guard._matches_dump_command(command, str(in_repo_vault_project)) is not None
    assert guard._matches_dump_command(f"{command} src", str(in_repo_vault_project)) is None


def test_gitignore_still_hides_the_in_project_vault_from_rg(in_repo_vault_project):
    """Control for IN_PROJECT_U_DENY: without -u, rg skips the ignored vault
    whatever its flags."""
    cwd = str(in_repo_vault_project)
    assert guard._matches_dump_command("rg -C 2 KEY", cwd) is None
    assert guard._matches_dump_command("rg -u -C 2 KEY", cwd) is not None


def test_listing_the_cwd_ignores_flag_values(in_repo_vault_project):
    """`gci -Depth 3` inside a vault lists the cwd; 3 is not a path."""
    cwd = str(in_repo_vault_project)
    assert guard._matches_dump_command("cd private_vault && gci -Depth 3", cwd) is not None
    assert guard._matches_dump_command("cd src && gci -Depth 3", cwd) is None


# ---------------------------------------------------------------------------
# Installer lean-review (PR #43) findings ported: backslash escapes, the words
# of a substitution, the inert-consumer heredoc rule, more nested shells, a
# vault file run as a command. Hermetic project: no vault directory, so only
# the name rule applies unless a test builds a vault. Each deny form has a
# twin over src/ (same syntax, no vault) that must pass.
# ---------------------------------------------------------------------------

ESCAPE_DENY = [
    r"echo \' ; cat private_vault/a.json",
    r"echo it\'s; cat private_vault/a.json",
    r"echo 'it'\''s' ; cat private_vault/a.json",
    r"echo \' $(cat private_vault/a.json)",
    r'echo \" ; cat private_vault/a.json',
    r'echo "say \"hi\"" ; cat private_vault/a.json',
    r"echo \` ; cat private_vault/a.json",
    # A PowerShell path ending in a backslash inside quotes: no Bash escape.
    'gc "C:\\x\\private_vault\\"',
    'type "C:\\x\\private_vault\\a.json"',
]


@pytest.mark.parametrize("command", ESCAPE_DENY)
def test_a_backslash_escape_does_not_hide_the_rest_of_the_line(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize("command", ESCAPE_DENY)
def test_the_same_escapes_over_src_are_allowed(hermetic_hook_project, command):
    assert guard._matches_dump_command(command.replace("private_vault", "src"), str(hermetic_hook_project)) is None


def test_quote_scan_controls(hermetic_hook_project):
    """Controls for the escape cases: a correctly quoted apostrophe never hid
    anything, and an escaped quote keeps its text literal."""
    cwd = str(hermetic_hook_project)
    assert guard._matches_dump_command('echo "it\'s" ; cat private_vault/a.json', cwd) is not None
    # \' is a literal quote, so the single-quoted-looking text stays one word.
    assert guard._matches_dump_command(r"echo \'private_vault\' ", cwd) is None
    assert guard._matches_dump_command(r"echo \' ; cat src/a.py", cwd) is None


SUBSTITUTION_WORD_DENY = [
    "cat $(echo private_vault/a.json)",
    "cat $(printf %s private_vault/a.json)",
    "cat `echo private_vault/a.json`",
    'cat "$(echo private_vault/a.json)"',
    "head -1 $(echo private_vault/a.json)",
    "cat $(cat <<EOF\nprivate_vault/a.json\nEOF\n)",
    "cat $(cat <<'EOF'\nprivate_vault/a.json\nEOF\n)",
    'cat "$(cat <<\'EOF\'\nprivate_vault/a.json\nEOF\n)"',
    "echo $(echo private_vault/a.json) | xargs cat",
    "python $(echo private_vault/x.py)",
]


@pytest.mark.parametrize("command", SUBSTITUTION_WORD_DENY)
def test_substitution_output_is_checked_as_operands_of_the_outer_command(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize("command", SUBSTITUTION_WORD_DENY)
def test_the_same_substitutions_over_src_are_allowed(hermetic_hook_project, command):
    assert guard._matches_dump_command(command.replace("private_vault", "src"), str(hermetic_hook_project)) is None


def test_substitution_words_only_count_for_the_stage_that_holds_them(hermetic_hook_project):
    cwd = str(hermetic_hook_project)
    # echo prints its operands: nothing is read.
    assert guard._matches_dump_command("echo $(echo private_vault/a.json)", cwd) is None
    # Control: the same words reach a reader.
    assert guard._matches_dump_command("cat $(echo private_vault/a.json)", cwd) is not None
    # A substitution in another stage does not leak its words into this one.
    assert guard._matches_dump_command("cat src/a.py; echo $(echo private_vault/a.json)", cwd) is None


def test_substitution_words_check_a_configured_vault_by_location(configured):
    cwd = str(configured.project)
    target = f"{configured.vault.as_posix()}/memory/rec.json"
    assert guard._matches_dump_command(f"cat $(echo {target})", cwd) is not None
    assert guard._matches_dump_command(f"cat $(printf %s {target})", cwd) is not None
    assert guard._matches_dump_command(f"cat $(echo {configured.outside.as_posix()}/notes.txt)", cwd) is None


# A body is dropped only for a receiver that stores or displays it.
INERT_HEREDOC = [
    "cat <<'EOF'\n{body}\nEOF\n",
    "cat > notes.txt <<'EOF'\n{body}\nEOF\n",
    "tee notes.txt <<'EOF'\n{body}\nEOF\n",
    "git commit -F - <<'EOF'\n{body}\nEOF\n",
    "gh pr create --body-file - <<'EOF'\n{body}\nEOF\n",
    "git commit -m \"$(cat <<'EOF'\n{body}\nEOF\n)\"",
]
SCRIPT_RECEIVERS = [
    "bash <<'EOF'\n{body}\nEOF\n",
    "python3.12 <<'EOF'\n{body}\nEOF\n",
    "python <<'EOF'\n{body}\nEOF\n",
    "sqlite3 <<'EOF'\n{body}\nEOF\n",
    "sudo bash <<'EOF'\n{body}\nEOF\n",
    "cat <<'EOF' | bash\n{body}\nEOF\n",
    "cat <<'EOF' | sh -s\n{body}\nEOF\n",
    "cat <<'EOF' | python3.12 -\n{body}\nEOF\n",
    "echo \"$(cat <<'EOF'\n{body}\nEOF\n)\"",
    "git log $(cat <<'EOF'\n{body}\nEOF\n)",
    "xargs <<'EOF'\n{body}\nEOF\n",
]


PROSE_BODY = "see private_vault/a.json for details"


@pytest.mark.parametrize("template", INERT_HEREDOC)
def test_a_body_given_to_an_inert_consumer_is_text(hermetic_hook_project, template):
    command = template.format(body=PROSE_BODY)
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is None


@pytest.mark.parametrize("template", SCRIPT_RECEIVERS)
def test_the_same_body_is_scanned_when_the_receiver_runs_or_reads_it(hermetic_hook_project, template):
    """Control for the allow cases above: identical body text, a receiver
    that is not inert (a shell, an interpreter incl. versioned names,
    sqlite3, xargs, a pipe, or a substitution used as words)."""
    assert guard._matches_dump_command(template.format(body=PROSE_BODY), str(hermetic_hook_project)) is not None
    # And the receiver alone is not the cause: no vault in the body, no deny.
    assert guard._matches_dump_command(template.format(body="see src/a.py"), str(hermetic_hook_project)) is None


def test_an_unquoted_delimiter_still_runs_its_substitutions(hermetic_hook_project):
    cwd = str(hermetic_hook_project)
    body = "$(cat private_vault/a.json)"
    assert guard._matches_dump_command(f"cat <<EOF\n{body}\nEOF\n", cwd) is not None
    assert guard._matches_dump_command(f"tee out.txt <<EOF\n{body}\nEOF\n", cwd) is not None
    assert guard._matches_dump_command("cat <<EOF\n$(cat src/a.py)\nEOF\n", cwd) is None


NESTED_SHELL_FORMS = [
    "wsl cat {t}",
    "wsl -e cat {t}",
    "wsl --exec cat {t}",
    "wsl -- cat {t}",
    "wsl -d Ubuntu cat {t}",
    "wsl -u root -- cat {t}",
    "wsl --cd /tmp cat {t}",
    "wsl.exe cat {t}",
    'wsl bash -c "cat {t}"',
    'fish -c "cat {t}"',
    'csh -c "cat {t}"',
    'tcsh -c "cat {t}"',
    'bash -c "cat {t}"',
]


@pytest.mark.parametrize("form", NESTED_SHELL_FORMS)
def test_nested_shells_are_walked_for_a_configured_vault(configured, form):
    cwd = str(configured.project)
    vault = form.format(t=f"{configured.vault.as_posix()}/memory/rec.json")
    assert guard._matches_dump_command(vault, cwd) is not None
    # Control: the same wrapper over a file next to the vault.
    other = form.format(t=f"{configured.outside.as_posix()}/notes.txt")
    assert guard._matches_dump_command(other, cwd) is None


@pytest.mark.parametrize("form", NESTED_SHELL_FORMS)
def test_nested_shells_are_walked_for_the_name_rule(hermetic_hook_project, form):
    cwd = str(hermetic_hook_project)
    assert guard._matches_dump_command(form.format(t="private_vault/a.json"), cwd) is not None
    assert guard._matches_dump_command(form.format(t="src/a.py"), cwd) is None


COMMAND_WORD_DENY = [
    "./private_vault/run.sh",
    "private_vault/run.sh --flag",
    "sudo ./private_vault/run.sh",
    "FOO=1 ./private_vault/run.sh",
    "(./private_vault/run.sh)",
    "true && ./private_vault/run.sh",
    "xargs -n1 ./private_vault/run.sh",
    "python3.12 <<'EOF'\nprint(open('private_vault/a.json').read())\nEOF\n",
    "bash -c ./private_vault/run.sh",
]


@pytest.mark.parametrize("command", COMMAND_WORD_DENY)
def test_running_a_file_under_a_vault_is_denied(hermetic_hook_project, command):
    assert guard._matches_dump_command(command, str(hermetic_hook_project)) is not None


@pytest.mark.parametrize("command", COMMAND_WORD_DENY)
def test_running_the_same_file_elsewhere_is_allowed(hermetic_hook_project, command):
    assert guard._matches_dump_command(command.replace("private_vault", "scripts"), str(hermetic_hook_project)) is None


def test_the_command_word_rule_matches_a_component_not_a_substring(hermetic_hook_project):
    cwd = str(hermetic_hook_project)
    assert guard._matches_dump_command("./scripts/run.sh", cwd) is None
    assert guard._matches_dump_command("./scripts/private_vault_notes.sh", cwd) is None
    assert guard._matches_dump_command("./scripts/run_private_vault.sh", cwd) is None
    assert guard._matches_dump_command("./private_vault/run.sh", cwd) is not None


def test_a_wildcard_through_a_vault_alias_is_denied(in_repo_vault_project):
    """The configured vault is a junction or symlink alias of the in-project
    one: a glob through the alias needs the alias's own spelling, which the
    dedup in vault_dirs must keep."""
    project = in_repo_vault_project
    try:
        _link(project / "private_vault", project / "alias")
    except (OSError, AttributeError) as exc:
        pytest.skip(f"cannot create a directory link here: {exc}")
    (project / ".env").write_text("PRIVATE_VAULT_PATH=alias\n", encoding="utf-8")
    cwd = str(project)
    for command in ("cat alias/*.json", "cat al*/top.json", "cat alias/top.json", "ls alias", "ls al*"):
        assert guard._matches_dump_command(command, cwd) is not None, command
    assert guard._matches_dump_command("cat src/*.py", cwd) is None
    assert guard._matches_dump_command("ls src", cwd) is None


def _link(target, link) -> None:
    import os
    import sys

    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_nesting_past_the_cap_names_the_limit_in_the_deny_reason(hermetic_hook_project):
    payload = {"tool_name": "Bash", "tool_input": {"command": _nest("echo hi", 9)}, "cwd": str(hermetic_hook_project)}
    reason = hook_output(_run_hook(payload))["permissionDecisionReason"]
    assert "more than 8 levels" in reason
    # Control: an ordinary deny keeps the vault wording.
    payload["tool_input"]["command"] = "cat private_vault/a.json"
    ordinary = hook_output(_run_hook(payload))["permissionDecisionReason"]
    assert "more than 8 levels" not in ordinary
    assert "vault path" in ordinary
