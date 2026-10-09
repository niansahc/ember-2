"""
tests/test_write_memory.py

Unit tests for should_skip_memory() in src/memory/write_memory.py.

Covers the filter conditions that silently drop memory writes:
- empty / too-short content
- meta-marker prefixes (prompt scaffolding, JSON keys)
- JSON/list payloads
- code fences

Includes regression tests for the openai_adapter conversation format history:
- old format ("User asked: ...") — filtered by meta-marker
- intermediate format ("User: X\\nAssistant: Y") — was filtered by the
  combined-exchange guard (now removed)
- current format — two separate writes, each passes on its own
"""

import pytest

from src.core.config import vault_binding
from src.memory.write_memory import should_skip_memory
from tests.conftest import memory_db_ids as _row_ids


# ---------------------------------------------------------------------------
# Empty / too short
# ---------------------------------------------------------------------------

def test_empty_string_is_skipped():
    assert should_skip_memory("") is True


def test_whitespace_only_is_skipped():
    assert should_skip_memory("   \n\t  ") is True


def test_too_short_is_skipped():
    assert should_skip_memory("Short text.") is True


def test_39_chars_is_skipped_for_non_conversation():
    # non-journal, non-conversation types require 40 chars minimum
    assert should_skip_memory("a" * 39, memory_type="ingested") is True


def test_short_conversation_turn_is_not_skipped():
    # conversation turns are never skipped for length
    assert should_skip_memory("Yes", memory_type="conversation") is False
    assert should_skip_memory("Go ahead", memory_type="conversation") is False


def test_40_chars_passes_length_check_for_non_journal():
    # guard is len < 40, so exactly 40 passes for non-journal
    assert should_skip_memory("a" * 40, memory_type="conversation") is False


def test_journal_minimum_is_20_chars():
    assert should_skip_memory("a" * 19, memory_type="journal") is True
    assert should_skip_memory("a" * 20, memory_type="journal") is False


def test_journal_39_chars_passes():
    # 39 chars is above the journal minimum of 20
    assert should_skip_memory("a" * 39, memory_type="journal") is False


# ---------------------------------------------------------------------------
# Meta-marker filtering
# ---------------------------------------------------------------------------

def test_user_asked_prefix_is_skipped():
    text = "User asked: Hey E, can you tell me about the Ember-2 project and why it matters?"
    assert should_skip_memory(text) is True


def test_ember_responded_is_skipped():
    text = "Ember responded: Sure! Ember-2 is a local personal intelligence system designed for you."
    assert should_skip_memory(text) is True


def test_assistant_responded_is_skipped():
    text = "Assistant responded: Here is the answer to your question about the project architecture."
    assert should_skip_memory(text) is True


def test_task_marker_is_skipped():
    text = "### Task: Generate 1-3 broad tags categorizing the main themes of this conversation."
    assert should_skip_memory(text) is True


def test_generate_tags_marker_is_skipped():
    text = "Generate 1-3 broad tags for this conversation about memory and retrieval systems."
    assert should_skip_memory(text) is True


def test_json_key_user_message_is_skipped():
    text = '{"user_message": "hello", "response": "hi there, how are you doing today?"}'
    assert should_skip_memory(text) is True


def test_json_key_memory_items_is_skipped():
    text = '{"memory_items": ["item one about work", "item two about health and wellbeing"]}'
    assert should_skip_memory(text) is True


# ---------------------------------------------------------------------------
# JSON / list payloads
# ---------------------------------------------------------------------------

def test_json_object_is_skipped():
    text = '{"key": "value", "another": "something meaningful here for context"}'
    assert should_skip_memory(text) is True


def test_json_array_is_skipped():
    text = '["first item here", "second item here", "third item for the list"]'
    assert should_skip_memory(text) is True


# ---------------------------------------------------------------------------
# Code fences
# ---------------------------------------------------------------------------

def test_code_fence_is_skipped():
    text = "Here is the code:\n```python\ndef hello():\n    print('hello world')\n```"
    assert should_skip_memory(text) is True


# ---------------------------------------------------------------------------
# Regression: openai_adapter conversation format history
# ---------------------------------------------------------------------------

def test_old_adapter_format_is_skipped():
    """Original format hit the 'user asked:' meta-marker and was silently dropped."""
    text = "User asked: What have I been working on today?. Ember responded: Here is a summary of your recent work on Ember-2."
    assert should_skip_memory(text, memory_type="conversation") is True


