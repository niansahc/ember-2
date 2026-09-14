"""
tests/test_retrieval_ablation_runner.py

The runner's job is isolation, and isolation failures are silent by nature: a
run that touched the live vault, or that let one arm's state reach the next,
produces a clean-looking report with meaningless numbers. These tests pin the
refusals and the restoration rather than the arithmetic.

No test here executes a full ablation -- that is minutes of subprocesses. The
subprocess boundary itself is exercised by running one arm in-process, which is
what the child does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.retrieval_ablation import runner


class TestFailsClosedOnVaultResolution:
    """Every failure path must refuse. CLAUDE.md makes evals test-vault only,
    and a fallback to the live vault would be both a privacy breach and a
    meaningless measurement, since the live corpus has no warm records at all.
    """

    def test_refuses_when_test_vault_unset(self, monkeypatch):
        monkeypatch.setattr(runner, "_read_env_value", lambda key: None)
        vault, reason = runner.resolve_test_vault()
        assert vault is None
        assert "not set" in reason

    def test_refuses_when_test_vault_is_not_a_directory(self, monkeypatch, tmp_path):
        missing = tmp_path / "nope"
        monkeypatch.setattr(
            runner,
            "_read_env_value",
            lambda key: str(missing) if key == "VAULT_PATH_TEST" else None,
        )
        vault, reason = runner.resolve_test_vault()
        assert vault is None
        assert "existing directory" in reason

    def test_refuses_when_test_vault_is_the_live_vault(self, monkeypatch, tmp_path):
        """The one that actually matters: both variables set, both pointing at
        the same place. Nothing downstream would notice."""
        monkeypatch.setattr(
            runner, "_read_env_value", lambda key: str(tmp_path)
        )
        vault, reason = runner.resolve_test_vault()
        assert vault is None
        assert "same path as PRIVATE_VAULT_PATH" in reason

    def test_accepts_a_distinct_existing_test_vault(self, monkeypatch, tmp_path):
        """Non-vacuousness: the refusals above must not be refusing everything."""
        test_vault = tmp_path / "test"
        live = tmp_path / "live"
        test_vault.mkdir()
        live.mkdir()
        monkeypatch.setattr(
            runner,
            "_read_env_value",
            lambda key: str(test_vault)
            if key == "VAULT_PATH_TEST"
            else str(live),
        )
        vault, reason = runner.resolve_test_vault()
        assert reason is None
        assert vault == test_vault.resolve()

    def test_main_exits_nonzero_without_building_a_service(self, monkeypatch, capsys):
        monkeypatch.setattr(
            runner, "resolve_test_vault", lambda: (None, "VAULT_PATH_TEST is not set")
        )

        def _explode(*args, **kwargs):
            raise AssertionError("run_all_arms must not be reached on refusal")

        monkeypatch.setattr(runner, "run_all_arms", _explode)
        monkeypatch.setattr("sys.argv", ["runner"])
        assert runner.main() == 1
        assert "REFUSING" in capsys.readouterr().err


class TestTieringDatabasesAreRestored:
    """`_update_retrieval_stats` fires unconditionally on the read path inside a
    bare except and writes last_retrieved_at / retrieval_count -- the tiering
    job's inputs. An arm can therefore promote the records it surfaced and
    change the next arm's treatment. Fixture ids match no row today so the
    writes are no-ops, but the guarantee must not rest on that coincidence.
    """

    @pytest.fixture
    def vault(self, tmp_path):
        embeddings = tmp_path / "embeddings"
        embeddings.mkdir()
        for name in runner.TIERING_DATABASES:
            (embeddings / name).write_bytes(f"original-{name}".encode())
        return tmp_path

    def test_snapshot_then_restore_undoes_a_write(self, vault, tmp_path):
        snapshot_dir = tmp_path / "snap"
        before = runner.snapshot_databases(vault, snapshot_dir)
        assert set(before) == set(runner.TIERING_DATABASES)

        target = vault / "embeddings" / runner.TIERING_DATABASES[0]
        target.write_bytes(b"an arm promoted the records it surfaced")
        assert runner.database_digests(vault) != before

        runner.restore_databases(vault, snapshot_dir)
        assert runner.database_digests(vault) == before

    def test_digests_are_real_sha256_of_the_files(self, vault):
        digests = runner.database_digests(vault)
        for name, digest in digests.items():
            expected = hashlib.sha256(
                (vault / "embeddings" / name).read_bytes()
            ).hexdigest()
            assert digest == expected

    def test_missing_database_is_not_an_error(self, tmp_path):
        """A fresh test vault may not have run the tiering job yet."""
        (tmp_path / "embeddings").mkdir()
        assert runner.snapshot_databases(tmp_path, tmp_path / "snap") == {}
        assert runner.database_digests(tmp_path) == {}


class TestChildArmOutput:
    """The child writes JSON to stdout and the parent parses it, so the contract
    between them is a real interface even though both live in this file."""

    def test_one_arm_round_trips_through_json(self):
        payload = runner.run_arm("A_NAIVE_cosine")
        restored = json.loads(json.dumps(payload))
        assert restored["arm"] == "A_NAIVE_cosine"
        assert restored["cells"], "no strata were run"
        for name, cell in restored["cells"].items():
            assert cell["ranked"], name
            for row in cell["ranked"]:
                assert set(row) == {"id", "score", "memory_type", "tier"}

    def test_reference_arm_carries_attribution_and_others_do_not(self):
        """Attribution is a property of the corpus against the full pipeline, so
        measuring it once in A0_FULL keeps every other arm's child cheap."""
        assert runner.run_arm("A0_FULL")["attribution"]
        assert runner.run_arm("A_NAIVE_cosine")["attribution"] == {}

    def test_an_arm_is_deterministic(self):
        """Two runs of the same arm must agree exactly. Ids are explicit rather
        than hash-derived precisely so this holds across processes."""
        first = runner.run_arm("A_T-off_scoring")["cells"]
        second = runner.run_arm("A_T-off_scoring")["cells"]
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_arms_do_not_all_agree(self):
        """Non-vacuousness for determinism: if every arm returned the same
        thing, the determinism test above would pass while the eval measured
        nothing at all."""
        full = runner.run_arm("A0_FULL")["cells"]
        naive = runner.run_arm("A_NAIVE_cosine")["cells"]
        differing = [
            name for name in full
            if [r["id"] for r in full[name]["ranked"]]
            != [r["id"] for r in naive[name]["ranked"]]
        ]
        assert len(differing) >= 6, differing


class TestEntryPointIsNotWiredToTheHook:
    def test_ablation_flag_is_opt_in(self):
        """tools/eval_retrieval.py runs from the post-commit hook on any commit
        touching src/context, src/retrieval or src/llm. The ablation runs one
        subprocess per arm, so it must never fire from that path."""
        source = Path("tools/eval_retrieval.py").read_text(encoding="utf-8")
        assert '"--ablation" in sys.argv' in source

        hook = Path(".claude/hooks/post_commit_eval.py").read_text(encoding="utf-8")
        assert "--ablation" not in hook
