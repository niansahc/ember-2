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

Create policy (safe_create_json), for append-only records that must never
replace an existing file:
  - Same temp file, then os.link() onto the target. A hard link fails with
    FileExistsError when the target exists, atomically, so the existence check
    and the publish are one step and two writers can never both succeed.
  - On a filesystem without hard links (FAT/exFAT, some network shares) it
    falls back to an O_EXCL create: still exclusive, but a reader can briefly
    see a partly written file.
  - A taken name raises RecordExistsError; nothing is written.
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


def _write_failed(p: Path, exc: OSError) -> JsonIoError:
    logger.warning("[JSONIO] write failed: %s (%s)", p, type(exc).__name__)
    return JsonIoError(f"failed to write JSON: {p} ({type(exc).__name__})")


def _discard(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def _dump_to_fd(fd: int, written: Path, p: Path, data: Any) -> None:
    """Serialise `data` into the open file `fd` (the file at `written`), in
    the one canonical vault JSON encoding. On OSError, remove `written` and
    raise JsonIoError for target `p`."""
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        _discard(written)
        raise _write_failed(p, exc) from exc


def _write_temp_json(p: Path, data: Any) -> Path:
    """Write `data` to a new uniquely-named temp file beside `p`; return it.

    Unique temp name in the SAME directory: same filesystem keeps os.replace
    and os.link atomic, and the unique name prevents concurrent writers from
    clobbering each other's temp file. mkdir/mkstemp failures are write
    failures too.
    """
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp"
        )
    except OSError as exc:
        raise _write_failed(p, exc) from exc

    tmp_path = Path(tmp_name)
    _dump_to_fd(fd, tmp_path, p, data)
    return tmp_path


def safe_write_json(path: str | os.PathLike, data: Any) -> Path:
    """Atomically write `data` as JSON to `path`.

    Writes to a uniquely-named temp file in the target's directory, then
    os.replace() onto the target so a reader never sees a half-written file.
    On OSError the temp file is removed and JsonIoError is raised (a failed
    canonical write must not be silent). Returns the written path.
    """
    p = Path(path)
    tmp_path = _write_temp_json(p, data)
    try:
        os.replace(tmp_path, p)
    except OSError as exc:
        _discard(tmp_path)
        raise _write_failed(p, exc) from exc
    return p


class RecordExistsError(FileExistsError):
    """safe_create_json found its target name already taken. Nothing was
    written and the existing file is untouched."""


# One WARNING per process when the hard-link path is unavailable.
_link_fallback_logged = False


def safe_create_json(path: str | os.PathLike, data: Any) -> Path:
    """Write `data` as JSON to `path` only if `path` does not exist yet.

    For append-only records: an existing file is never replaced. The record
    is written to a temp file, then hard-linked onto the target. os.link
    fails with FileExistsError when the target exists, so the existence
    check and the publish are one atomic step even across processes.

    Raises RecordExistsError when the name is taken, JsonIoError on any
    other write failure. Returns the written path.
    """
    global _link_fallback_logged
    p = Path(path)
    tmp_path = _write_temp_json(p, data)
    try:
        os.link(tmp_path, p)
    except FileExistsError as exc:
        raise RecordExistsError("record already exists") from exc
    except OSError as exc:
        # No hard links on this filesystem (FAT/exFAT, some network shares).
        if not _link_fallback_logged:
            _link_fallback_logged = True
            logger.warning(
                "[JSONIO] hard link unavailable (%s); exclusive create falls back to O_EXCL",
                type(exc).__name__,
            )
        _create_exclusive(p, data)
    finally:
        _discard(tmp_path)
    return p


def _create_exclusive(p: Path, data: Any) -> None:
    """O_EXCL fallback for safe_create_json: exclusive, but not atomic for
    readers (one can briefly see a partly written file)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(p, flags)
    except FileExistsError as exc:
        raise RecordExistsError("record already exists") from exc
    except OSError as exc:
        raise _write_failed(p, exc) from exc
    # The file is ours (created exclusively above), so removing it on a
    # failed write is safe.
    _dump_to_fd(fd, p, p, data)
