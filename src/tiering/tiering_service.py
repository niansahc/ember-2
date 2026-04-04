"""
src/tiering/tiering_service.py

Hot/warm/cold memory tiering service (ADR-015).

Computes a composite heat score for each record in memory.db and
ingested.db, assigns a tier, and writes the updated tier and heat_score
only where values have changed. Logs transition counts.

Heat score formula:
    heat = (recency_score * 0.5) + (access_score * 0.3) + (importance_score * 0.2)

Tier thresholds:
    hot:  heat >= TIER_HOT_THRESHOLD  (default 0.5)
    warm: heat >= TIER_WARM_THRESHOLD (default 0.2)
    cold: heat <  TIER_WARM_THRESHOLD

Hard overrides:
    - Profile memory: always hot
    - Unresolved state records: always hot (detected by memory_type)

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

logger = logging.getLogger("ember.tiering")

# Importance score heuristic by memory_type (ADR-015)
IMPORTANCE_BY_TYPE: dict[str, float] = {
    "profile": 1.0,
    "state": 0.9,
    "reflection": 0.7,
    "journal": 0.6,
    "conversation": 0.4,
    "ingested": 0.3,
}
DEFAULT_IMPORTANCE = 0.5


def _importance_for_type(memory_type: str | None) -> float:
    """Return importance_score heuristic for a memory_type."""
    return IMPORTANCE_BY_TYPE.get(memory_type or "", DEFAULT_IMPORTANCE)


def _recency_score(
    last_retrieved_at: str | None,
    created_at: str | None,
    halflife_days: int,
) -> float:
    """
    Exponential decay based on days since last retrieval (or creation).

    Returns 0.0-1.0. Halflife = the number of days at which score = 0.5.
    """
    reference = last_retrieved_at or created_at
    if not reference:
        return 0.0

    try:
        # Handle hyphenated Ember timestamps: 2026-04-03T16-53-55
        clean = reference.replace("Z", "").split("+")[0]
        if "T" in clean:
            date_part = clean.split("T")[0]
        else:
            date_part = clean[:10]
        ref_date = datetime.strptime(date_part, "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0.0

    days_ago = max((datetime.now() - ref_date).days, 0)

    if halflife_days <= 0:
        return 1.0 if days_ago == 0 else 0.0

    # Exponential decay: score = 2^(-days/halflife)
    return math.pow(2, -days_ago / halflife_days)


def _access_score(retrieval_count: int, ceiling: int) -> float:
    """Normalized retrieval count. Saturates at 1.0 when count >= ceiling."""
    if ceiling <= 0:
        return 0.0
    return min(retrieval_count / ceiling, 1.0)


def _compute_heat(
    recency: float, access: float, importance: float
) -> float:
    """Composite heat score (ADR-015)."""
    return (recency * 0.5) + (access * 0.3) + (importance * 0.2)


def _tier_from_heat(heat: float, hot_threshold: float, warm_threshold: float) -> str:
    """Assign tier from heat score and thresholds."""
    if heat >= hot_threshold:
        return "hot"
    if heat >= warm_threshold:
        return "warm"
    return "cold"


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

        # Process each database
        for db_name in ["memory.db", "ingested.db"]:
            db_path = vault / "embeddings" / db_name
            if not db_path.exists():
                continue

            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                "SELECT id, memory_type, created_at, last_retrieved_at, "
                "retrieval_count, importance_score, tier, heat_score "
                "FROM vectors"
            )

            updates: list[tuple[str, float, str, str]] = []

            for row in cursor:
                record_id = row["id"]
                memory_type = row["memory_type"] or ""
                created_at = row["created_at"]
                last_retrieved = row["last_retrieved_at"]
                retrieval_count = row["retrieval_count"] or 0
                old_importance = row["importance_score"]
                old_tier = row["tier"] or "hot"
                old_heat = row["heat_score"] or 1.0

                # Compute importance from type heuristic
                importance = _importance_for_type(memory_type)

                # Compute component scores
                recency = _recency_score(last_retrieved, created_at, halflife)
                access = _access_score(retrieval_count, ceiling)
                heat = _compute_heat(recency, access, importance)

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

                transitions["total"] += 1

                # Only write if changed
                if new_tier != old_tier or abs(heat - old_heat) > 0.01 or abs(importance - (old_importance or 0.5)) > 0.01:
                    updates.append((new_tier, heat, importance, record_id))

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
                    "UPDATE vectors SET tier = ?, heat_score = ?, importance_score = ? WHERE id = ?",
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
