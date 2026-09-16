"""
tools/retrieval_ablation/report.py

Aggregates arm results into per-arm deltas against A0_FULL.

Every metric is reported over TWO windows, and the pair is the point:

  delivered -- the packet the model actually sees, after the per-policy limit
               and the profile guaranteed-slot partition.
  ranked    -- the ranker's full verdict, before either of those.

A mechanism that moves `ranked` but not `delivered` is masked by slot
allocation. A mechanism that moves neither does nothing. Those are different
findings and the delivered set alone cannot tell them apart -- which matters
here, because tiering turns out to be the first kind, and reporting only the
delivered set would have called it the second.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .corpus import FIXTURES, FIXTURES_BY_ID, STRATA
from .metrics import (
    K_SERVICE,
    Delivered,
    QueryLabels,
    compare_to_reference,
    contamination_at_k,
    evaluate_query,
    leakage_by_class,
    mean_ignoring_none,
    ndcg_at_k,
    unresolved_fraction,
)

REFERENCE_ARM = "A0_FULL"


def _labels(stratum) -> QueryLabels:
    grades = dict(stratum.judgments)
    distractors = {
        fid: FIXTURES_BY_ID[fid].distractor_class
        for fid in stratum.judgments
        if FIXTURES_BY_ID[fid].distractor_class
    }
    return QueryLabels(
        grades=grades, distractor_classes=distractors, abstain=stratum.abstain
    )


def _to_delivered(rows) -> list[Delivered]:
    return [
        Delivered(
            id=r["id"], score=r["score"], memory_type=r["memory_type"], tier=r["tier"]
        )
        for r in rows
    ]


def summarise_arm(arm_payload: dict, reference_payload: dict | None) -> dict:
    """Per-stratum and aggregate metrics for one arm, over both windows."""
    per_stratum: dict[str, dict] = {}

    for stratum in STRATA:
        cell = arm_payload["cells"][stratum.name]
        labels = _labels(stratum)
        delivered = _to_delivered(cell["delivered"])
        ranked = _to_delivered(cell["ranked"])

        entry = {
            "delivered": evaluate_query(delivered, labels),
            "ranked": {
                "count": len(ranked),
                "ndcg_at_6": ndcg_at_k(ranked, labels, K_SERVICE),
                "contamination_at_4": contamination_at_k(
                    [i for i in ranked if i.memory_type != "profile"], labels
                ),
                "leakage_by_class": leakage_by_class(
                    [i for i in ranked if i.memory_type != "profile"], labels
                ),
                "unresolved_fraction": unresolved_fraction(ranked),
            },
            "residual_quota": cell["residual_quota"],
        }

        if reference_payload is not None:
            ref_cell = reference_payload["cells"][stratum.name]
            entry["vs_reference"] = {
                "delivered": compare_to_reference(
                    delivered, _to_delivered(ref_cell["delivered"])
                ),
                "ranked": compare_to_reference(ranked, _to_delivered(ref_cell["ranked"])),
            }
        per_stratum[stratum.name] = entry

    def agg(window: str, key: str):
        return mean_ignoring_none(
            per_stratum[s.name][window].get(key) for s in STRATA
        )

    aggregates = {
        "delivered_ndcg_at_6": agg("delivered", "ndcg_at_6"),
        "delivered_ndcg_at_4": agg("delivered", "ndcg_at_4_model_visible"),
        "delivered_contamination_at_4": agg("delivered", "contamination_at_4"),
        "delivered_unresolved": agg("delivered", "unresolved_fraction"),
        "ranked_ndcg_at_6": agg("ranked", "ndcg_at_6"),
        "ranked_contamination_at_4": agg("ranked", "contamination_at_4"),
        "ranked_unresolved": agg("ranked", "unresolved_fraction"),
    }

    if reference_payload is not None:
        aggregates["delivered_jaccard_vs_ref"] = mean_ignoring_none(
            per_stratum[s.name]["vs_reference"]["delivered"]["selection_jaccard"]
            for s in STRATA
        )
        aggregates["ranked_jaccard_vs_ref"] = mean_ignoring_none(
            per_stratum[s.name]["vs_reference"]["ranked"]["selection_jaccard"]
            for s in STRATA
        )
        aggregates["delivered_displacement_vs_ref"] = mean_ignoring_none(
            per_stratum[s.name]["vs_reference"]["delivered"]["rank_displacement"]
            for s in STRATA
        )
        aggregates["ranked_displacement_vs_ref"] = mean_ignoring_none(
            per_stratum[s.name]["vs_reference"]["ranked"]["rank_displacement"]
            for s in STRATA
        )
        aggregates["delivered_changed_strata"] = sum(
            1
            for s in STRATA
            if [i["id"] for i in arm_payload["cells"][s.name]["delivered"]]
            != [i["id"] for i in reference_payload["cells"][s.name]["delivered"]]
        )
        aggregates["ranked_changed_strata"] = sum(
            1
            for s in STRATA
            if [i["id"] for i in arm_payload["cells"][s.name]["ranked"]]
            != [i["id"] for i in reference_payload["cells"][s.name]["ranked"]]
        )

    leakage: dict[str, int] = {}
    for stratum in STRATA:
        for cls, n in per_stratum[stratum.name]["delivered"]["leakage_by_class"].items():
            leakage[cls] = leakage.get(cls, 0) + n

    return {
        "arm": arm_payload["arm"],
        "label": arm_payload["label"],
        "aggregates": aggregates,
        "leakage_total": leakage,
        "per_stratum": per_stratum,
    }


def _fmt(value, width: int = 6) -> str:
    if value is None:
        return "n/a".rjust(width)
    return f"{value:.3f}".rjust(width)


def _delta(value, reference) -> str:
    if value is None or reference is None:
        return "    n/a"
    return f"{value - reference:+.3f}".rjust(7)


def render_report(raw: dict) -> tuple[str, dict]:
    arms = raw["arms"]
    reference_payload = arms[REFERENCE_ARM]
    summaries = {
        name: summarise_arm(payload, None if name == REFERENCE_ARM else reference_payload)
        for name, payload in arms.items()
    }
    ref_agg = summaries[REFERENCE_ARM]["aggregates"]

    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("RETRIEVAL-ARCHITECTURE ABLATION")
    add("=" * 78)
    add(f"Run:   {datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}")
    add(f"Vault: {raw['vault']}")
    add(
        f"Corpus: {len(STRATA)} strata, {len(FIXTURES)} fixtures, graded 0-3 by "
        "construction, cosines measured"
    )
    add("")
    add("Question (TDD 50.1 / 25.3): how much does the memory-type taxonomy and")
    add("hot/warm/cold tiering add over a naive verbatim + embedding baseline?")
    add("")

    add("-" * 78)
    add("DELIVERED -- what the model actually sees")
    add("-" * 78)
    add(f"{'arm':22} {'nDCG@6':>7} {'d':>7} {'nDCG@4':>7} {'d':>7} {'contam@4':>9} {'d':>7}")
    for name, summary in summaries.items():
        a = summary["aggregates"]
        add(
            f"{name:22} "
            f"{_fmt(a['delivered_ndcg_at_6'], 7)} "
            f"{_delta(a['delivered_ndcg_at_6'], ref_agg['delivered_ndcg_at_6'])} "
            f"{_fmt(a['delivered_ndcg_at_4'], 7)} "
            f"{_delta(a['delivered_ndcg_at_4'], ref_agg['delivered_ndcg_at_4'])} "
            f"{_fmt(a['delivered_contamination_at_4'], 9)} "
            f"{_delta(a['delivered_contamination_at_4'], ref_agg['delivered_contamination_at_4'])}"
        )

    add("")
    add("-" * 78)
    add("RANKED -- the ranker's verdict, before the limit and profile slots")
    add("-" * 78)
    add(f"{'arm':22} {'nDCG@6':>7} {'d':>7} {'contam@4':>9} {'d':>7}")
    for name, summary in summaries.items():
        a = summary["aggregates"]
        add(
            f"{name:22} "
            f"{_fmt(a['ranked_ndcg_at_6'], 7)} "
            f"{_delta(a['ranked_ndcg_at_6'], ref_agg['ranked_ndcg_at_6'])} "
            f"{_fmt(a['ranked_contamination_at_4'], 9)} "
            f"{_delta(a['ranked_contamination_at_4'], ref_agg['ranked_contamination_at_4'])}"
        )

    add("")
    add("-" * 78)
    add("MOVEMENT -- masked vs inert")
    add("-" * 78)
    add("An arm that moves `ranked` but not `delivered` is masked by slot")
    add("allocation, not inert. The delivered set alone cannot tell them apart.")
    add("")
    add(f"{'arm':22} {'strata changed':>15} {'displacement':>26}")
    add(f"{'':22} {'deliv':>7} {'ranked':>7} {'deliv':>12} {'ranked':>13}")
    for name, summary in summaries.items():
        if name == REFERENCE_ARM:
            continue
        a = summary["aggregates"]
        add(
            f"{name:22} "
            f"{a['delivered_changed_strata']:>7}/8 "
            f"{a['ranked_changed_strata']:>5}/8 "
            f"{_fmt(a['delivered_displacement_vs_ref'], 12)} "
            f"{_fmt(a['ranked_displacement_vs_ref'], 13)}"
        )

    add("")
    add("-" * 78)
    add("DISTRACTOR LEAKAGE (delivered, by targeted mechanism)")
    add("-" * 78)
    for name, summary in summaries.items():
        total = sum(summary["leakage_total"].values())
        detail = ", ".join(
            f"{cls}={n}" for cls, n in sorted(summary["leakage_total"].items())
        )
        add(f"{name:22} total={total:<3} {detail or '-'}")

    add("")
    add("-" * 78)
    add("LEAKAGE ATTRIBUTION -- measured, not assumed")
    add("-" * 78)
    add("A distractor class names the lever it was BUILT to bait. That label is a")
    add("design intention. The column that matters is the measured one: which")
    add("lever actually carries the bait above the records it displaces. Where")
    add("they differ, read the leakage count above as naming the DOMINANT lever,")
    add("not the class name.")
    add("")
    add(f"{'bait':32} {'class':22} {'labelled':>9} {'dominant lever':>18} {'swing':>7}")
    attribution = arms[REFERENCE_ARM].get("attribution", {})
    entangled_rows = 0
    for stratum_name, rows in attribution.items():
        for row in rows:
            flag = " *" if row.get("entangled") else ""
            entangled_rows += 1 if row.get("entangled") else 0
            add(
                f"{row['bait'][:32]:32} {row['class']:22} "
                f"{row['labelled_swing']:+9.3f} "
                f"{row['dominant_lever']:>18} {row['dominant_swing']:+7.3f}{flag}"
            )
    if entangled_rows:
        add("")
        add("  * recency and decay are NOT separable by construction, and no corpus")
        add("    can make them so. Both key on the same input -- the record's age --")
        add("    but act on opposite sides of the pair: the additive freshness bonus")
        add("    lifts the fresh distractor, and the multiplicative decay penalty")
        add("    pushes down the older relevant record it displaces. A fresh bait is")
        add("    helped twice by two mechanisms a minimal pair cannot tell apart,")
        add("    because holding age constant disables both at once. That is a")
        add("    property of the pipeline, not a limit of these fixtures, and it is")
        add("    printed rather than tuned away.")

    add("")
    add("-" * 78)
    add("SEPARABILITY")
    add("-" * 78)
    for label, arm_name in (
        ("typing, scoring only     ", "A_T-off_scoring"),
        ("typing, incl. gating     ", "A_T-off_policy"),
        ("tiering                  ", "A_H-off"),
        ("temporal decay           ", "A_decay-off"),
        ("recency bonus            ", "A_recency-off"),
        ("lexical and entity terms ", "A_lexical-off"),
        ("source quality           ", "A_quality-off"),
    ):
        a = summaries[arm_name]["aggregates"]
        d_ranked = a["ranked_ndcg_at_6"]
        base = ref_agg["ranked_ndcg_at_6"]
        verdict = _movement_verdict(a)
        delta = "n/a" if d_ranked is None or base is None else f"{base - d_ranked:+.3f}"
        add(f"  {label} ranked nDCG cost of removing: {delta:>8}   {verdict}")

    add("")
    add("-" * 78)
    add("READING THIS RESULT")
    add("-" * 78)
    for line in CAVEATS:
        add(line)

    add("")
    add(f"Tiering databases unchanged by the run: {raw['databases_unchanged']}")

    payload = {
        "generated": datetime.now().isoformat(),
        "vault": raw["vault"],
        "databases_unchanged": raw["databases_unchanged"],
        "reference_arm": REFERENCE_ARM,
        "arms": summaries,
    }
    return "\n".join(lines), payload


def _movement_verdict(aggregates: dict) -> str:
    delivered = aggregates.get("delivered_changed_strata", 0)
    ranked = aggregates.get("ranked_changed_strata", 0)
    if ranked == 0:
        return "no effect on ranking at all"
    if delivered == 0:
        return "moves ranking, masked by slot allocation"
    return "moves ranking and delivery"


CAVEATS: tuple[str, ...] = (
    "1. As of the ADR-015 amendment (v0.19.0, steps 4-5), the three defects",
    "   this caveat used to describe are fixed: ContextItem.store_id now",
    "   carries the real vectors primary key, so update_retrieval_stats",
    "   actually matches rows; _recency_score parses epoch/ISO/hyphenated",
    "   timestamps via the shared helper instead of returning 0.0 for",
    "   float-epoch created_at; and the importance-by-type ladder is",
    "   removed, so heat no longer has a TYPE-lookup term at all. Measured",
    "   directly against the live vault post-fix: still ~99.9% cold, zero",
    "   warm -- but now because the (reclassified, formerly-ingested)",
    "   corpus is genuinely old with zero retrieval history, not because",
    "   heat was being computed from broken or type-only inputs. A",
    "   `tiering does not help` reading from THIS run is still a statement",
    "   about synthetic fixtures, not the deployed system -- read it as",
    "   measuring the MECHANISM, and cross-check against a fresh live-vault",
    "   tier distribution if the production claim matters.",
    "",
    "2. Profile records take three guaranteed slots on every query because",
    "   get_profile_items' min_score floor is dead on the SQLite path. Under",
    "   the reflective policy that leaves ONE non-profile slot. This masks",
    "   other mechanisms from the delivered set and is why the ranked window",
    "   is reported as a first-class metric rather than a diagnostic.",
    "",
    "3. Diversity selection is forced off in every arm as a controlled",
    "   variable: it is a hard round-robin quota that would clamp composition",
    "   identically everywhere. The residual is recorded per stratum.",
    "",
    "4. This measures RETRIEVAL, not generation. Type still reaches the model",
    "   through prompt-layer labels even when scoring is ablated.",
    "",
    "5. Base cosine is MEASURED with nomic-embed-text over the fixture texts and",
    "   frozen, not stipulated. The first build of this eval hand-assigned it and",
    "   made it agree with the grades -- cosine alone scored nDCG@6 0.937, so the",
    "   naive baseline held the answer key, the pipeline had nothing to fix, and",
    "   every ablation read as an improvement. The corpus tests now pin the",
    "   headroom: naive-cosine nDCG must stay under 0.80, some relevant records",
    "   must start below the cut, and some baits must start above it.",
)


def write_report(text: str, payload: dict, repo_root: Path) -> tuple[Path, Path]:
    out_dir = repo_root / "logs" / "retrieval_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_path = out_dir / f"ablation_{stamp}.log"
    json_path = out_dir / f"ablation_{stamp}.json"
    log_path.write_text(text, encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return log_path, json_path
