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

from src.memory.write_memory import should_skip_memory


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
