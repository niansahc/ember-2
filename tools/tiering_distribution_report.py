"""
tools/tiering_distribution_report.py

ADR-015 amendment, implementation step 4 -- measurement tool.

Reports the hot/warm/cold tier distribution a synthetic corpus produces
under the OLD heat formula (recency*0.5 + access*0.3 + importance*0.2,
access driven by a monotonic retrieval_count) versus the NEW one
(recency*0.625 + access*0.375, access driven by a decayed frequency
accumulator), side by side.

Entirely synthetic fixture data -- no vault content, no real record ids
(CLAUDE.md vault privacy rule), following the precedent set by
tools/retrieval_ablation/corpus.py. Not a test: this is a one-off report
run manually and pasted into the PR description, same spirit as
tools/eval_retrieval.py.

Usage:
    python tools/tiering_distribution_report.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.tiering.tiering_service import (  # noqa: E402
    _access_score as new_access_score,
    _compute_heat as new_compute_heat,
    _recency_score as new_recency_score,
    _tier_from_heat,
)

HALFLIFE_DAYS = 30
ACCESS_CEILING = 10
HOT_THRESHOLD = 0.5
WARM_THRESHOLD = 0.2

# ---------------------------------------------------------------------------
# OLD model (origin/main, pre-step-4) -- reimplemented here rather than
# imported, since the old code no longer exists on this branch after the
# change it is being measured against.
# ---------------------------------------------------------------------------

OLD_IMPORTANCE_BY_TYPE: dict[str, float] = {
    "profile": 1.0,
    "state": 0.9,
    "reflection": 0.7,
    "journal": 0.6,
    "conversation": 0.4,
    "ingested": 0.3,
}


def old_recency_score(last_retrieved_at: str | None, created_at: str | None, halflife_days: int) -> float:
    """Reimplementation of the pre-amendment _recency_score: parses only a
    %Y-%m-%d date prefix, so epoch-float timestamps silently return 0.0.
    """
    reference = last_retrieved_at or created_at
    if not reference:
        return 0.0
    try:
        clean = reference.replace("Z", "").split("+")[0]
        date_part = clean.split("T")[0] if "T" in clean else clean[:10]
        ref_date = datetime.strptime(date_part, "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0.0
    days_ago = max((datetime.now() - ref_date).days, 0)
    if halflife_days <= 0:
        return 1.0 if days_ago == 0 else 0.0
    return 2 ** (-days_ago / halflife_days)


def old_access_score(retrieval_count: int, ceiling: int) -> float:
    if ceiling <= 0:
        return 0.0
    return min(retrieval_count / ceiling, 1.0)


def old_compute_heat(recency: float, access: float, importance: float) -> float:
    return (recency * 0.5) + (access * 0.3) + (importance * 0.2)


def old_importance_for_type(memory_type: str) -> float:
    return OLD_IMPORTANCE_BY_TYPE.get(memory_type, 0.5)


# ---------------------------------------------------------------------------
# Shared history simulation: turns a retrieval history into both models'
# native inputs (old: raw count; new: decayed frequency accumulator), so
# the same underlying scenario is scored fairly by both.
# ---------------------------------------------------------------------------


def simulate_frequency_score(gap_days: list[int], halflife: int) -> float:
    """Decay-then-increment accumulator, walked oldest-gap-first.

    gap_days: elapsed days between consecutive retrievals (first entry is
    the gap since the record was created). Mirrors
    SqliteVectorStore.update_retrieval_stats's per-event update.
    """
    freq = 0.0
    for gap in gap_days:
        decay = 2 ** (-gap / halflife) if halflife > 0 else 0.0
        freq = freq * decay + 1.0
    return freq


@dataclass(frozen=True)
class Fixture:
    label: str
    memory_type: str
    created_days_ago: int
    retrieval_gap_days: list[int]  # [] = never retrieved
    has_source_record_ids: bool | None = None  # reflection-only; None = n/a
    epoch_timestamp: bool = False  # created_at stored as a Unix epoch string

    @property
    def retrieval_count(self) -> int:
        return len(self.retrieval_gap_days)

    @property
    def last_retrieved_days_ago(self) -> int | None:
        if not self.retrieval_gap_days:
            return None
        # Days ago of the LAST retrieval = created_days_ago minus the sum
        # of all gaps consumed since creation.
        return max(self.created_days_ago - sum(self.retrieval_gap_days), 0)

    def created_at_str(self, epoch: bool = False) -> str:
        dt = datetime.now() - timedelta(days=self.created_days_ago)
        if epoch:
            return str(dt.timestamp())
        return dt.strftime("%Y-%m-%dT%H-%M-%S")

    def last_retrieved_at_str(self) -> str | None:
        days = self.last_retrieved_days_ago
        if days is None:
            return None
        dt = datetime.now() - timedelta(days=days)
        return dt.strftime("%Y-%m-%dT%H-%M-%S")


FIXTURES: list[Fixture] = [
    Fixture("hot: profile (hard override)", "profile", 400, []),
    Fixture("hot: unresolved state (hard override)", "state", 5, []),
    Fixture("recent conversation, never retrieved", "conversation", 3, []),
    Fixture("recent conversation, retrieved often", "conversation", 10, [3, 3, 2]),
    # gap_days are elapsed-time-between-retrievals, walked from creation
    # forward -- NOT days-ago-of-each-retrieval. A record "retrieved long
    # ago, then abandoned" needs SMALL gaps (retrievals clustered soon
    # after creation) so most of created_days_ago is left as untouched
    # silence before "now" -- see Fixture.last_retrieved_days_ago.
    Fixture("month-old journal, one retrieval shortly after creation, then silence", "journal", 45, [1]),
    Fixture(
        "legacy pattern: retrieved 4x shortly after creation, nothing "
        "since (the old permanent-floor case)",
        "conversation", 200, [5, 5, 5, 5],
    ),
    Fixture("old ingested doc, never retrieved", "ingested", 180, []),
    Fixture("very old reflection, no provenance (legacy, unprovenanced)", "reflection", 300, [], has_source_record_ids=False),
    Fixture("recent reflection with provenance", "reflection", 5, [], has_source_record_ids=True),
    Fixture("6-month-old conversation, never retrieved", "conversation", 180, []),
    Fixture("1-year-old journal, never retrieved", "journal", 365, []),
    Fixture(
        "epoch-timestamped conversation (reclassified ChatGPT import), recent",
        "conversation", 5, [], epoch_timestamp=True,
    ),
]


def score_old(fx: Fixture) -> tuple[float, str]:
    recency = old_recency_score(None, fx.created_at_str(epoch=fx.epoch_timestamp), HALFLIFE_DAYS)
    access = old_access_score(fx.retrieval_count, ACCESS_CEILING)
    importance = old_importance_for_type(fx.memory_type)
    heat = old_compute_heat(recency, access, importance)
    if fx.memory_type in ("profile", "state"):
        heat = max(heat, HOT_THRESHOLD)
        return heat, "hot"
    return heat, _tier_from_heat(heat, HOT_THRESHOLD, WARM_THRESHOLD)


def score_new(fx: Fixture) -> tuple[float, str]:
    last_retrieved = fx.last_retrieved_at_str()
    recency = new_recency_score(last_retrieved, fx.created_at_str(epoch=fx.epoch_timestamp), HALFLIFE_DAYS)
    frequency_score = simulate_frequency_score(fx.retrieval_gap_days, HALFLIFE_DAYS)
    freq_decayed = frequency_score * recency
    access = new_access_score(freq_decayed, ACCESS_CEILING)
    heat = new_compute_heat(recency, access)
    if fx.memory_type in ("profile", "state"):
        heat = max(heat, HOT_THRESHOLD)
        return heat, "hot"
    return heat, _tier_from_heat(heat, HOT_THRESHOLD, WARM_THRESHOLD)


def main() -> None:
    print(f"{'fixture':<70} {'old heat':>9} {'old tier':>9} {'new heat':>9} {'new tier':>9}")
    print("-" * 110)

    old_counts = {"hot": 0, "warm": 0, "cold": 0}
    new_counts = {"hot": 0, "warm": 0, "cold": 0}

    for fx in FIXTURES:
        old_heat, old_tier = score_old(fx)
        new_heat, new_tier = score_new(fx)
        old_counts[old_tier] += 1
        new_counts[new_tier] += 1
        flag = "  <-- CHANGED" if old_tier != new_tier else ""
        print(
            f"{fx.label:<70} {old_heat:>9.3f} {old_tier:>9} "
            f"{new_heat:>9.3f} {new_tier:>9}{flag}"
        )

    print("-" * 110)
    print(f"OLD distribution: {old_counts}")
    print(f"NEW distribution: {new_counts}")
    print()
    print(
        "Note: this is a mechanism check against synthetic fixtures, "
        "not a claim about the live corpus's distribution -- the live "
        "corpus has never been measured under a working access term."
    )


if __name__ == "__main__":
    main()
