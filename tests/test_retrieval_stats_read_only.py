"""
tests/test_retrieval_stats_read_only.py

Read-only coverage for every retrieval-stat write path, not just the one
endpoint (#206, #227).

tests/test_debug_context_read_only.py proves the per-call flag works. This
file covers what the flag cannot: a caller that never passes it. Phase 1 of
the measurement-first audit replays thousands of scoring passes, and a
replay that promotes the records it surfaces is not a measurement of
retrieval, it is a rewrite of the tiering inputs with a report attached.

Same discipline as the #206 suite: every "nothing was written" assertion is
paired with a control over the same corpus asserting that the unguarded path
DOES write. An empty vault or a broken retriever would otherwise make the
whole file pass vacuously.
"""

import hashlib
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.context.service import ContextService
from src.core.config import get_private_vault_path
from src.memory.write_memory import write_memory
from src.retrieval.retrieval_stats import (
    ENV_FLAG,
    retrieval_stats_disabled,
    retrieval_stats_disabled_now,
)

EMBED_DIM = 768
QUERY = "what have i been reading about lately"

# Enough passes to make an accumulating write impossible to miss: the
# frequency term is decay-then-increment, so a single unguarded query moves
# the digest and a hundred of them move it a hundred times.
REPLAY_QUERIES = [
    f"what have i been reading about topic {i % 7} lately, pass {i}"
    for i in range(100)
]


def _memory_db() -> Path:
    return get_private_vault_path() / "embeddings" / "memory.db"


def _stats_digest() -> str:
    """Digest of every column a delivery is capable of changing."""
    path = _memory_db()
    if not path.exists():
        return "no-database"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, last_retrieved_at, frequency_score, tier, heat_score, "
            "retrieval_count FROM vectors ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


@pytest.fixture
def seeded_vault():
    vector = [0.1] * EMBED_DIM
    with patch("src.memory.write_memory.embed_text", return_value=vector):
        for i in range(6):
            write_memory(
                text=(
                    f"a substantive conversation turn about topic {i} with enough "
                    "length to clear the low-value floor and the short-content filter"
                ),
                memory_type="conversation",
                source="chat",
                metadata={"role": "user", "content_kind": "user_content"},
            )
    yield vector


@pytest.fixture
def stub_query_embedding(seeded_vault):
    with patch("src.retrieval.semantic_search.embed_text", return_value=seeded_vault):
        yield


@pytest.fixture
def clean_env():
    """The flag must not leak between tests in either direction."""
    previous = os.environ.pop(ENV_FLAG, None)
    yield
    if previous is None:
        os.environ.pop(ENV_FLAG, None)
    else:
        os.environ[ENV_FLAG] = previous


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------

def test_writes_are_enabled_by_default(clean_env):
    assert retrieval_stats_disabled_now() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_arms_the_switch(clean_env, value):
    os.environ[ENV_FLAG] = value
    assert retrieval_stats_disabled_now() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_env_flag_is_not_armed_by_a_falsey_value(clean_env, value):
    os.environ[ENV_FLAG] = value
    assert retrieval_stats_disabled_now() is False


def test_env_flag_is_read_per_call_not_cached(clean_env):
    # A harness that sets the variable after import must still be covered.
    assert retrieval_stats_disabled_now() is False
    os.environ[ENV_FLAG] = "1"
    assert retrieval_stats_disabled_now() is True
    del os.environ[ENV_FLAG]
    assert retrieval_stats_disabled_now() is False


def test_context_manager_restores_on_exit(clean_env):
    with retrieval_stats_disabled():
        assert retrieval_stats_disabled_now() is True
    assert retrieval_stats_disabled_now() is False


def test_context_manager_nests(clean_env):
    with retrieval_stats_disabled():
        with retrieval_stats_disabled():
            assert retrieval_stats_disabled_now() is True
        # The inner exit must not re-enable writes inside the outer scope.
        assert retrieval_stats_disabled_now() is True
    assert retrieval_stats_disabled_now() is False


def test_context_manager_restores_after_an_exception(clean_env):
    with pytest.raises(RuntimeError):
        with retrieval_stats_disabled():
            raise RuntimeError("replay blew up mid-run")
    assert retrieval_stats_disabled_now() is False


# ---------------------------------------------------------------------------
# The pair: unguarded writes, guarded does not -- over the same corpus
# ---------------------------------------------------------------------------

def test_unguarded_build_context_writes(stub_query_embedding, clean_env):
    # Non-vacuity guard for everything below.
    service = ContextService()
    before = _stats_digest()

    packet = service.build_context(QUERY)

    assert packet.memory_items, "nothing delivered; the suppression tests would be vacuous"
    assert _stats_digest() != before


def test_env_flag_suppresses_the_write_without_read_only(stub_query_embedding, clean_env):
    """The case the per-call flag cannot cover: a caller that forgot it."""
    service = ContextService()
    service.build_context(QUERY)          # settle any first-delivery write
    before = _stats_digest()

    os.environ[ENV_FLAG] = "1"
    packet = service.build_context(QUERY)  # read_only deliberately NOT passed

    assert packet.memory_items
    assert _stats_digest() == before


