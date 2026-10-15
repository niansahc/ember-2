"""
.claude/hooks/vault_guard.py

PreToolUse hook for Read|Grep|Glob|Edit|Write -- denies agent tool calls
that would read, list, or edit private_vault/ content. Reads hook JSON
from stdin. Returns a deny decision if the call matches; silent
pass-through otherwise.

Why: vault contents (record text, names, record IDs in file names) must
never enter the session transcript (CLAUDE.md, Vault Privacy Rule).

Two rules:

1. Naming. Any path field that names a private_vault directory, in any
   path form (C:\\x\\private_vault\\a, /c/x/private_vault/a,
   ../private_vault, PRIVATE_VAULT, private_vault. -- Win32 drops the
   trailing dot) or as a glob component that could match it (priv*,
   *_vault), is denied. Same normalisation as env_guard.py: backslash
   -> slash, case-insensitive, trailing dots/spaces dropped, colon parts
   (drive-relative `C:private_vault`) checked.

2. Reach. A search rooted above an existing in-project private_vault/
   can surface its content without naming it (verified live with
   synthetic files under another contents-ignored directory):
     - Glob ignores .gitignore entirely, so any pattern that can descend
       into the vault lists vault paths (record IDs).
     - Grep (ripgrep) honours .gitignore with no glob, but a whitelisting
       glob overrides it for files directly inside the vault directory
       (and for subdirectories whose names the glob matches).
   Reach is only checked when the vault directory exists, so a machine
   without an in-repo vault pays nothing and nothing is over-blocked.
   The cure in the deny message: scope `path` to a subdirectory, or
   anchor the pattern (src/**/*.py).

Shared with secret_dump_guard.py: names_vault_path() (one definition of
"a vault path").

Limits: the configured vault at PRIVATE_VAULT_PATH, when it lives outside
the repo, is not covered by name. A pattern check, not a sandbox.
"""

import fnmatch
import json
import os
import sys
from pathlib import Path

_VAULT_DIR = "private_vault"
_WILDCARDS = frozenset("*?[")


def _project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])


def _components(path: str) -> list[str]:
    """Path components, Win32-canonical: slash-separated, lowercased,
    trailing dots/spaces dropped, colon parts split out."""
    parts = []
    for comp in path.replace("\\", "/").split("/"):
        parts.extend(p.rstrip(". ").lower() for p in comp.split(":"))
    return [p for p in parts if p]


def _names_vault_dir(component: str) -> bool:
    if component == _VAULT_DIR:
        return True
    # A glob component with at least one literal character that can match
    # the directory name (priv*, *_vault). Bare `*`/`**` are a reach
    # question, not naming -- see _reaches_vault().
    return (
        bool(_WILDCARDS & set(component))
        and component.strip("*") != ""
        and fnmatch.fnmatchcase(_VAULT_DIR, component)
    )


def names_vault_path(candidate) -> bool:
    """True when `candidate` (a path, filename, or glob) names a
    private_vault directory or something inside one."""
    if not isinstance(candidate, str):
        return False
    return any(_names_vault_dir(c) for c in _components(candidate))


def _search_root(tool_input: dict, payload: dict) -> Path:
    cwd = Path(payload.get("cwd") or os.getcwd())
    path = tool_input.get("path")
    return (cwd / path) if isinstance(path, str) and path else cwd


def _vault_below(root: Path) -> Path | None:
    """The in-project vault directory, when it exists at or below `root`."""
    vault = _project_dir() / _VAULT_DIR
    if not vault.is_dir():
        return None
    try:
        vault.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return vault


def _pattern_descends(pattern_parts: list[str], target_parts: list[str]) -> bool:
    """True when a slash-anchored glob can match a path under `target_parts`
    (relative to the search root): every target component is matched in
    order, or a `**` component is reached first."""
    for i, target in enumerate(target_parts):
        if i >= len(pattern_parts):
            return False
        part = pattern_parts[i]
        if part == "**":
            return True
        if not fnmatch.fnmatchcase(target, part):
            return False
    return len(pattern_parts) > len(target_parts)


def _reaches_vault(tool: str, tool_input: dict, payload: dict) -> str | None:
    """Return the pattern that would surface vault content from a search
    rooted above the vault, else None."""
    field = "pattern" if tool == "Glob" else "glob"
    pattern = tool_input.get(field)
    if not isinstance(pattern, str) or not pattern:
        return None  # Grep without a glob honours .gitignore
    # A negated Grep glob (`!*.py`) needs no special case: its leading `!`
    # never fnmatches a vault entry name or path component below.
    root = _search_root(tool_input, payload)
    vault = _vault_below(root)
    if vault is None:
        return None
    rel = _components(str(vault.resolve().relative_to(root.resolve())))
    parts = [p.lower() for p in pattern.replace("\\", "/").split("/") if p]
    if tool == "Grep" and len(parts) == 1:
        # Unanchored ripgrep glob: matches entry names at any depth, so it
        # reaches every vault entry (file, or subdirectory to descend into)
        # whose name it matches.
        try:
            names = [entry.name.lower() for entry in os.scandir(vault)]
        except OSError:
            return pattern
        return pattern if any(fnmatch.fnmatchcase(n, parts[0]) for n in names) else None
    return pattern if _pattern_descends(parts, rel) else None


# Which input fields of each tool can name a path.
PATH_FIELDS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "Grep": ("path", "glob"),
    "Glob": ("path", "pattern"),
}


def evaluate(payload) -> str | None:
    """Return the matched target when the tool call would touch vault
    content, else None."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or tool not in PATH_FIELDS:
        return None
    for field in PATH_FIELDS[tool]:
        value = tool_input.get(field)
        if names_vault_path(value):
            return value
    if tool in ("Grep", "Glob"):
        return _reaches_vault(tool, tool_input, payload)
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    target = evaluate(payload)
    if target:
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Vault Privacy Rule: private_vault/ content may not be "
                    f"read, listed, or edited (matched '{target}'). If this "
                    f"is a search, scope `path` to a subdirectory or anchor "
                    f"the pattern (e.g. src/**/*.py). See CLAUDE.md and "
                    f".claude/hooks/vault_guard.py."
                ),
            }
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
