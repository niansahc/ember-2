"""
.claude/hooks/vault_guard.py

PreToolUse hook for Read|Grep|Glob|Edit|Write -- denies agent tool calls
that would read, list, or edit vault content. Reads hook JSON from stdin.
Returns a deny decision if the call matches; silent pass-through
otherwise.

Why: vault contents (record text, names, record IDs in file names) must
never enter the session transcript (CLAUDE.md, Vault Privacy Rule).

Which vaults:
  - any directory named private_vault, by name (rule 1);
  - by location, when the directory exists: <project>/private_vault and
    the configured vault, PRIVATE_VAULT_PATH. The configured vault usually
    lives outside the repo (.env.example: C:/EmberVault), where no name
    rule reaches it.

PRIVATE_VAULT_PATH is resolved inside this process: the process
environment first, then .env through env_file.read_env_value (the parser
post_commit_eval.py uses). A relative value resolves against the project
root, as the API does. The value is never printed, deny messages
included. Parsing .env costs a python-dotenv import (~60 ms per hook
process), so the value is cached in .claude/.env.vault_cache.json, keyed
by the .env mtime and size. The cache file's .env.* name puts it under
env_guard.py, which denies agent reads and writes of it; it is
gitignored. With no .env, or no key in it, only the in-repo rules apply
and a note goes to stderr.

Rules:

1. Naming. Any path field that names a private_vault directory, in any
   path form (C:\\x\\private_vault\\a, /c/x/private_vault/a,
   ../private_vault, PRIVATE_VAULT, private_vault. -- Win32 drops the
   trailing dot) or as a glob component that could match it (priv*,
   *_vault), is denied. Same normalisation as env_guard.py: backslash
   -> slash, case-insensitive, trailing dots/spaces dropped, colon parts
   (drive-relative `C:private_vault`) checked.
   By location: any path field that resolves to a location at or under
   an existing vault is denied, whatever the session cwd. Resolution
   covers relative and ../ forms, ~, Git Bash /c/..., WSL /mnt/c/...,
   drive-relative C:x, the \\\\?\\ device prefix, and, through realpath,
   8.3 short names, junctions and symlinks.

2. Reach. A search rooted above an existing vault can surface its content
   without naming it (verified live with synthetic files under another
   contents-ignored directory):
     - Glob ignores .gitignore entirely, so any pattern that can descend
       into the vault lists vault paths (record IDs).
     - Grep (ripgrep) honours .gitignore with no glob, but a whitelisting
       glob overrides it for files directly inside the vault directory
       (and for subdirectories whose names the glob matches). That is the
       rule for a vault inside the project, which is gitignored.
     - A vault outside the project has no .gitignore over it, so Grep
       reaches it with no glob, a negated glob, or an unanchored glob
       (ripgrep descends every subdirectory). An anchored glob is judged
       like Glob.
   A search rooted inside a vault is denied outright. Reach is only
   checked for vault directories that exist, so a machine without one
   pays nothing and nothing is over-blocked. The cure in the deny
   message: scope `path` to a subdirectory, or anchor the pattern
   (src/**/*.py).

Shared with secret_dump_guard.py: names_vault_path(), vault_dirs(),
inside_vault(), above_vault() (one definition of "a vault path").

A pattern check, not a sandbox.
"""

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Same directory; Python puts it on sys.path when this file runs as a
# script. Shared with post_commit_eval.py so both hooks parse .env one way.
from env_file import read_env_value

_VAULT_DIR = "private_vault"
_VAULT_KEY = "PRIVATE_VAULT_PATH"
# `.env.*` basename: env_guard.py denies agent reads and writes of it.
_CACHE_NAME = ".env.vault_cache.json"
_WILDCARDS = frozenset("*?[")

# Path-form prefixes, matched after backslashes become slashes.
_DEVICE_PREFIX_RE = re.compile(r"^//[?.]/")  # \\?\C:\x, \\.\C:\x
_POSIX_DRIVE_RE = re.compile(r"^/(?:mnt/)?([a-z])(?=/|$)", re.IGNORECASE)  # /c/x, /mnt/c/x


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


