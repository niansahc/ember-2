"""
src/core/jsonio.py

Safe JSON file I/O helpers (ADR-039).

Canonical and derived vault JSON reads/writes route through these so that:
  - a corrupt or unreadable file never silently returns wrong data and never
    crashes an unrelated request path without an explicit, logged decision; and
  - a write is atomic: a reader sees either the old file or the new one, never
    a half-written file.

Read policy (safe_read_json):
  - On JSONDecodeError/OSError the failure is always logged (path + exception
    type only, never file content -- vault privacy rule).
  - If `default` is omitted, JsonIoError is raised so the caller surfaces the
    failure (e.g. a corrupt import must not look like a successful empty import).
  - If `default` is supplied, it is returned (e.g. preferences/config fall back;
    collection readers pass default=None and skip the bad file).

Write policy (safe_write_json):
  - Serialise, write to a uniquely-named temp file in the SAME directory, then
    os.replace() onto the target. os.replace is atomic on Windows and POSIX.
  - On OSError the temp file is cleaned up and JsonIoError is raised: a failed
    canonical write must be visible, not silent.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("ember.jsonio")

# Sentinel distinguishing "raise on failure" from "return this default".
# A plain None cannot serve as the sentinel because None is a legitimate,
# commonly-passed default value.
_RAISE = object()


class JsonIoError(Exception):
    """A JSON file could not be read/parsed or written, and the caller did not
    supply a fallback default."""


def safe_read_json(path: str | os.PathLike, *, default: Any = _RAISE) -> Any:
    """Read and parse a JSON file.

    On success returns the parsed object. On JSONDecodeError or OSError, logs
    the failure (path + exception type, never content) and either raises
    JsonIoError (when `default` is omitted) or returns `default`.
    """
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        # A missing file is normal (e.g. first run before anything is written),
        # not corruption -- do not log. Still raise when no default was given.
        if default is _RAISE:
            raise JsonIoError(f"missing JSON file: {p}") from exc
        return default
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[JSONIO] read failed: %s (%s)", p, type(exc).__name__)
        if default is _RAISE:
            raise JsonIoError(
                f"failed to read JSON: {p} ({type(exc).__name__})"
            ) from exc
        return default


def safe_write_json(path: str | os.PathLike, data: Any) -> Path:
    """Atomically write `data` as JSON to `path`.

    Writes to a uniquely-named temp file in the target's directory, then
    os.replace() onto the target so a reader never sees a half-written file.
    On OSError the temp file is removed and JsonIoError is raised (a failed
    canonical write must not be silent). Returns the written path.
    """
    p = Path(path)
    # Unique temp name in the SAME directory: same filesystem keeps os.replace
    # atomic, and the unique name prevents concurrent writers from clobbering
    # each other's temp file. mkdir/mkstemp failures are write failures too.
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp"
        )
    except OSError as exc:
        logger.warning("[JSONIO] write failed: %s (%s)", p, type(exc).__name__)
        raise JsonIoError(
            f"failed to write JSON: {p} ({type(exc).__name__})"
        ) from exc

    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, p)
    except OSError as exc:
        logger.warning("[JSONIO] write failed: %s (%s)", p, type(exc).__name__)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise JsonIoError(
            f"failed to write JSON: {p} ({type(exc).__name__})"
        ) from exc
    return p
