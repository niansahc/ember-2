"""
src/core/timestamps.py

Shared vault-record timestamp parsing.

Three formats appear across the corpus and are handled independently in
three call sites today (ranker._parse_age_days, prompt_builder._parse_timestamp,
and -- until this fix -- tiering_service._recency_score, which only handled
one of the three). This module is the parser for a fourth call site
(_recency_score) rather than a consolidation of the other two: those are
working and tested, and folding them in here is out of scope for the change
that introduced this file. See ADR-015 amendment, implementation step 4.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_vault_timestamp(timestamp: str | None) -> datetime | None:
    """Parse a vault-record timestamp into a timezone-aware datetime (UTC).

    Accepts three formats, in order:
    1. Unix epoch as a numeric string (e.g. "1715284775.822009").
    2. The vault canonical hyphenated form ``YYYY-MM-DDTHH-MM-SS``.
    3. Standard ISO 8601 (``YYYY-MM-DDTHH:MM:SS`` with optional Z / offset).

    Returns None when nothing parses.
    """
    if not timestamp:
        return None

    stripped = timestamp.strip()
    if not stripped:
        return None

    # Format 1: numeric-only string -> Unix epoch. Checked first so a
    # decimal epoch string isn't mis-truncated by the hyphenated branch.
    if stripped.replace(".", "", 1).isdigit():
        try:
            epoch = float(stripped)
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    # Format 2: vault canonical hyphenated "YYYY-MM-DDTHH-MM-SS[-ffffff]".
    try:
        if "T" in stripped:
            date_part, time_part = stripped.split("T", 1)
            segments = time_part.split("-")
            if len(segments) >= 3:
                colon_time = f"{segments[0]}:{segments[1]}:{segments[2]}"
                if len(segments) == 4:
                    colon_time += f".{segments[3]}"
                iso_str = f"{date_part}T{colon_time}"
                dt = datetime.fromisoformat(iso_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
    except (ValueError, IndexError):
        pass

    # Format 3: standard ISO 8601, optional trailing Z / offset.
    try:
        normalized = stripped.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    return None
