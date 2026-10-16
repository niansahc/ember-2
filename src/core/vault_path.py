"""
src/core/vault_path.py

The one resolution rule for a configured vault path (PRIVATE_VAULT_PATH,
VAULT_PATH_TEST, VAULT_PATH_LIVE, VAULT_PATH_DEMO).

Every reader of those values goes through resolve_configured_path():
src/core/config.py for the app, .claude/hooks/hook_utils.py for the hooks,
tools/retrieval_ablation/runner.py, the vault-swap endpoint, and the eval
helpers. Before this module existed the app resolved a relative value
against the process cwd while the hooks resolved it against the project
root, so one value could name two directories and the vault guard could
protect a directory the app was not using.

The rule:
  - unset, empty or whitespace-only: None (not configured);
  - `~` expands to the home directory;
  - a relative value resolves against the project root, never the cwd;
  - an absolute value passes through;
  - the result goes through os.path.realpath, which is what
    pathlib.Path.resolve() calls, so callers that compare two spellings of
    one directory see equal strings.

Dependency-free on purpose (os only, no pathlib, no app imports): the
PreToolUse hooks start a fresh Python process on every tool call and import
this module from there, so it must cost almost nothing to load.
"""

import os

# The checkout this file lives in: src/core/vault_path.py -> repo root.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_configured_path(value, root=None):
    """Return `value` as an absolute, realpath-normalised path string, or
    None when it is unset, empty or whitespace-only.

    `root` is the directory a relative value resolves against. It defaults
    to PROJECT_ROOT, read at call time so tests can point it at a tmp
    project. The hooks pass their project directory (CLAUDE_PROJECT_DIR,
    the same checkout in a real session); the ablation runner passes its
    REPO_ROOT.
    """
    value = (value or "").strip()
    if not value:
        return None
    base = root if root is not None else PROJECT_ROOT
    # os.path.join drops `base` when the value is absolute.
    return os.path.realpath(os.path.join(base, os.path.expanduser(value)))
