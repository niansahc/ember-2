"""
src/core/paths.py

Path containment for untrusted path input. Every place the API joins a
client-supplied segment onto a directory it owns (UI file serving, upload
saving, import path validation) goes through resolve_inside so the
invariant is written once.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("ember.paths")


def resolve_inside(root: Path, candidate: str | Path, *, direct_child: bool = False) -> Path | None:
    """Resolve `candidate` against `root` and return it only if it stays inside.

    Resolution follows symlinks and collapses `..` segments. An absolute or
    drive-letter candidate replaces `root` on join and therefore fails the
    containment check. `direct_child=True` additionally requires the result
    to sit immediately under `root` (no subdirectories). Unresolvable input
    (embedded NUL, overlong path, bad drive) returns None rather than raising.
    Existence is not checked; callers decide what to do with a missing file.
    """
    try:
        resolved_root = root.resolve()
        resolved = (resolved_root / candidate).resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(resolved_root):
        logger.warning("[PATHS] Rejected path escaping %s", resolved_root.name)
        return None
    if direct_child and resolved.parent != resolved_root:
        logger.warning("[PATHS] Rejected nested path under %s", resolved_root.name)
        return None
    return resolved
