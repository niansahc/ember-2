"""
tests/test_guard_counters.py

Coverage for the guard hit counters.

The counters exist to find semantically dead configuration: guards that
execute, evaluate their predicate, and are never true on real data. Three
properties have to hold or the measurement is worthless or harmful.

1. THEY CANNOT CHANGE SCORING. Verified by comparing packets and scores
   with recording on and off, not by inspection.
2. THEY COUNT LIVE TRAFFIC ONLY. The suite itself is the wrong population,
   so the scope refuses to open under pytest -- which means these tests
   drive the recorder directly rather than through build_context, and one
   test asserts that a normal build records nothing.
3. THEY DO NOT REACH THE VAULT. The writer refuses a path inside it.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule). Counter data is
site names and integers, so nothing here can carry vault content anyway.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import ContextRanker
from src.context.service import ContextService
from src.observability import guard_counters as gc


@pytest.fixture(autouse=True)
def _close_cached_connections():
    """Connections are cached by path for speed; tests must not share them."""
    gc.close_connections()
    yield
    gc.close_connections()


@pytest.fixture
def counter_db(tmp_path, monkeypatch):
    path = tmp_path / "counters" / "guard_counters.db"
    monkeypatch.setenv(gc.ENV_DB_PATH, str(path))
    yield path


@pytest.fixture
def recording(counter_db):
    """Open a scope despite pytest, so the recorder itself can be tested.

    The pytest guard is the feature under test elsewhere; here it has to be
    stepped around deliberately, which is why this fixture is explicit
    rather than an autouse convenience.
    """
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording(enabled=True) as active:
            assert active
            yield
    # The scope flushes on exit.


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------

def test_count_returns_its_argument_unchanged(recording):
    """The passthrough is what makes instrumentation behaviour-preserving."""
    assert gc.count("site.a", True) is True
    assert gc.count("site.a", False) is False


def test_evaluations_and_firings_are_counted_separately(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            for fired in (True, False, False, False):
                gc.count("site.a", fired)

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert rows["site.a"]["evaluations"] == 4
    assert rows["site.a"]["firings"] == 1


def test_a_guard_evaluated_but_never_true_is_distinguishable_from_unreached(counter_db):
    """The central distinction. Both report zero firings and they are not
    the same finding: one is a rule that never matched, the other is code
    that never ran."""
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            for _ in range(10):
                gc.count("site.never_true", False)

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert rows["site.never_true"]["evaluations"] == 10
    assert rows["site.never_true"]["firings"] == 0
    assert "site.not_reached" not in rows


def test_branches_record_the_arm_taken_against_the_parent_total(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            for which in ("cold", "cold", "hot"):
                assert gc.branch("tier", which) == which

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert rows["tier"]["evaluations"] == 3
    assert rows["tier=cold"]["firings"] == 2
    assert rows["tier=hot"]["firings"] == 1
    # A branch's rate is against its parent's evaluations, not its own.
    assert rows["tier=cold"]["denominator"] == 3
    assert "tier=warm" not in rows


def test_reached_records_an_evaluation_with_no_firing(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.reached("site.fallthrough")

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert rows["site.fallthrough"]["evaluations"] == 1
    assert rows["site.fallthrough"]["firings"] == 0


def test_nothing_is_recorded_outside_a_scope(counter_db):
    gc.count("site.orphan", True)
    gc.branch("site.orphan_branch", "x")
    assert gc.read_all(counter_db) == []


def test_nested_scopes_do_not_double_count(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            with gc.recording() as inner:
                assert inner is False
                gc.count("site.a", True)

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert rows["site.a"]["evaluations"] == 1


def test_a_disabled_scope_records_nothing(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording(enabled=False) as active:
            assert active is False
            gc.count("site.a", True)
    assert gc.read_all(counter_db) == []


def test_the_env_switch_turns_recording_off(counter_db, monkeypatch):
    monkeypatch.setenv(gc.ENV_DISABLE, "1")
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording() as active:
            assert active is False
            gc.count("site.a", True)
    assert gc.read_all(counter_db) == []


def test_a_flush_failure_does_not_escape(counter_db):
    """Instrumentation must degrade to no counting, never to a broken turn."""
    with patch.object(gc, "_under_pytest", return_value=False):
        with patch.object(gc, "flush", side_effect=OSError("disk gone")):
            with gc.recording():
                gc.count("site.a", True)
    # No exception, and the scope closed cleanly.
    assert gc.recording_enabled() is False


# ---------------------------------------------------------------------------
# Live traffic only
# ---------------------------------------------------------------------------

def test_the_scope_refuses_to_open_under_pytest(counter_db):
    """Test fixtures exercise guards production never does."""
    with gc.recording(enabled=True) as active:
        assert active is False
        gc.count("site.a", True)
    assert gc.read_all(counter_db) == []


def test_a_build_context_in_the_suite_records_nothing(counter_db, seeded_vault):
    """End to end: the suite must not be able to pollute the population."""
    service = ContextService()
    with patch("src.retrieval.semantic_search.embed_text", return_value=seeded_vault):
        service.build_context("what have i been reading about lately")
    assert gc.read_all(counter_db) == []


# ---------------------------------------------------------------------------
# No effect on scoring
# ---------------------------------------------------------------------------

EMBED_DIM = 768
QUERY = "what have i been reading about lately"


@pytest.fixture
def seeded_vault():
    from src.memory.write_memory import write_memory

    vector = [0.1] * EMBED_DIM
    rows = [
        ("today i was reading about retrieval scoring and it clarified a lot",
         "conversation", {"role": "user", "content_kind": "experience"}),
        ("assistant: here is a summary of what you have been reading lately",
         "conversation", {"role": "assistant", "content_kind": "answer"}),
        ("a journal entry about working through the reading list this week",
         "journal", {"role": "user", "content_kind": "user_content"}),
        ("the user prefers dense technical reading over summaries",
         "profile", {"content_kind": "user_content"}),
    ]
    with patch("src.memory.write_memory.embed_text", return_value=vector):
        for text, memory_type, metadata in rows:
            write_memory(text=text, memory_type=memory_type, source="chat",
                         metadata=metadata)
    yield vector


def _packet_fingerprint(packet):
    return [
        (item.store_id, item.memory_type, round(float(item.score), 12))
        for item in packet.memory_items
    ] + [
        ("refl", item.store_id, round(float(item.score), 12))
        for item in packet.reflection_items
    ]


def test_recording_does_not_change_the_packet(counter_db, seeded_vault):
    """Verified by comparison, not by reading the diff and hoping."""
    service = ContextService()
    with patch("src.retrieval.semantic_search.embed_text", return_value=seeded_vault):
        # Like for like: both runs take the same path through build_context,
        # differing only in whether the recorder is live. Comparing a
        # read_only run against a writing one would confound the counters
        # with the stats write.
        with patch.object(gc, "_under_pytest", return_value=False):
            with_counting = _packet_fingerprint(service.build_context(QUERY))
            with patch.object(gc, "recording_enabled", return_value=False),                  patch.object(gc, "count", side_effect=lambda site, fired: fired),                  patch.object(gc, "branch", side_effect=lambda site, which: which):
                without = _packet_fingerprint(service.build_context(QUERY))

    assert with_counting == without
    assert without, "nothing delivered; the comparison would be vacuous"


def test_recording_does_not_change_ranker_scores(counter_db):
    ranker = ContextRanker()
    policy = ContextPolicy(name="reflective", memory_weight=0.7, recency_bias=0.2,
                           prefer_experiences=True)

    def _items():
        return [
            ContextItem(
                id=f"i{n}", store_id=f"i{n}",
                content="today i noticed something worth remembering about the work",
                source="chat", item_type="conversation", memory_type="conversation",
                score=0.4 + n / 100, timestamp="2026-09-01T12-00-00", tags=[],
                metadata={"role": "user", "content_kind": "experience"},
                tier="cold" if n % 2 else "hot",
            )
            for n in range(5)
        ]

    with patch.object(gc, "_under_pytest", return_value=True):
        plain = ranker.apply_policy(_items(), policy)
        plain_scores = [round(i.score, 12) for i in ranker.rank(plain, [])[0]]

    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            counted = ranker.apply_policy(_items(), policy)
            counted_scores = [round(i.score, 12) for i in ranker.rank(counted, [])[0]]

    assert counted_scores == plain_scores


def test_count_is_a_pure_passthrough_over_both_outcomes():
    """The property the no-effect guarantee rests on, stated directly."""
    for value in (True, False):
        assert gc.count("site.x", value) == value
    assert gc.branch("site.y", "arm") == "arm"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def test_counts_accumulate_across_scopes_and_restarts(counter_db):
    """Persistence: a second process must add to the first, not replace it."""
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)
        with gc.recording():
            gc.count("site.a", True)
            gc.count("site.a", False)

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert rows["site.a"]["evaluations"] == 3
    assert rows["site.a"]["firings"] == 2


def test_counters_are_readable_while_a_writer_holds_the_database(counter_db):
    """Readable live: the reader opens read-only and never blocks the API."""
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)

    writer = sqlite3.connect(str(counter_db))
    try:
        writer.execute("BEGIN")
        writer.execute(
            "UPDATE guard_counters SET evaluations = evaluations WHERE site = 'site.a'"
        )
        rows = gc.read_all(counter_db)
        assert rows[0]["site"] == "site.a"
    finally:
        writer.rollback()
        writer.close()


def test_reading_a_database_that_does_not_exist_yet_is_empty(tmp_path):
    assert gc.read_all(tmp_path / "absent.db") == []


def test_reset_clears_the_window(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)
    assert gc.read_all(counter_db)
    gc.reset(counter_db)
    assert gc.read_all(counter_db) == []


def test_writing_inside_the_vault_is_refused(tmp_path):
    from src.core.config import get_private_vault_path

    vault = get_private_vault_path()
    with pytest.raises(ValueError, match="inside the vault"):
        gc.flush({"site.a": ["predicate", None, 1, 1]}, vault / "counters.db")


def test_the_default_path_is_outside_the_vault():
    from src.core.config import get_private_vault_path

    default = gc.DEFAULT_DB_PATH.resolve()
    vault = get_private_vault_path().resolve()
    assert vault != default and vault not in default.parents


def test_the_connection_is_cached_rather_than_reopened(counter_db):
    """Reopening per turn measured at ~29ms; caching brings it under 1ms."""
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)
        first = gc._connect(counter_db)
        with gc.recording():
            gc.count("site.a", True)
        assert gc._connect(counter_db) is first

    gc.close_connections()
    assert gc._connect(counter_db) is not first


def test_wal_mode_is_enabled_so_readers_never_block(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)
    conn = gc._connect(counter_db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_a_reset_leaves_the_database_usable(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)
        gc.reset(counter_db)
        with gc.recording():
            gc.count("site.b", True)

    rows = {row["site"]: row for row in gc.read_all(counter_db)}
    assert set(rows) == {"site.b"}


def test_first_and_last_seen_are_recorded(counter_db):
    with patch.object(gc, "_under_pytest", return_value=False):
        with gc.recording():
            gc.count("site.a", True)
    row = gc.read_all(counter_db)[0]
    assert row["first_seen"]
    assert row["last_seen"]


# ---------------------------------------------------------------------------
# Inventory: the instrumented sites are actually wired up
# ---------------------------------------------------------------------------

INSTRUMENTED_MODULES = (
    "src/retrieval/semantic_search.py",
    "src/retrieval/vector_index.py",
    "src/context/service.py",
    "src/context/ranker.py",
    "src/context/retriever.py",
    "src/context/low_value.py",
)

# The sites the brief names as the minimum coverage, by prefix.
REQUIRED_PREFIXES = (
    "semantic_search.min_score_floor.",
    "vector_index.min_score_floor.",
    "should_exclude_result.",
    "type_gate.",
    "relevance_gate.",
    "echo_filter.",
    "low_value_filter.",
    "low_value.",
    "ranker.authorship.",
    "ranker.decay.no_decay_type",
    "ranker.tier",
    "reserved_slots.",
    "diversity.",
    "profile.",
)


def _declared_sites() -> set[str]:
    import re

    repo_root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r'(?:count|branch|reached)\(\s*f?"([^"]+)"')
    sites: set[str] = set()
    for relative in INSTRUMENTED_MODULES:
        source = (repo_root / relative).read_text(encoding="utf-8")
        sites.update(pattern.findall(source))
    return sites


def test_every_required_guard_family_is_instrumented():
    sites = _declared_sites()
    missing = [
        prefix
        for prefix in REQUIRED_PREFIXES
        if not any(site.startswith(prefix) for site in sites)
    ]
    assert not missing, f"guard families with no counter: {missing}"


def test_the_inventory_is_not_trivially_small():
    """A scanner that quietly matched nothing would pass every check above."""
    assert len(_declared_sites()) >= 40


def test_site_names_are_unique_per_call_site():
    """Two sites sharing a name would silently pool two different guards."""
    import re

    repo_root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r'(?:count|reached)\(\s*"([^"]+)"')
    seen: dict[str, int] = {}
    for relative in INSTRUMENTED_MODULES:
        source = (repo_root / relative).read_text(encoding="utf-8")
        for site in pattern.findall(source):
            seen[site] = seen.get(site, 0) + 1
    duplicates = {site: n for site, n in seen.items() if n > 1}
    assert not duplicates, f"duplicate counter site names: {duplicates}"
