"""
tools/rebuild_tiers.py

Rebuild the ADR-015 tier distribution, and decide out loud what to do
with the delivery history behind it.

Why a rebuild is needed at all: until #238, retrieval stats were written
for every record in the context packet, while the prompt rendered a slice
of it. 19.1% of writes on the production corpus went to records the model
never saw. The write is not a nudge either -- it sets last_retrieved_at
to now, which forces recency to 1.0 and heat to at least 0.625, over the
0.5 hot threshold outright. One appearance in a candidate set was a
guaranteed promotion to hot.

So the stored tier column was built partly from candidacy rather than
delivery, and re-running the tiering service over the same columns would
recompute the same answer from the same corrupt inputs.

The contaminated-history problem, stated rather than assumed
------------------------------------------------------------
A last_retrieved_at written before #238 cannot be told apart from an
earned one. Nothing recorded which writes were for rendered records: the
timestamp is the same either way, and the 19.1% figure is a rate over the
population, not a label on any row.

That leaves two honest options and no third:

  keep it   -- carry the contamination forward permanently, in the column
               that feeds the rank-1 delivery parameter (tier.cold,
               ST 0.2855)
  clear it  -- discard the delivery history entirely, including the ~81%
               that was earned, and let the corrected mechanism re-earn it

--reset-delivery-signal takes the second. It is destructive and it is the
default for a reason: the alternative launders a known-corrupt signal
into every measurement that reads tier from here on, and the signal it
protects was barely doing anything (18,565 of 18,680 records were already
cold). Without the flag the tool only recomputes tier from the stored
columns, which is the "keep it" option.

Clearing sets last_retrieved_at to NULL and frequency_score to 0.
_recency_score then falls back to created_at, so heat becomes a function
of record age until real deliveries accumulate.

Read the snapshot line before trusting any of this: the tool refuses to
run without one it has verified it can restore.

    python tools/rebuild_tiers.py --dry-run
    python tools/rebuild_tiers.py --artefacts DIR --reset-delivery-signal

Prints counts and tier names. Never a record, an id, or a timestamp.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TIERS = ("hot", "warm", "cold")


def distribution(db_path: Path) -> Counter:
    if not db_path.exists():
        return Counter()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return Counter({
            (row[0] or "(none)"): row[1]
            for row in conn.execute(
                "SELECT tier, count(*) FROM vectors GROUP BY tier")
        })
    finally:
        conn.close()


def tier_by_id(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {row[0]: (row[1] or "(none)")
                for row in conn.execute("SELECT id, tier FROM vectors")}
    finally:
        conn.close()


def delivery_signal_counts(db_path: Path) -> dict:
    """How much delivery history is actually stored, before it is cleared."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT count(*), "
            "sum(CASE WHEN last_retrieved_at IS NOT NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN COALESCE(frequency_score, 0) > 0 THEN 1 ELSE 0 END) "
            "FROM vectors"
        ).fetchone()
        return {"rows": rows[0], "with_last_retrieved": rows[1] or 0,
                "with_frequency": rows[2] or 0}
    finally:
        conn.close()


def reset_delivery_signal(db_path: Path) -> int:
    """Discard the contaminated delivery history. Destructive."""
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "UPDATE vectors SET last_retrieved_at = NULL, frequency_score = 0 "
            "WHERE last_retrieved_at IS NOT NULL OR COALESCE(frequency_score, 0) != 0"
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _print_distribution(label: str, dist: Counter, total: int) -> None:
    print(f"  {label}")
    for tier in TIERS:
        count = dist.get(tier, 0)
        share = f"{count / total:6.2%}" if total else "     -"
        print(f"    {tier:6} {count:8} {share}")
    other = {k: v for k, v in dist.items() if k not in TIERS}
    for name, count in sorted(other.items()):
        print(f"    {name:6} {count:8}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report the current state and change nothing")
    parser.add_argument("--artefacts",
                        help="directory for the snapshot; must be outside the "
                             "repository. Required unless --dry-run")
    parser.add_argument("--reset-delivery-signal", action="store_true",
                        help="discard last_retrieved_at and frequency_score "
                             "before recomputing (see the module docstring)")
    args = parser.parse_args()

    from src.core.config import get_private_vault_path
    from src.observability.guard_counters import assert_outside_repo

    vault = get_private_vault_path().resolve()
    stores = [vault / "embeddings" / name
              for name in ("memory.db", "ingested.db")]
    present = [p for p in stores if p.exists()]

    print(f"  vault  : {vault.name}")
    for path in present:
        signal = delivery_signal_counts(path)
        print(f"  {path.name:12} rows {signal['rows']:7} | "
              f"with last_retrieved_at {signal['with_last_retrieved']:6} | "
              f"with frequency_score {signal['with_frequency']:6}")

    before = Counter()
    for path in present:
        before.update(distribution(path))
    total = sum(before.values())
    print()
    _print_distribution("tier before", before, total)

    if args.dry_run:
        print()
        print("  dry run: nothing written")
        return 0

    if not args.artefacts:
        print("  REFUSED: --artefacts is required for a real run.")
        return 2

    artefacts = Path(args.artefacts)
    try:
        assert_outside_repo(artefacts)
    except ValueError as exc:
        print(f"  REFUSED: {exc}")
        return 2
    artefacts.mkdir(parents=True, exist_ok=True)

    from traffic_window import _snapshot_store

    print()
    for path in present:
        snapshot = artefacts / f"{path.name}.pre-tier-rebuild"
        details = _snapshot_store(path, snapshot)
        rehearsal = artefacts / f"{path.name}.rehearsal"
        rehearsed = _snapshot_store(snapshot, rehearsal)
        restorable = (
            rehearsed["integrity"] == "ok"
            and rehearsed["rows"] == details["rows"]
            and rehearsed["snapshot_sha256"] == details["snapshot_sha256"]
        )
        rehearsal.unlink(missing_ok=True)
        print(f"  snapshot {path.name:12} integrity={details['integrity']} "
              f"restore={'VERIFIED' if restorable else 'FAILED'}")
        if not restorable:
            print("  REFUSED: snapshot does not restore to itself.")
            return 2

    tiers_before = {}
    for path in present:
        tiers_before[path.name] = tier_by_id(path)

    if args.reset_delivery_signal:
        print()
        for path in present:
            cleared = reset_delivery_signal(path)
            print(f"  cleared delivery signal on {cleared} row(s) in {path.name}")
    else:
        print()
        print("  delivery signal KEPT: recomputing tier from the stored "
              "columns, contamination included")

    from src.tiering.tiering_service import TieringService

    transitions = TieringService().run()

    after = Counter()
    for path in present:
        after.update(distribution(path))
    print()
    _print_distribution("tier after", after, sum(after.values()))

    print()
    print("  movement")
    moved_up = moved_down = unchanged = 0
    order = {"cold": 0, "warm": 1, "hot": 2}
    detail: Counter = Counter()
    for path in present:
        now = tier_by_id(path)
        for record_id, new_tier in now.items():
            old_tier = tiers_before[path.name].get(record_id)
            if old_tier is None or old_tier == new_tier:
                unchanged += 1
                continue
            detail[f"{old_tier} -> {new_tier}"] += 1
            if order.get(new_tier, -1) > order.get(old_tier, -1):
                moved_up += 1
            else:
                moved_down += 1
    print(f"    unchanged     {unchanged:8}")
    print(f"    moved warmer  {moved_up:8}")
    print(f"    moved colder  {moved_down:8}")
    for name, count in sorted(detail.items(), key=lambda kv: -kv[1]):
        print(f"      {name:18} {count:8}")

    print()
    print(f"  service transitions: {transitions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
