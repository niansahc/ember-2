"""
scripts/test_deviation_detection.py

Automated test session for deviation detection calibration.
Seeds known positions, sends simulated conversation turns through
the pipeline, then audits which deviation records were written.

IMPORTANT: Deviation detection runs inside the API server process,
not in this script. The EMBER_DEVIATION_DETECTION env var must be
set in .env BEFORE starting the API. Setting it in this script's
process has no effect on the already-running server.

Setup (required before running):
    1. Set EMBER_DEVIATION_DETECTION=true in .env
    2. Restart the API: ./start_api.bat (Windows) or ./start_api.sh (Mac/Linux)
    3. Run this script: python scripts/test_deviation_detection.py

Requires:
- Live Ollama instance
- Running Ember-2 API with EMBER_DEVIATION_DETECTION=true in .env
- PRIVATE_VAULT_PATH set in .env

Usage:
    python scripts/test_deviation_detection.py

Output:
    - Terminal: full report
    - docs/test-reports/deviation-detection-report.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Preflight checks ─────────────────────────────────────────────────────

def _check_ollama() -> bool:
    try:
        import ollama
        ollama.list()
        return True
    except Exception:
        return False


def _check_api() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:8000/api/health")
        key = _get_api_key()
        if key:
            req.add_header("X-API-Key", key)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_api_key() -> str | None:
    try:
        import keyring
        return keyring.get_password("ember-2", "api_key")
    except Exception:
        return os.getenv("EMBER_API_KEY")


# ── Lodestone seed management ────────────────────────────────────────────

SEED_POSITIONS = [
    {
        "value": "Values creative autonomy over structured process",
        "taxonomy_category": "character",
    },
    {
        "value": "Prefers direct communication without softening",
        "taxonomy_category": "character",
    },
    {
        "value": "Prioritizes privacy over convenience",
        "taxonomy_category": "directional",
    },
    {
        "value": "Skeptical of centralized AI platforms",
        "taxonomy_category": "directional",
    },
    {
        "value": "Values iterative progress over big releases",
        "taxonomy_category": "directional",
    },
    {
        "value": "Prefers working independently",
        "taxonomy_category": "relational",
    },
]


def _temporarily_unconfirm_existing() -> list[str]:
    """Unconfirm existing active lodestone records to make room for seeds. Returns their IDs."""
    from src.memory.lodestone_service import read_active, update
    active = read_active()
    ids = []
    for rec in active:
        update(rec["id"], {"confirmed": False})
        ids.append(rec["id"])
    if ids:
        print(f"  Temporarily unconfirmed {len(ids)} existing lodestone records")
    return ids


def _restore_existing(record_ids: list[str]) -> None:
    """Re-confirm previously unconfirmed lodestone records."""
    from src.memory.lodestone_service import update
    restored = 0
    for rid in record_ids:
        result = update(rid, {"confirmed": True})
        if result:
            restored += 1
    print(f"  Restored {restored} existing lodestone records")


def _seed_lodestone_records() -> list[str]:
    """Write lodestone seed records directly to vault. Returns record IDs."""
    from src.memory.lodestone_service import write
    ids = []
    for pos in SEED_POSITIONS:
        rec = write(
            value=pos["value"],
            taxonomy_category=pos["taxonomy_category"],
            acquisition_path="explicit",
            source="test_deviation_harness",
            confirmed=True,
        )
        ids.append(rec["id"])
        print(f"  Seeded: {pos['value']}")
    return ids


def _cleanup_lodestone_records(record_ids: list[str]) -> None:
    """Remove seeded lodestone records by deleting their files."""
    from src.core.config import get_private_vault_path
    vault = get_private_vault_path()
    lodestone_dir = vault / "memory" / "lodestone"
    removed = 0
    for f in lodestone_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("id") in record_ids:
                f.unlink()
                removed += 1
        except Exception:
            continue
    print(f"  Cleaned up {removed} seeded lodestone records")


# ── Test conversation turns ──────────────────────────────────────────────

TEST_CATEGORIES = {
    "1_restated": {
        "label": "Category 1 — Restated positions (should NOT trigger)",
        "expected_fires": False,
        "turns": [
            "I really don't trust cloud-based AI services",
            "I'd rather ship small and often than wait for a big launch",
            "Just tell me straight, don't sugarcoat it",
        ],
    },
    "2_new_opinions": {
        "label": "Category 2 — New opinions, no conflict (should trigger)",
        "expected_fires": True,
        "turns": [
            "I've been thinking a lot about solarpunk lately, it really resonates",
            "I want to start prioritizing sleep over late night coding sessions",
            "Honestly I think documentation matters more than I used to admit",
        ],
    },
    "3_reversals": {
        "label": "Category 3 — Genuine reversals (should trigger, high entropy)",
        "expected_fires": True,
        "turns": [
            "Actually I think working with a team might be better than solo work",
            "I'm starting to think some cloud services are worth the tradeoff",
            "Maybe big planned releases are better than constant small ones",
        ],
    },
    "4_noise": {
        "label": "Category 4 — Noise (should NOT trigger)",
        "expected_fires": False,
        "turns": [
            "What's the weather like today",
            "Can you help me write a grocery list",
            "Tell me about the history of Richmond",
        ],
    },
    "5_edge_cases": {
        "label": "Category 5 — Edge cases (document, no pass/fail)",
        "expected_fires": None,  # No expectation
        "turns": [
            "I dunno, maybe I'm wrong about the cloud stuff",
            "Sometimes I wonder if I'm too rigid about process",
            "Part of me wants to try pair programming but I'm not sure",
        ],
    },
}


# ── Send conversation turn through API ───────────────────────────────────

def _send_turn(message: str, session_id: str) -> dict:
    """Send a conversation turn through the full pipeline via API."""
    import urllib.request

    api_key = _get_api_key()
    url = "http://localhost:8000/v1/chat/completions"

    body = json.dumps({
        "model": "ember-2",
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }).encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Session-ID", session_id)
    req.add_header("X-Test-Session", "true")
    if api_key:
        req.add_header("X-API-Key", api_key)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "reply": reply[:200]}
    except Exception as exc:
        return {"success": False, "reply": "", "error": str(exc)}


# ── Read deviation records from vault ────────────────────────────────────

def _read_deviation_records_since(start_time: str) -> list[dict]:
    """Read all deviation records written after start_time."""
    from src.core.config import get_private_vault_path
    vault = get_private_vault_path()
    dev_dir = vault / "memory" / "deviation"
    if not dev_dir.exists():
        return []

    records = []
    for f in sorted(dev_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("type") != "deviation":
                continue
            if data.get("timestamp", "") >= start_time:
                records.append(data)
        except Exception:
            continue
    return records


# ── Read deviation logs ──────────────────────────────────────────────────

def _read_deviation_logs_since(start_time: str) -> list[dict]:
    """Read deviation log entries written after start_time."""
    log_dir = Path(__file__).resolve().parents[1] / "logs" / "deviation"
    if not log_dir.exists():
        return []

    entries = []
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"{today}.log"
    if not log_file.exists():
        return []

    for line in log_file.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("ts", "") >= start_time:
                entries.append(entry)
        except Exception:
            continue
    return entries


# ── Report generation ────────────────────────────────────────────────────

def _generate_report(
    results: dict[str, list[dict]],
    deviation_records: list[dict],
    deviation_logs: list[dict],
    start_time: str,
) -> str:
    lines = []
    lines.append("# Deviation Detection Test Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Session start:** {start_time}")
    lines.append(f"**Total deviation records written:** {len(deviation_records)}")
    lines.append(f"**Total detection log entries:** {len(deviation_logs)}")
    lines.append("")

    # Build lookup from logs by matching approximate timing
    log_by_ts = {}
    for entry in deviation_logs:
        log_by_ts[entry.get("ts", "")] = entry

    lines.append("---")
    lines.append("")

    # Per-category results
    summary = {}
    for cat_key, cat_data in TEST_CATEGORIES.items():
        cat_results = results.get(cat_key, [])
        fire_count = sum(1 for r in cat_results if r.get("fired"))

        lines.append(f"## {cat_data['label']}")
        lines.append("")

        for r in cat_results:
            status = "FIRED" if r.get("fired") else "no fire"
            pattern = r.get("pattern_class", "—")
            entropy = r.get("entropy", "—")
            lines.append(f"- **Input:** {r['input']}")
            lines.append(f"  - Status: {status}")
            if r.get("fired"):
                lines.append(f"  - Pattern class: {pattern}")
                lines.append(f"  - Entropy: {entropy}")
            lines.append("")

        # Expected vs actual
        expected = cat_data["expected_fires"]
        if expected is True:
            if fire_count == 0:
                lines.append(f"**Result: {fire_count} fires — FALSE NEGATIVE (expected fires)**")
            else:
                lines.append(f"**Result: {fire_count} fires — OK**")
        elif expected is False:
            if fire_count > 0:
                lines.append(f"**Result: {fire_count} fires — FALSE POSITIVE (expected 0)**")
            else:
                lines.append(f"**Result: {fire_count} fires — OK**")
        else:
            lines.append(f"**Result: {fire_count} fires — documented (no pass/fail)**")

        lines.append("")
        lines.append("---")
        lines.append("")

        summary[cat_key] = {"label": cat_data["label"], "fires": fire_count, "expected": expected}

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Fires | Expected | Status |")
    lines.append("|---|---|---|---|")
    for cat_key, s in summary.items():
        expected_str = "fires" if s["expected"] is True else ("0" if s["expected"] is False else "n/a")
        if s["expected"] is True:
            status = "OK" if s["fires"] > 0 else "FALSE NEGATIVE"
        elif s["expected"] is False:
            status = "OK" if s["fires"] == 0 else "FALSE POSITIVE"
        else:
            status = "documented"
        lines.append(f"| {s['label'][:50]} | {s['fires']} | {expected_str} | {status} |")

    lines.append("")

    # Entropy ordering check (Cat 3 should have higher entropy than Cat 2)
    cat2_entropies = [r.get("entropy", 0) for r in results.get("2_new_opinions", []) if r.get("fired") and isinstance(r.get("entropy"), (int, float))]
    cat3_entropies = [r.get("entropy", 0) for r in results.get("3_reversals", []) if r.get("fired") and isinstance(r.get("entropy"), (int, float))]

    if cat2_entropies and cat3_entropies:
        avg_cat2 = sum(cat2_entropies) / len(cat2_entropies)
        avg_cat3 = sum(cat3_entropies) / len(cat3_entropies)
        lines.append(f"**Entropy ordering:** Cat 2 avg = {avg_cat2:.4f}, Cat 3 avg = {avg_cat3:.4f}")
        if avg_cat3 > avg_cat2:
            lines.append("Entropy ordering correct (Cat 3 > Cat 2)")
        else:
            lines.append("**WARNING: Entropy ordering wrong (Cat 3 should be > Cat 2)**")
    else:
        lines.append("**Entropy ordering:** insufficient data (not enough fires to compare)")

    lines.append("")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print()
    print("Deviation Detection Test Harness")
    print("================================")
    print()

    # Preflight
    if not _check_ollama():
        print("ERROR: Ollama is not running. Start Ollama and try again.")
        sys.exit(1)

    if not _check_api():
        print("ERROR: Ember-2 API is not running at http://localhost:8000.")
        print("Start it with: ./start_api.bat (Windows) or ./start_api.sh (Mac/Linux)")
        sys.exit(1)

    print("[1/5] Preflight checks passed")
    print()

    # Save and set env
    original_detection = os.environ.get("EMBER_DEVIATION_DETECTION")
    os.environ["EMBER_DEVIATION_DETECTION"] = "true"
    print("[2/5] Enabled EMBER_DEVIATION_DETECTION=true")

    # Seed lodestone records
    print()
    print("[3/5] Seeding lodestone positions...")
    existing_ids = _temporarily_unconfirm_existing()
    seeded_ids = _seed_lodestone_records()

    # Run test turns
    print()
    print("[4/5] Running test conversation turns...")
    print()

    start_time = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    session_id = f"deviation-test-{start_time}"

    results: dict[str, list[dict]] = {}

    for cat_key, cat_data in TEST_CATEGORIES.items():
        print(f"  {cat_data['label']}")
        cat_results = []

        for turn_text in cat_data["turns"]:
            print(f"    Sending: {turn_text[:60]}...")
            api_result = _send_turn(turn_text, session_id)

            if not api_result["success"]:
                print(f"    ERROR: {api_result.get('error', 'unknown')}")
                cat_results.append({
                    "input": turn_text,
                    "fired": False,
                    "error": api_result.get("error"),
                })
                continue

            # Wait for background deviation detection to complete
            time.sleep(5)

            # Check if a deviation record was written for this turn
            recent_records = _read_deviation_records_since(start_time)
            recent_logs = _read_deviation_logs_since(start_time)

            # Match by looking at friction_context in metadata
            fired = False
            pattern_class = None
            entropy = None

            for rec in recent_records:
                meta = rec.get("metadata", {})
                if turn_text[:100] in meta.get("friction_context", ""):
                    fired = True
                    pattern_class = meta.get("pattern_class")
                    entropy = meta.get("entropy_score")
                    break

            # Also check logs for SKIPPED/NO results
            if not fired:
                for log_entry in recent_logs:
                    if log_entry.get("result") in ("YES",):
                        # May have been written but not matched above
                        pass

            status_str = f"FIRED ({pattern_class}, entropy={entropy})" if fired else "no fire"
            print(f"    Result: {status_str}")

            cat_results.append({
                "input": turn_text,
                "fired": fired,
                "pattern_class": pattern_class,
                "entropy": entropy,
                "reply": api_result.get("reply", "")[:100],
            })

        results[cat_key] = cat_results
        print()

    # Audit phase
    print("[5/5] Generating report...")
    print()

    all_deviation_records = _read_deviation_records_since(start_time)
    all_deviation_logs = _read_deviation_logs_since(start_time)

    report = _generate_report(results, all_deviation_records, all_deviation_logs, start_time)

    # Print to terminal
    print(report)

    # Write to file
    report_dir = Path(__file__).resolve().parents[1] / "docs" / "test-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "deviation-detection-report.md"
    report_file.write_text(report, encoding="utf-8")
    print(f"\nReport written to: {report_file}")

    # Cleanup
    print()
    print("Cleanup...")
    _cleanup_lodestone_records(seeded_ids)
    if existing_ids:
        _restore_existing(existing_ids)

    # Restore env
    if original_detection is None:
        os.environ.pop("EMBER_DEVIATION_DETECTION", None)
    else:
        os.environ["EMBER_DEVIATION_DETECTION"] = original_detection
    print("  Restored EMBER_DEVIATION_DETECTION")

    print()
    print(f"Deviation records from this session are preserved in the vault.")
    print(f"Review them at: GET /v1/deviations")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