def test_user_turn_passes():
    """
    Current format: user turn written as a standalone record.
    Should pass all filters and be stored.
    """
    text = "What have I been working on today? I want to review progress on the Ember-2 retrieval pipeline."
    assert should_skip_memory(text, memory_type="conversation") is False


def test_assistant_turn_passes():
    """
    Current format: assistant reply written as a standalone record.
    Should pass all filters and be stored.
    """
    text = "You have been working on the Ember-2 project, specifically the retrieval pipeline and conversation memory write path."
    assert should_skip_memory(text, memory_type="conversation") is False


# ---------------------------------------------------------------------------
# Record write and index are separate (plan v3 section 1, ADR-047).
#
# write_canonical_record() writes the canonical record with no content filter;
# should_index() is the only place the filter applies, and index_record() does
# the embedding and memory.db insert. write_memory() composes the three and
# keeps its old behavior for every other caller.
# ---------------------------------------------------------------------------

CODE_FENCE_REPLY = "Here is the snippet:\n```python\nprint('hi')\n```"
PLAIN_REPLY = "A plain reply with no code in it, long enough to be ordinary."


@pytest.fixture
def bound_vault(tmp_path, monkeypatch):
    """A fresh vault bound for this test only, with embedding stubbed."""
    for sub in ("memory", "embeddings"):
        (tmp_path / sub).mkdir()
    monkeypatch.setattr(
        "src.memory.write_memory.embed_text", lambda _t: [0.0] * 768,
    )
    with vault_binding(tmp_path):
        yield tmp_path


def _conversation_record(text: str, **metadata) -> dict:
    return {
        "id": "2026-01-01T00-00-00-000001",
        "timestamp": "2026-01-01T00-00-00-000001",
        "type": "conversation",
        "text": text,
        "source": "chat",
        "tags": ["conversation"],
        "metadata": {"role": "assistant", **metadata},
    }


def test_should_index_rejects_code_fence_conversation(bound_vault):
    from src.memory.write_memory import should_index

    assert should_index(_conversation_record(CODE_FENCE_REPLY)) is False


def test_should_index_accepts_plain_conversation(bound_vault):
    """Positive control for the code-fence rejection above."""
    from src.memory.write_memory import should_index

    assert should_index(_conversation_record(PLAIN_REPLY)) is True


def test_should_index_rejects_exchange_outcome(bound_vault):
    from src.memory.write_memory import should_index

    record = _conversation_record(PLAIN_REPLY, kind="exchange_outcome")
    record["type"] = "system_event"
    assert should_index(record) is False


def test_should_index_accepts_system_event_without_outcome_kind(bound_vault):
    """Positive control: only the exchange_outcome kind is excluded."""
    from src.memory.write_memory import should_index

    record = _conversation_record(PLAIN_REPLY, kind="audit")
    record["type"] = "system_event"
    assert should_index(record) is True


def test_write_canonical_record_writes_text_the_filter_skips(bound_vault):
    from src.memory.write_memory import write_canonical_record

    record, path = write_canonical_record(
        text=CODE_FENCE_REPLY, memory_type="conversation", source="chat",
        metadata={"role": "assistant"},
    )
    assert path.exists()
    assert record["text"] == CODE_FENCE_REPLY
    assert record["metadata"]["contains_named_third_party"] is False
    # The canonical write never indexes.
    assert _row_ids(bound_vault) == set()


def test_write_memory_still_refuses_text_the_filter_skips(bound_vault):
    """Control: write_memory keeps today's filter on the write (R10)."""
    from src.memory.write_memory import write_memory

    assert write_memory(
        text=CODE_FENCE_REPLY, memory_type="conversation", source="chat",
    ) is None
    assert not list((bound_vault / "memory" / "conversation").glob("*.json"))


def test_index_record_inserts_a_memory_db_row(bound_vault):
    from src.memory.write_memory import index_record, write_canonical_record

    record, path = write_canonical_record(
        text=PLAIN_REPLY, memory_type="conversation", source="chat",
        metadata={"role": "assistant"},
    )
    assert _row_ids(bound_vault) == set()
    index_record(record, path)
    assert _row_ids(bound_vault) == {record["id"]}


def test_write_memory_still_indexes_inline(bound_vault):
    """write_memory keeps its write-then-index behavior and return value."""
    from src.memory.write_memory import write_memory

    path = write_memory(
        text=PLAIN_REPLY, memory_type="conversation", source="chat",
        metadata={"role": "assistant"},
    )
    assert path is not None and path.exists()
    assert _row_ids(bound_vault) == {path.stem}