# ---------------------------------------------------------------------------
# Vault locations
# ---------------------------------------------------------------------------

def absolute_path(path: str, base: Path) -> str:
    """`path` as an absolute, lexically normalised path, for every form an
    agent might write. Relative forms resolve against `base`."""
    s = _DEVICE_PREFIX_RE.sub("", path.replace("\\", "/"))
    s = os.path.expanduser(s)
    m = _POSIX_DRIVE_RE.match(s) if os.name == "nt" else None
    if m:
        s = f"{m.group(1)}:/{s[m.end():]}"
    # ntpath.join also resolves drive-relative C:x against `base` when it is
    # on the same drive (the only drive whose cwd the hook knows).
    return os.path.normpath(os.path.join(str(base), s))


def _resolutions(path: str, base: Path) -> list[str]:
    """Absolute forms of `path`: lexical, plus the real path (8.3 short
    names, junctions, symlinks) when it has no wildcard."""
    lexical = absolute_path(path, base)
    forms = [lexical]
    if not _WILDCARDS & set(lexical):
        try:
            real = os.path.realpath(lexical)
        except (OSError, ValueError):
            real = lexical
        if real != lexical:
            forms.append(real)
    return forms


def path_forms(path: str, base: Path) -> list[list[str]]:
    return [_components(r) for r in _resolutions(path, base)]


def _covers(parts: list[str], target: list[str], strict: bool = False) -> bool:
    """True when `parts` (a path, or a glob split into components) can name
    `target` or something below it; with `strict`, only something below."""
    for i, comp in enumerate(target):
        if i >= len(parts):
            return False
        if parts[i] == "**":
            return True
        if parts[i] != comp and not fnmatch.fnmatchcase(comp, parts[i]):
            return False
    return not strict or len(parts) > len(target)


class Vault(NamedTuple):
    path: Path
    forms: list[list[str]]
    # Inside the project, so gitignored: ripgrep skips it unless a glob
    # whitelists its entries.
    in_project: bool


def _note(message: str) -> None:
    print(f"[vault_guard] {message}; only the in-repo private_vault/ rules apply.", file=sys.stderr)


def _write_cache(cache: Path, data: dict) -> None:
    """Atomic, best effort: a failed write only costs the next call a parse."""
    tmp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _configured_vault() -> str | None:
    """The raw PRIVATE_VAULT_PATH value, or None. Never printed."""
    value = (os.environ.get(_VAULT_KEY) or "").strip()
    if value:
        return value
    project = _project_dir()
    env_path = project / ".env"
    try:
        stat = env_path.stat()
    except OSError:
        _note(".env not found")
        return None
    key = [stat.st_mtime_ns, stat.st_size]
    cache = project / ".claude" / _CACHE_NAME
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        hit = cached["key"] == key and isinstance(cached["value"], (str, type(None)))
        value = cached["value"] if hit else None
    except (OSError, ValueError, KeyError, TypeError):
        hit = False
    if not hit:
        value = read_env_value(_VAULT_KEY, env_path)
        _write_cache(cache, {"key": key, "value": value})
    if not value:
        _note(f"{_VAULT_KEY} is not set in .env")
    return value


def vault_dirs() -> list[Vault]:
    """Existing vault directories: <project>/private_vault and the
    configured vault, deduplicated."""
    project = _project_dir()
    project_forms = path_forms(str(project), project)
    candidates = [str(project / _VAULT_DIR)]
    configured = _configured_vault()
    if configured:
        candidates.append(configured)
    vaults: list[Vault] = []
    for raw in candidates:
        path = Path(absolute_path(raw, project))
        if not path.is_dir():
            continue
        forms = path_forms(str(path), project)
        if any(f in v.forms for v in vaults for f in forms):
            continue
        in_project = any(_covers(f, p) for f in forms for p in project_forms)
        vaults.append(Vault(path, forms, in_project))
    return vaults


