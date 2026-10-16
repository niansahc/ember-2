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
environment first, then .env through hook_utils.env_file_value (the parser
post_commit_eval.py uses). It resolves through hook_utils.project_path,
which delegates to src/core/vault_path.py, the rule the app's
get_private_vault_path() and post_commit_eval.py use: a relative value
against the project root, never the cwd. The value is never printed, deny messages
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
inside_vault(), above_vault(), path_forms() (one definition of "a vault
path"); absolute_path() lives in hook_utils.py. Paths are plain strings:
this runs as a fresh process on every tool call, and pathlib/typing
imports cost ~11 ms.

A pattern check, not a sandbox.
"""

import json
import os
import sys
from collections import namedtuple

# Same directory; Python puts it on sys.path when this file runs as a
# script. Shared with post_commit_eval.py so both hooks parse .env one way
# and resolve PRIVATE_VAULT_PATH by the app's rule (src/core/vault_path.py).
from hook_utils import absolute_path, deny, env_file_value, project_dir, project_path

_VAULT_DIR = "private_vault"
_VAULT_KEY = "PRIVATE_VAULT_PATH"
# `.env.*` basename: env_guard.py denies agent reads and writes of it.
_CACHE_NAME = ".env.vault_cache.json"
_WILDCARDS = frozenset("*?[")


def _components(path: str) -> list[str]:
    """Path components, Win32-canonical: slash-separated, lowercased,
    trailing dots/spaces dropped, colon parts split out."""
    parts = []
    for comp in path.replace("\\", "/").split("/"):
        parts.extend(p.rstrip(". ").lower() for p in comp.split(":"))
    return [p for p in parts if p]


def _matches(name: str, pattern: str) -> bool:
    """fnmatchcase for a glob component, equality for a literal one (the
    same result: fnmatch escapes every other character). fnmatch is
    imported only for a pattern with a wildcard, which most calls never
    see."""
    if not _WILDCARDS & set(pattern):
        return name == pattern
    import fnmatch

    return fnmatch.fnmatchcase(name, pattern)


def _names_vault_dir(component: str) -> bool:
    # A literal match, or a glob component with at least one literal
    # character that can match the directory name (priv*, *_vault). Bare
    # `*`/`**` are a reach question, not naming -- see _reaches_vault().
    return component.strip("*") != "" and _matches(_VAULT_DIR, component)


def names_vault_path(candidate) -> bool:
    """True when `candidate` (a path, filename, or glob) names a
    private_vault directory or something inside one."""
    if not isinstance(candidate, str):
        return False
    return any(_names_vault_dir(c) for c in _components(candidate))


# ---------------------------------------------------------------------------
# Vault locations
# ---------------------------------------------------------------------------

def _resolutions(path: str, base: str) -> list[str]:
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


def path_forms(path: str, base: str) -> list[list[str]]:
    return [_components(r) for r in _resolutions(path, base)]


def _covers(parts: list[str], target: list[str], strict: bool = False) -> bool:
    """True when `parts` (a path, or a glob split into components) can name
    `target` or something below it; with `strict`, only something below."""
    for i, comp in enumerate(target):
        if i >= len(parts):
            return False
        if parts[i] == "**":
            return True
        if not _matches(comp, parts[i]):
            return False
    return not strict or len(parts) > len(target)


# path: the directory; forms: path_forms() of it; in_project: inside the
# project, so gitignored, and ripgrep skips it unless a glob whitelists its
# entries.
Vault = namedtuple("Vault", "path forms in_project")


def _note(message: str) -> None:
    print(f"[vault_guard] {message}; only the in-repo private_vault/ rules apply.", file=sys.stderr)


def _write_cache(cache: str, data: dict) -> None:
    """Atomic, best effort: a failed write only costs the next call a parse."""
    tmp = f"{cache}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, cache)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _configured_vault() -> str | None:
    """The raw PRIVATE_VAULT_PATH value, or None. Never printed."""
    value = (os.environ.get(_VAULT_KEY) or "").strip()
    if value:
        return value
    project = project_dir()
    env_path = os.path.join(project, ".env")
    try:
        stat = os.stat(env_path)
    except OSError:
        _note(".env not found")
        return None
    key = [stat.st_mtime_ns, stat.st_size]
    cache = os.path.join(project, ".claude", _CACHE_NAME)
    try:
        with open(cache, encoding="utf-8") as handle:
            cached = json.load(handle)
        hit = cached["key"] == key and isinstance(cached["value"], (str, type(None)))
        value = cached["value"] if hit else None
    except (OSError, ValueError, KeyError, TypeError):
        hit = False
    if not hit:
        value = env_file_value(_VAULT_KEY, env_path)
        _write_cache(cache, {"key": key, "value": value})
    if not value:
        _note(f"{_VAULT_KEY} is not set in .env")
    return value


def vault_dirs() -> list[Vault]:
    """Existing vault directories: <project>/private_vault and the
    configured vault, deduplicated."""
    project = project_dir()
    candidates = [os.path.join(project, _VAULT_DIR)]
    configured = _configured_vault()
    if configured:
        candidates.append(configured)
    project_forms = None  # resolved only once a vault directory exists
    vaults: list[Vault] = []
    for raw in candidates:
        # The directory, by the rule the app uses (realpath-resolved).
        path = project_path(raw)
        if not os.path.isdir(path):
            continue
        # Plus the spelling as configured: a glob written through a junction
        # or symlink name gets no realpath (it has wildcards), so it can
        # only match the lexical form.
        forms = path_forms(path, project)
        forms += [f for f in path_forms(raw, project) if f not in forms]
        if any(f in v.forms for v in vaults for f in forms):
            continue
        if project_forms is None:
            project_forms = path_forms(project, project)
        in_project = any(_covers(f, p) for f in forms for p in project_forms)
        vaults.append(Vault(path, forms, in_project))
    return vaults


def inside_vault(candidate: str, base: str, vaults: list[Vault]) -> bool:
    """True when `candidate` (a path or glob, relative to `base`) can name
    a location at or under one of `vaults`."""
    forms = path_forms(candidate, base)
    return any(_covers(f, v) for f in forms for vault in vaults for v in vault.forms)


def above_vault(forms: list[list[str]], vault: Vault) -> bool:
    """True when a directory (as `path_forms` components) is a strict ancestor
    of `vault`."""
    return any(_covers(v, f, strict=True) for f in forms for v in vault.forms)


# ---------------------------------------------------------------------------
# Reach
# ---------------------------------------------------------------------------

def _glob_reaches(pattern: str, roots: list[str], vault: Vault) -> bool:
    """A glob, joined to its search root, can match something below `vault`."""
    return any(
        _covers(_components(absolute_path(pattern, root)), v, strict=True)
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
        return any(_matches(n, parts[0].lower()) for n in names)
    return _glob_reaches(rooted, roots, vault)


def _reaches_vault(tool: str, tool_input: dict, cwd: str, vaults: list[Vault]) -> str | None:
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
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    if tool in ("Grep", "Glob"):
        return _reaches_vault(tool, tool_input, cwd, vaults)  # root inside, or reach
    path = tool_input.get("file_path")
    if isinstance(path, str) and path and inside_vault(path, cwd, vaults):
        return path
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    target = evaluate(payload)
    if target:
        deny(
            f"Vault Privacy Rule: vault content (private_vault/ or the "
            f"configured PRIVATE_VAULT_PATH) may not be read, listed, "
            f"or edited (matched '{target}'). If this is a search, "
            f"scope `path` to a subdirectory or anchor the pattern "
            f"(e.g. src/**/*.py). See CLAUDE.md and "
            f".claude/hooks/vault_guard.py."
        )


if __name__ == "__main__":
    main()
