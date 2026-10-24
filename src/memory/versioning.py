"""
src/memory/versioning.py

Version counters on append-only records (session, project, lodestone).

An edit never changes a record in place (Rule 3): it writes a new record
whose version is one above the version it built on, and readers keep the
highest version per logical id. Neither the wall clock nor file mtime can
order these records alone: both repeat inside one tick, and both step
backward (NTP correction, vault copy or restore).
"""

import logging
from typing import Any

logger = logging.getLogger("ember.versioning")


def coerce_version(value: Any) -> int:
    """A stored version counter as an int.

    Missing (None) is 0: records written before the counter existed. An
    integral float or a digit string reads as its integer. Anything else
    (a bool, a fraction, other text) reads as 0 and is logged by type, so
    a hand-edited record cannot outrank real versions by accident.
    """
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    logger.warning("Unreadable record version of type %s read as 0", type(value).__name__)
    return 0


def metadata_version(record: dict) -> int:
    """The version counter session and project records keep in metadata."""
    return coerce_version((record.get("metadata") or {}).get("version"))


def metadata_rank(record: dict) -> tuple[int, str]:
    """Order for session and project records: version first; the timestamp
    only orders records with equal versions (all 0 before the counter)."""
    return metadata_version(record), record.get("timestamp", "")