def inside_vault(candidate: str, base: Path, vaults: list[Vault]) -> bool:
    """True when `candidate` (a path or glob, relative to `base`) can name
    a location at or under one of `vaults`."""
    forms = path_forms(candidate, base)
    return any(_covers(f, v) for f in forms for vault in vaults for v in vault.forms)


def above_vault(forms: list[list[str]], vault: Vault) -> bool:
    """True when a directory (as `path_forms` components) is a strict ancestor
    of `vault`."""
    return any(len(f) < len(v) and v[: len(f)] == f for f in forms for v in vault.forms)


# ---------------------------------------------------------------------------
# Reach
# ---------------------------------------------------------------------------

def _glob_reaches(pattern: str, roots: list[str], vault: Vault) -> bool:
    """A glob, joined to its search root, can match something below `vault`."""
    return any(
        _covers(_components(absolute_path(pattern, Path(root))), v, strict=True)
        for root in roots
        for v in vault.forms
    )


def _grep_reaches(glob: str | None, roots: list[str], vault: Vault) -> bool:
    """A Grep rooted above `vault` would search its content."""
    # ripgrep globs are relative to the search root, a leading `/` included.
    parts = [p for p in (glob or "").replace("\\", "/").split("/") if p]
    rooted = "/".join(parts)
    unanchored = len(parts) == 1
    if not vault.in_project:
        # No .gitignore over it: ripgrep descends every subdirectory, so only
        # an anchored whitelisting glob can keep it out.
        if glob is None or glob.startswith("!") or unanchored:
            return True
        return _glob_reaches(rooted, roots, vault)
    if glob is None:
        return False  # .gitignore applies
    if unanchored:
        # Matches entry names at any depth, so it reaches every vault entry
        # (file, or subdirectory to descend into) whose name it matches. A
        # negated glob (`!*.py`) never fnmatches one: its leading `!` is
        # literal here.
        try:
            names = [entry.name.lower() for entry in os.scandir(vault.path)]
        except OSError:
            return True
        return any(fnmatch.fnmatchcase(n, parts[0].lower()) for n in names)
    return _glob_reaches(rooted, roots, vault)


def _reaches_vault(tool: str, tool_input: dict, cwd: Path, vaults: list[Vault]) -> str | None:
    """Return the field value that would surface vault content from a
    search rooted at or above a vault, else None."""
    path = tool_input.get("path")
    root = path if isinstance(path, str) and path else "."
    roots = _resolutions(root, cwd)
    root_forms = [_components(r) for r in roots]
    field = "pattern" if tool == "Glob" else "glob"
    pattern = tool_input.get(field)
    pattern = pattern if isinstance(pattern, str) and pattern else None
    for vault in vaults:
        if any(_covers(f, v) for f in root_forms for v in vault.forms):
            return root  # rooted inside the vault
        if tool == "Glob":
            if pattern and _glob_reaches(pattern, roots, vault):
                return pattern
        elif above_vault(root_forms, vault) and _grep_reaches(pattern, roots, vault):
            return pattern or root
    return None


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
    vaults = vault_dirs()
    if not vaults:
        return None
    cwd = Path(payload.get("cwd") or os.getcwd())
    path = tool_input.get(PATH_FIELDS[tool][0])
    if isinstance(path, str) and path and inside_vault(path, cwd, vaults):
        return path
    if tool in ("Grep", "Glob"):
        return _reaches_vault(tool, tool_input, cwd, vaults)
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
                    f"Vault Privacy Rule: vault content (private_vault/ or the "
                    f"configured PRIVATE_VAULT_PATH) may not be read, listed, "
                    f"or edited (matched '{target}'). If this is a search, "
                    f"scope `path` to a subdirectory or anchor the pattern "
                    f"(e.g. src/**/*.py). See CLAUDE.md and "
                    f".claude/hooks/vault_guard.py."
                ),
            }
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
