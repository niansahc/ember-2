"""
tests/test_traffic_window.py

Coverage for the declared-site inventory and the designed traffic window.

The counters can only report on sites that exist as rows, and a row
appears the first time a site records something. An unreached guard
therefore leaves no trace at all, which is precisely the finding the
exercise is looking for. Accounting for it needs a declared inventory
extracted from the source, and an inventory that drifts out of step with
the code is worse than none -- it would quietly stop asking about a guard.
So the tests here are mostly about drift: every instrumented module
listed, every dynamic site given an arm domain, every arm domain matching
the code it claims to describe.

The traffic window tests cover the two things that can silently invalidate
a window: a query set that does not route where it was written to route
(#230 lost a policy exactly this way), and a run pointed at the personal
vault.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import guard_counter_sites as sites  # noqa: E402
import traffic_window as window  # noqa: E402

from src.context.policies import classify_query  # noqa: E402
from src.context.ranker import ContextRanker  # noqa: E402
from src.observability.guard_counters import (  # noqa: E402
    KIND_BRANCH,
    KIND_PARENT,
    KIND_PREDICATE,
)


# ---------------------------------------------------------------------------
# The declared inventory
# ---------------------------------------------------------------------------

class TestDeclaredInventory:
    def test_every_module_that_counts_is_listed(self):
        """A new instrumented module must join the inventory with the code.

        Without this the inventory silently stops covering a file, and the
        guards in it become invisible rather than reported as unreached.
        """
        importing = set()
        for path in (REPO_ROOT / "src").rglob("*.py"):
            if "guard_counters" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            if "guard_counters import" in text or "import guard_counters" in text:
                importing.add(path.relative_to(REPO_ROOT).as_posix())

        listed = set(sites.INSTRUMENTED_MODULES)
        # src/observability/__init__.py re-exports; it counts nothing itself.
        importing = {
            module for module in importing
            if not module.endswith("observability/__init__.py")
        }
        assert importing == listed

    def test_every_call_site_produces_at_least_one_declared_row(self):
        declared = {row["site"] for row in sites.declared_sites()}
        for call in sites.call_sites():
            name = call["name"]
            if "\0" in name:
                prefix = name.split("\0")[0].rstrip(".")
                matches = [site for site in declared if site.startswith(prefix + ".")]
            else:
                matches = [
                    site for site in declared
                    if site == name or site.startswith(name + "=")
                ]
            assert matches, f"{call['module']}:{call['line']} declares nothing"

    def test_declared_rows_are_unique_and_well_formed(self):
        declared = sites.declared_sites()
        names = [row["site"] for row in declared]
        assert len(names) == len(set(names))
        for row in declared:
            assert row["site"].strip()
            assert row["kind"] in {KIND_PREDICATE, KIND_PARENT, KIND_BRANCH}
            # Only a branch arm has a parent, and its parent must exist.
            if row["kind"] == KIND_BRANCH:
                assert row["parent"] in set(names)
            else:
                assert row["parent"] is None

    def test_decay_bucket_arms_come_from_the_ladders(self):
        """The arm domain is the ladder, not a copy of it.

        A bucket added to _EPHEMERAL_DECAY should appear in the inventory
        without anyone remembering to add it here.
        """
        declared = {row["site"] for row in sites.declared_sites()}
        for family, tiers in (
            ("reflection", ContextRanker._REFLECTION_DECAY),
            ("ephemeral", ContextRanker._EPHEMERAL_DECAY),
            ("default", ContextRanker._DEFAULT_DECAY),
        ):
            for max_age, _weight in tiers:
                arm = "older" if max_age is None else f"d{max_age}"
                assert f"ranker.decay.bucket.{family}={arm}" in declared

    def test_a_dynamic_site_without_an_arm_domain_is_refused(self, monkeypatch):
        """Silence is the failure mode to avoid.

        An undeclared dynamic site would otherwise contribute no rows and
        read as fully accounted for.
        """
        original = sites._dynamic_arms

        def _missing_one():
            arms = dict(original())
            arms.pop("ranker.decay.family")
            return arms

        monkeypatch.setattr(sites, "_dynamic_arms", _missing_one)
        with pytest.raises(KeyError, match="ranker.decay.family"):
            sites.declared_sites()

    def test_unreached_sites_are_reported_not_omitted(self):
        """The reader must turn absence into a row, not into nothing."""
        import guard_counters as reader

        recorded = [
            {
                "site": "type_gate.profile_bypass",
                "kind": KIND_PREDICATE,
                "parent": None,
                "evaluations": 12,
                "firings": 3,
                "denominator": 12,
                "first_seen": "2026-09-23T00-00-00",
                "last_seen": "2026-09-23T00-00-01",
            }
        ]
        rows = reader._with_declared(recorded)
        by_site = {row["site"]: row for row in rows}
        assert len(rows) == len(sites.declared_sites())
        assert by_site["type_gate.profile_bypass"]["evaluations"] == 12
        # Everything else is present at zero rather than missing.
        untouched = by_site["ranker.decay.ladder_fell_through"]
        assert untouched["evaluations"] == 0
        assert untouched["firings"] == 0


class TestClassification:
    """A branch parent is not a guard that never fires."""

    ROWS = [
        {"site": "ranker.tier", "kind": KIND_PARENT, "parent": None,
         "evaluations": 40, "firings": 0, "denominator": 40},
        {"site": "ranker.tier=cold", "kind": KIND_BRANCH, "parent": "ranker.tier",
         "evaluations": 0, "firings": 40, "denominator": 40},
        {"site": "ranker.tier=warm", "kind": KIND_BRANCH, "parent": "ranker.tier",
         "evaluations": 0, "firings": 0, "denominator": 40},
        {"site": "echo_filter.meta_marker", "kind": KIND_PREDICATE, "parent": None,
         "evaluations": 40, "firings": 0, "denominator": 40},
        {"site": "ranker.project.match", "kind": KIND_PREDICATE, "parent": None,
         "evaluations": 0, "firings": 0, "denominator": 0},
    ]

    def _reader(self):
        import guard_counters as reader

        return reader

    def test_a_branch_parent_is_not_reported_as_dead(self):
        dead = {row["site"] for row in self._reader().dead_rows(self.ROWS)}
        assert "ranker.tier" not in dead
        # The arm that was never taken is the real finding.
        assert dead == {"ranker.tier=warm", "echo_filter.meta_marker"}

    def test_unreached_is_about_evaluations_not_firings(self):
        unreached = {row["site"] for row in self._reader().unreached_rows(self.ROWS)}
        assert unreached == {"ranker.project.match"}

    def test_an_arm_taken_every_time_never_discriminates(self):
        always = {row["site"] for row in self._reader().always_rows(self.ROWS)}
        assert always == {"ranker.tier=cold"}

    def test_the_three_classes_do_not_overlap(self):
        reader = self._reader()
        dead = {row["site"] for row in reader.dead_rows(self.ROWS)}
        unreached = {row["site"] for row in reader.unreached_rows(self.ROWS)}
        always = {row["site"] for row in reader.always_rows(self.ROWS)}
        assert not dead & unreached
        assert not dead & always
        assert not unreached & always


# ---------------------------------------------------------------------------
# The designed query set
# ---------------------------------------------------------------------------

class TestQuerySet:
    POLICIES = {
        "task_status", "status_state", "reflective", "factual_recall",
        "recent_activity", "recent", "activity", "default", "web_search",
        "clarification",
    }

    def test_the_set_covers_every_policy(self):
        """Routing, not intent.

        A query written for one policy is routinely claimed by an earlier
        branch of the cascade, and a window run on an uncovered set cannot
        say anything about the guards behind the missing branch.
        """
        assigned = {classify_query(entry["query"]).name for entry in window.QUERY_SET}
        assert self.POLICIES <= assigned, self.POLICIES - assigned

    def test_every_query_declares_what_it_targets(self):
        for entry in window.QUERY_SET:
            assert entry["query"].strip()
            assert entry["targets"], entry["query"]

    def test_queries_are_distinct(self):
        queries = [entry["query"] for entry in window.QUERY_SET]
        assert len(queries) == len(set(queries))

    def test_the_set_reaches_the_relational_path(self):
        from src.context.policies import _matches_relational_query

        relational = [
            entry for entry in window.QUERY_SET
            if _matches_relational_query(entry["query"])
        ]
        assert relational, "no query reaches the authorship multipliers"


# ---------------------------------------------------------------------------
# The run guard
# ---------------------------------------------------------------------------

class TestRunRefusesThePersonalVault:
    def _run_against(self, monkeypatch, active: str, personal: Path | None) -> int:
        monkeypatch.setattr(
            window, "_get", lambda *a, **k: {"active_vault": active, "label": "x"}
        )
        monkeypatch.setattr(
            window, "_personal_vault_from_env_file", lambda: personal
        )

        def _refuse_post(*args, **kwargs):
            raise AssertionError("a refused window must send no turns")

        monkeypatch.setattr(window, "_post", _refuse_post)
        return window.cmd_run("http://127.0.0.1:8000", "ember", 1.0)

    def test_refuses_when_the_active_vault_is_the_personal_one(self, monkeypatch, tmp_path):
        personal = tmp_path / "personal"
        personal.mkdir()
        assert self._run_against(monkeypatch, str(personal), personal) == 2

    def test_refuses_when_the_personal_vault_cannot_be_identified(self, monkeypatch, tmp_path):
        """Unknown is not the same as safe."""
        assert self._run_against(monkeypatch, str(tmp_path / "somewhere"), None) == 2

    def test_reads_the_personal_path_from_the_env_file_not_the_environment(
        self, monkeypatch
    ):
        """The driver may itself be run with PRIVATE_VAULT_PATH overridden.

        The sentinel comes from tmp_path rather than a literal, so the
        assertion tests the source of the value and not path syntax: a
        Windows-style literal is a relative path on POSIX and resolves
        somewhere neither side meant.
        """
        sentinel = Path(self.__class__.__name__).resolve() / "nowhere"
        monkeypatch.setenv("PRIVATE_VAULT_PATH", str(sentinel))
        from_file = window._personal_vault_from_env_file()
        assert from_file is None or from_file != sentinel