def test_context_manager_suppresses_the_write_without_read_only(stub_query_embedding, clean_env):
    service = ContextService()
    service.build_context(QUERY)
    before = _stats_digest()

    with retrieval_stats_disabled():
        packet = service.build_context(QUERY)

    assert packet.memory_items
    assert _stats_digest() == before


def test_suppression_does_not_change_what_is_delivered(stub_query_embedding, clean_env):
    service = ContextService()
    writing = service.build_context(QUERY)
    with retrieval_stats_disabled():
        suppressed = service.build_context(QUERY)

    assert [i.store_id for i in suppressed.memory_items] == [
        i.store_id for i in writing.memory_items
    ]


def test_writes_resume_after_the_scope_closes(stub_query_embedding, clean_env):
    """The failure mode that would be worse than the bug.

    A suppression that leaked would look identical to a correct read-only run
    while quietly disabling tiering's only upward path (ADR-015).
    """
    service = ContextService()
    with retrieval_stats_disabled():
        service.build_context(QUERY)
    before = _stats_digest()

    service.build_context(QUERY)

    assert _stats_digest() != before


# ---------------------------------------------------------------------------
# The store is gated directly, so a caller that bypasses build_context
# entirely is still covered
# ---------------------------------------------------------------------------

def test_store_level_write_is_suppressed(stub_query_embedding, clean_env):
    from src.retrieval.semantic_search import _get_memory_store

    store = _get_memory_store()
    assert store is not None
    ids = [row[0] for row in store._conn.execute("SELECT id FROM vectors LIMIT 3")]
    assert ids, "no rows to write against; this test would be vacuous"

    store.update_retrieval_stats(ids)      # control: this must move the digest
    before = _stats_digest()
    assert before != "no-database"

    with retrieval_stats_disabled():
        store.update_retrieval_stats(ids)

    assert _stats_digest() == before


# ---------------------------------------------------------------------------
# The actual acceptance criterion: a replay leaves the database alone
# ---------------------------------------------------------------------------

def test_hundred_query_replay_leaves_the_database_unchanged(stub_query_embedding, clean_env):
    service = ContextService()
    service.build_context(QUERY)
    before = _stats_digest()

    delivered = 0
    with retrieval_stats_disabled():
        for query in REPLAY_QUERIES:
            packet = service.build_context(query, read_only=True)
            delivered += len(packet.memory_items)

    assert delivered > 0, "the replay delivered nothing; the digest check is vacuous"
    assert _stats_digest() == before


def test_the_same_replay_unguarded_does_change_the_database(stub_query_embedding, clean_env):
    """Control for the test above. Ten passes, not a hundred -- the point is
    that the harness is capable of writing, not how much."""
    service = ContextService()
    service.build_context(QUERY)
    before = _stats_digest()

    for query in REPLAY_QUERIES[:10]:
        service.build_context(query)

    assert _stats_digest() != before


# ---------------------------------------------------------------------------
# Guard against reintroduction
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

# The only callers that answer a real user turn. Everything else builds a
# packet in order to look at retrieval, and must say so.
WRITING_CALLERS = {
    Path("src/api/chat.py"),
    Path("src/api/openai_adapter.py"),
}

SCANNED_DIRS = ("src", "tools", "scripts")


def _call_text(source: str, start: int) -> str:
    """The text of a call starting at the opening paren, paren-balanced."""
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return source[start:]


def _build_context_call_sites():
    sites = []
    for directory in SCANNED_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            source = path.read_text(encoding="utf-8", errors="ignore")
            cursor = 0
            while True:
                found = source.find(".build_context(", cursor)
                if found == -1:
                    break
                paren = found + len(".build_context")
                sites.append((path.relative_to(REPO_ROOT), _call_text(source, paren)))
                cursor = paren
    return sites


def test_every_investigative_build_context_call_is_read_only():
    """A trace replay added later must not be able to forget the flag quietly.

    The process-level switch makes forgetting survivable; this makes it
    visible. Both matter: the switch has to be armed by somebody, and the
    only way to know a new harness armed it is to require the call site to
    declare its intent.
    """
    sites = _build_context_call_sites()
    assert len(sites) >= 5, "the scanner found almost nothing; it has probably broken"

    offenders = [
        str(path)
        for path, call in sites
        if path not in WRITING_CALLERS and "read_only=True" not in call
    ]
    assert not offenders, (
        "build_context called without read_only=True outside the live chat "
        f"path: {offenders}. A caller that inspects retrieval must not write "
        "to it (#206, #227)."
    )


def test_the_live_chat_path_still_writes():
    """The inverse guard. If a future edit made the chat path read-only, the
    test above would still pass and tiering would silently stop."""
    sites = dict(
        (path, call) for path, call in _build_context_call_sites()
        if path in WRITING_CALLERS
    )
    assert set(sites) == WRITING_CALLERS, (
        f"expected a build_context call in each of {WRITING_CALLERS}, found {set(sites)}"
    )
    for path, call in sites.items():
        assert "read_only=True" not in call, (
            f"{path} answers real user turns; making it read-only disables "
            "the only upward path in ADR-015 tiering"
        )
