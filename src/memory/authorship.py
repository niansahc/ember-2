"""
src/memory/authorship.py

Authorship classification for the SQLite-backed memory types (conversation,
profile, reflection, journal). Shared by the live write path
(src/memory/write_memory.py) and the reflection backfill script
(scripts/rebuild_authorship_reflections.py) so the two classifiers cannot
drift apart.

Mirrors and extends the ingestion-side classifier in
scripts/rebuild_authorship_index.py, which covers imported/third-party
content (ChatGPT exports, PDFs, books) instead of the live write path.

Recalling Too Well Phase 1, item 2: write_memory() never set authorship,
so every record defaulted to 'unknown' and took the ranker's conservative
0.5x multiplier on relational/identity queries -- including a user's own
profile and journal records, which should never be discounted that way.
"""

from __future__ import annotations

# Reflection sources that are Ember's own synthesis of the user's own vault
# content, written "in the user's voice, first person" by convention (see
# lodestone_synthesis.py Stage 3 prompt). Not third-party content -- treated
# as first_person so reflections are not discounted on relational/identity
# queries the way imported third-party content is.
_DERIVED_FIRST_PERSON_SOURCES = frozenset({
    "reflection_engine",
    "session_reflection",
})


def classify_authorship(
    memory_type: str,
    source: str | None,
    metadata: dict | None = None,
) -> str:
    """Derive an authorship label for a record about to be written.

    Returns one of: first_person, mixed, unknown. (third_party is reserved
    for ingested/imported content, classified separately by
    scripts/rebuild_authorship_index.py -- nothing on the live write path
    writes third-party content.)
    """
    metadata = metadata or {}

    if memory_type == "profile":
        # Profile records are canonical facts about the user -- always
        # first-person regardless of how they were captured (onboarding,
        # API, etc.).
        return "first_person"

    if memory_type == "journal":
        # Journal entries are always the user's own words.
        return "first_person"

    if memory_type == "reflection":
        if (source or "") in _DERIVED_FIRST_PERSON_SOURCES:
            return "first_person"
        return "unknown"

    if memory_type == "conversation":
        role = metadata.get("role")
        if role == "user":
            return "first_person"
        if role == "assistant":
            # Ember's own words: not the user's, but not third-party either.
            return "mixed"
        return "unknown"

    return "unknown"
