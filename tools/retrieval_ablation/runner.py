"""
tools/retrieval_ablation/runner.py

Runs the ablation: one subprocess per arm, against the test vault only.

Subprocess per arm is not just tidiness. Module-level state -- the SQLite store
cache, lru_cached clients, the config module's vault globals, the ollama
default client -- persists for the life of a process, so an in-process loop
would let one arm's residue reach the next. A fresh interpreter per arm makes
that structurally impossible rather than merely unlikely.

Snapshot and restore of memory.db and ingested.db around every arm is
mandatory. `ContextService.build_context` calls `_update_retrieval_stats`
unconditionally on the read path, inside a bare `except`, and that writes
`last_retrieved_at` and `frequency_score` (ADR-015 amendment step 4 --
`retrieval_count` is legacy and no longer written) -- the inputs the
tiering job reads. An arm can therefore promote the records it surfaced
and change the next arm's treatment assignment. Fixture ids do not match
any row in this ablation's synthetic corpus, so the writes are still
no-ops here, but the guarantee must not rest on that coincidence --
ContextItem.store_id now carries the real vectors primary key (the
identity mismatch that made this a coincidence rather than a structural
fact is itself fixed), so a fixture id that DID collide with a real row
would now actually be written. Note also that `_recency_score` parses
epoch/ISO/hyphenated timestamps via the shared helper now, not only a
date prefix: restoring the files is still the only thing that separates
arms, since same-day ordering alone no longer fails to parse.

Vault resolution is fail-closed, copied from .claude/hooks/post_commit_eval.py.
There is no real-vault arm and no fallback path to one.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_PATH = REPO_ROOT / ".env"

# The tiering inputs an arm can mutate through the read path.
TIERING_DATABASES = ("memory.db", "ingested.db")


# ---------------------------------------------------------------------------
# Fail-closed vault resolution
# ---------------------------------------------------------------------------

def _read_env_value(key: str) -> str | None:
    """Process env first, then .env. Returns None rather than raising."""
    value = os.environ.get(key)
    if value:
        return value.strip().strip('"').strip("'")
    if not ENV_PATH.exists():
        return None
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, raw = line.partition("=")
            if name.strip() == key:
                return raw.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def resolve_test_vault() -> tuple[Path | None, str | None]:
    """Return (path, None) on success and (None, reason) on failure.

    Every failure is a refusal. There is no branch that falls back to the live
    vault -- CLAUDE.md makes evals test-vault only, and this eval writes
    nothing it would want in a real vault anyway.
    """
    test_vault = _read_env_value("VAULT_PATH_TEST")
    if not test_vault:
        return None, "VAULT_PATH_TEST is not set"

    test_path = Path(test_vault).resolve()
    if not test_path.is_dir():
        # A missing directory would let SqliteVectorStore.__init__ mkdir an
        # empty vault and report a meaningless run.
        return None, "VAULT_PATH_TEST does not point at an existing directory"

    live_vault = _read_env_value("PRIVATE_VAULT_PATH")
    if live_vault and Path(live_vault).resolve() == test_path:
        return None, "VAULT_PATH_TEST resolves to the same path as PRIVATE_VAULT_PATH"

    return test_path, None


# ---------------------------------------------------------------------------
# Snapshot / restore
# ---------------------------------------------------------------------------

def snapshot_databases(vault: Path, into: Path) -> dict[str, str]:
    """Copy the tiering databases aside. Returns name -> sha256 for the report,
    so the run can prove it left them untouched."""
    import hashlib

    into.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name in TIERING_DATABASES:
        source = vault / "embeddings" / name
        if not source.exists():
            continue
        shutil.copy2(source, into / name)
        digests[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    return digests


def restore_databases(vault: Path, frm: Path) -> None:
    for name in TIERING_DATABASES:
        saved = frm / name
        if saved.exists():
            shutil.copy2(saved, vault / "embeddings" / name)


def database_digests(vault: Path) -> dict[str, str]:
    import hashlib

    digests: dict[str, str] = {}
    for name in TIERING_DATABASES:
        source = vault / "embeddings" / name
        if source.exists():
            digests[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    return digests


# ---------------------------------------------------------------------------
# Child: run every stratum for one arm
# ---------------------------------------------------------------------------

def run_arm(arm_name: str) -> dict:
    """Run all strata for one arm. Called in the child process."""
    from .arms import (
        ARMS_BY_NAME,
        measure_lever_attribution,
        residual_quota,
        run_cell,
    )
    from .corpus import STRATA

    arm = ARMS_BY_NAME[arm_name]
    cells: dict[str, dict] = {}
    attribution: dict[str, list[dict]] = {}
    for stratum in STRATA:
        result = run_cell(stratum, arm)
        cells[stratum.name] = {
            "delivered": [
                {"id": d.id, "score": d.score, "memory_type": d.memory_type, "tier": d.tier}
                for d in result.delivered
            ],
            "ranked": [
                {"id": d.id, "score": d.score, "memory_type": d.memory_type, "tier": d.tier}
                for d in result.ranked
            ],
            "residual_quota": residual_quota(stratum, arm, result.delivered),
        }
        # Attribution is a property of the corpus against the FULL pipeline, not
        # of this arm, so it is measured once -- in the reference arm's child,
        # where A0_FULL is already the thing being run.
        if arm_name == "A0_FULL":
            attribution[stratum.name] = [
                {k: v for k, v in row.items() if k != "swings"}
                | {"swings": row["swings"]}
                for row in measure_lever_attribution(stratum)
            ]
    return {
        "arm": arm_name,
        "label": arm.label,
        "cells": cells,
        "attribution": attribution,
    }


# ---------------------------------------------------------------------------
# Parent: orchestrate
# ---------------------------------------------------------------------------

def run_all_arms(vault: Path, verbose: bool = False) -> dict:
    from .arms import ARMS

    results: dict[str, dict] = {}
    before = database_digests(vault)

    with tempfile.TemporaryDirectory(prefix="ablation_snap_") as tmp:
        snapshot_dir = Path(tmp)
        snapshot_databases(vault, snapshot_dir)

        for arm in ARMS:
            if verbose:
                print(f"  running {arm.name} ...", flush=True)
            child_env = {
                **os.environ,
                "PRIVATE_VAULT_PATH": str(vault),
                # Deterministic id hashing in any library that uses it; the
                # corpus uses explicit ids, but a stray hash-ordered set
                # elsewhere should not vary between arms.
                "PYTHONHASHSEED": "0",
            }
            completed = subprocess.run(
                [sys.executable, "-m", "tools.retrieval_ablation.runner", "--child", arm.name],
                capture_output=True,
                text=True,
                env=child_env,
                cwd=str(REPO_ROOT),
                timeout=600,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"arm {arm.name} failed (exit {completed.returncode}):\n"
                    f"{completed.stderr[-2000:]}"
                )
            results[arm.name] = json.loads(completed.stdout)

            # Put the tiering inputs back before the next arm sees them.
            restore_databases(vault, snapshot_dir)

    after = database_digests(vault)
    return {
        "arms": results,
        "vault": str(vault),
        "databases_unchanged": before == after,
        "database_digests_before": before,
        "database_digests_after": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", metavar="ARM", help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.child:
        json.dump(run_arm(args.child), sys.stdout)
        return 0

    vault, reason = resolve_test_vault()
    if vault is None:
        print(
            f"REFUSING to run the retrieval ablation: {reason}.\n"
            "This eval is test-vault only (CLAUDE.md). Set VAULT_PATH_TEST to a "
            "real directory that is not PRIVATE_VAULT_PATH.",
            file=sys.stderr,
        )
        return 1

    print(f"Retrieval-architecture ablation. Vault: {vault}")
    raw = run_all_arms(vault, verbose=args.verbose)

    from .report import render_report, write_report

    text, payload = render_report(raw)
    print(text)
    log_path, json_path = write_report(text, payload, REPO_ROOT)
    print(f"\nLog written to:  {log_path}")
    print(f"JSON written to: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
