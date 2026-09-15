"""
src/tiering/tiering_service.py

Hot/warm/cold memory tiering service (ADR-015).

Computes a composite heat score for each record in memory.db and
ingested.db, assigns a tier, and writes the updated tier and heat_score
only where values have changed. Logs transition counts.

Heat score formula (ADR-015 amendment, implementation step 4):
    heat = (recency_score * 0.625) + (access_score * 0.375)

The importance term is gone -- see "Importance ladder flattened" below.
access_score is now derived from a decayed frequency accumulator
(frequency_score), not a monotonic retrieval_count -- see "Activation
model" below.

Tier thresholds:
    hot:  heat >= TIER_HOT_THRESHOLD  (default 0.5)
    warm: heat >= TIER_WARM_THRESHOLD (default 0.2)
    cold: heat <  TIER_WARM_THRESHOLD

Hard overrides:
    - Profile memory: always hot
    - Unresolved state records: always hot (detected by memory_type)

Source-bound (ADR-015 amendment, PR #180, implementation step 2):
    - A reflection's tier is capped at the max tier among its
      metadata.source_record_ids. Derived content has no independent
      standing -- it may end up colder than its sources, never hotter.
    - No resolvable source_record_ids floors the record at cold (the
      legacy/unprovenanced rule). Partial resolution bounds against
      whatever DOES resolve; see src/tiering/source_bound.py.
    - Bound is computed against IMMEDIATE sources only, using each
      source's CURRENT tier at the start of this run -- not resolved
      transitively. Correctness across a derivation chain (e.g. a
      monthly reflection sourcing a daily reflection) is a convergence
      property across nightly runs, not something computed in one pass:
      a source's tier already reflects ITS OWN bound from the previous
      night. See source_bound.py's docstring for the full rationale.

Importance ladder flattened (ADR-015 amendment, implementation step 4):
    IMPORTANCE_BY_TYPE contributed nothing to heat -- its maximum
    contribution (0.2 x 1.0 = 0.20) was exactly TIER_WARM_THRESHOLD, so
    it could never lift a record above cold. Reflection longevity is now
    governed by source-bounding (above) instead of a type constant, and
    type may still affect ranking elsewhere -- it no longer affects
    decay. The two remaining terms (recency, access) are renormalized to
    sum to 1.0, preserving their original 5:3 relative weight.

Activation model (ADR-015 amendment, implementation step 4):
    Base-level activation is recency plus a *decaying* frequency term,
    both on the same decay curve (same halflife), computed nightly here.
    Context conditioning -- the second half of the ACT-R structure this
    ADR cites -- is ADR-007's existing project boost
    (ContextRanker.apply_project_boost, +0.15), applied per-query at
    retrieval rather than baked into the nightly tier. This amendment
    does not add a second context-conditioning mechanism.

    The frequency term is a decayed accumulator (frequency_score),
    updated at retrieval time by SqliteVectorStore.update_retrieval_stats
    -- not the old monotonic retrieval_count, which installed a
    permanent floor once a record was retrieved four times. See that
    method's docstring for the decay-then-increment logic.

Run nightly via daemon thread, or manually via POST /tiering/run.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from src.core.config import (
    get_private_vault_path,
    get_tier_access_ceiling,
    get_tier_hot_threshold,
    get_tier_recency_halflife_days,
    get_tier_warm_threshold,
)
from src.core.timestamps import parse_vault_timestamp
from src.tiering.source_bound import bound_tier

logger = logging.getLogger("ember.tiering")


def _recency_score(
    last_retrieved_at: str | None,
    created_at: str | None,
    halflife_days: int,
) -> float:
    """
    Exponential decay based on days since last retrieval (or creation).

    Returns 0.0-1.0. Halflife = the number of days at which score = 0.5.

    Parses vault-canonical hyphenated timestamps, ISO 8601, and Unix
    epoch strings via parse_vault_timestamp -- previously this only
    handled the hyphenated/date-prefix form, so every epoch-stamped
    record (e.g. ChatGPT imports) silently scored 0.0.
    """
    reference = last_retrieved_at or created_at
    if not reference:
        return 0.0

    ref_dt = parse_vault_timestamp(reference)
    if ref_dt is None:
        return 0.0

    now = datetime.now(ref_dt.tzinfo)
    days_ago = max((now - ref_dt).days, 0)

    if halflife_days <= 0:
        return 1.0 if days_ago == 0 else 0.0

    # Exponential decay: score = 2^(-days/halflife)
    return math.pow(2, -days_ago / halflife_days)


def _access_score(frequency_score: float, ceiling: int) -> float:
    """Normalized decayed-frequency accumulator. Saturates at 1.0 when
    frequency_score >= ceiling.

    frequency_score is the already-decayed accumulator written by
    SqliteVectorStore.update_retrieval_stats -- not a raw count -- so no
    further decay is applied here.
    """
    if ceiling <= 0:
        return 0.0
    return min(frequency_score / ceiling, 1.0)


def _compute_heat(recency: float, access: float) -> float:
    """Composite heat score (ADR-015 amendment, implementation step 4).

    Weights (0.625 / 0.375) preserve the original recency:access ratio
    (5:3) from the pre-amendment formula (0.5 : 0.3 : 0.2), renormalized
    to sum to 1.0 now that the importance term is gone. Max heat is still
    1.0, so TIER_HOT_THRESHOLD/TIER_WARM_THRESHOLD remain meaningful
    without a .env change.
    """
    return (recency * 0.625) + (access * 0.375)


def _tier_from_heat(heat: float, hot_threshold: float, warm_threshold: float) -> str:
    """Assign tier from heat score and thresholds."""
    if heat >= hot_threshold:
        return "hot"
    if heat >= warm_threshold:
        return "warm"
    return "cold"


def _source_record_ids_from_row(row: sqlite3.Row) -> list[str]:
    """Parse metadata.source_record_ids off a `vectors` row.

    Malformed or missing metadata is treated as "no provenance" (empty
    list), which bound_tier() floors at cold -- the same outcome as a
    record that genuinely has no source_record_ids, and the correct one:
    a row this service cannot read the provenance of gets no benefit of
    the doubt either.
    """
    raw_metadata = row["metadata"]
    if not raw_metadata:
        return []
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return []
    if not isinstance(metadata, dict):
        return []
    source_ids = metadata.get("source_record_ids")
    if not isinstance(source_ids, list):
        return []
    return [sid for sid in source_ids if isinstance(sid, str) and sid]


class TieringService:
    """Compute and assign tiers for all records in SQLite stores."""

    def run(self) -> dict:
        """
        Run tiering across memory.db and ingested.db.

        Returns a dict with transition counts:
            {"hot_to_warm": N, "warm_to_cold": N, ...}
        """
        vault = get_private_vault_path()
        halflife = get_tier_recency_halflife_days()
        ceiling = get_tier_access_ceiling()
        hot_threshold = get_tier_hot_threshold()
        warm_threshold = get_tier_warm_threshold()

        transitions: dict[str, int] = {
            "hot_to_warm": 0,
            "hot_to_cold": 0,
            "warm_to_hot": 0,
            "warm_to_cold": 0,
            "cold_to_hot": 0,
            "cold_to_warm": 0,
            "unchanged": 0,
            "total": 0,
        }

        # Source-bound needs an id -> tier lookup that spans BOTH databases,
        # because a reflection's sources can live in either one
        # (conversation/journal/reflection in memory.db, ingested in
        # ingested.db). tier is a SQLite-only column -- it is never
        # mirrored into the vault JSON body -- so this snapshot has to come
        # from SQLite directly rather than from resolve_source_records().
        # Built once, before either database is processed for reassignment,
        # so every reflection in this run is bounded against the SAME
        # snapshot (last night's settled tiers), not a mix of settled and
        # already-updated-this-run values depending on iteration order.
        tier_index = self._build_tier_index(vault)

        # Process each database
        for db_name in ["memory.db", "ingested.db"]:
            db_path = vault / "embeddings" / db_name
            if not db_path.exists():
                continue

            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                "SELECT id, memory_type, created_at, last_retrieved_at, "
                "frequency_score, tier, heat_score, metadata "
                "FROM vectors"
            )

            updates: list[tuple[str, float, str]] = []

            for row in cursor:
                record_id = row["id"]
                memory_type = row["memory_type"] or ""
                created_at = row["created_at"]
                last_retrieved = row["last_retrieved_at"]
                frequency_score = row["frequency_score"] or 0.0
                old_tier = row["tier"] or "hot"
                old_heat = row["heat_score"] or 1.0

                # Compute component scores. Frequency decays on the same
                # curve as recency (same reference timestamp, same
                # halflife) -- see module docstring's "Activation model".
                recency = _recency_score(last_retrieved, created_at, halflife)
                freq_decayed = frequency_score * recency
                access = _access_score(freq_decayed, ceiling)
                heat = _compute_heat(recency, access)

                # Hard overrides
                if memory_type == "profile":
                    new_tier = "hot"
                    heat = max(heat, hot_threshold)
                elif memory_type == "state":
                    # Unresolved state records stay hot
                    # (resolved ones would have importance dropped, but we
                    # can't distinguish resolved from unresolved in SQLite
                    # without parsing metadata — for now, state stays hot)
                    new_tier = "hot"
                    heat = max(heat, hot_threshold)
                else:
                    new_tier = _tier_from_heat(heat, hot_threshold, warm_threshold)

                if memory_type == "reflection":
                    # Source-bound (ADR-015 amendment step 2): a reflection
                    # may be colder than its sources, never hotter.
                    new_tier = bound_tier(
                        new_tier,
                        _source_record_ids_from_row(row),
                        tier_index,
                    )

                transitions["total"] += 1

                # Only write if changed
                if new_tier != old_tier or abs(heat - old_heat) > 0.01:
                    updates.append((new_tier, heat, record_id))

                    if old_tier != new_tier:
                        key = f"{old_tier}_to_{new_tier}"
                        transitions[key] = transitions.get(key, 0) + 1
                    else:
                        transitions["unchanged"] += 1
                else:
                    transitions["unchanged"] += 1

            # Batch write updates
            if updates:
                conn.executemany(
                    "UPDATE vectors SET tier = ?, heat_score = ? WHERE id = ?",
                    updates,
                )
                conn.commit()

            conn.close()
            logger.info(
                "[TIERING] %s: %d records processed, %d updated",
                db_name, transitions["total"], len(updates),
            )

        # Log transitions
        self._log_transitions(transitions, vault)

        return transitions

    def _build_tier_index(self, vault: Path) -> dict[str, str]:
        """id -> current tier, merged across memory.db and ingested.db.

        A lightweight, separate pass (id + tier only) rather than folding
        into the main per-row loop below, so every reflection processed in
        this run bounds against one consistent snapshot instead of a mix
        of pre- and post-update values that would depend on row order.
        """
        tier_index: dict[str, str] = {}
        for db_name in ["memory.db", "ingested.db"]:
            db_path = vault / "embeddings" / db_name
            if not db_path.exists():
                continue
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                for row in conn.execute("SELECT id, tier FROM vectors"):
                    tier_index[row["id"]] = row["tier"] or "hot"
            finally:
                conn.close()
        return tier_index

    def _log_transitions(self, transitions: dict, vault: Path) -> None:
        """Write transition counts to logs/tiering/YYYY-MM-DD.log."""
        log_dir = vault.parent / "logs" / "tiering"

        # If vault parent doesn't contain logs, use repo logs dir
        repo_log_dir = Path(__file__).resolve().parents[2] / "logs" / "tiering"
        log_dir = repo_log_dir

        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"

        entry = {
            "timestamp": datetime.now().isoformat(),
            "transitions": transitions,
        }

        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info("[TIERING] Transitions: %s", json.dumps(transitions))
