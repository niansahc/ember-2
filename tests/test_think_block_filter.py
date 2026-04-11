"""
tests/test_think_block_filter.py

Tests for <think> block stripping from qwen3 model output.
Covers both the batch strip_think_blocks() function (used in
non-streaming path) and the streaming ThinkBlockFilter class.
"""

from src.llm.adapter import strip_think_blocks
from src.api.openai_adapter import ThinkBlockFilter


class TestStripThinkBlocks:
    """Batch stripping of <think> blocks from complete text."""

    def test_strips_single_think_block(self):
        text = "<think>Let me reason about this.</think>Here is my answer."
        assert strip_think_blocks(text) == "Here is my answer."

    def test_strips_multiline_think_block(self):
        text = (
            "<think>\nStep 1: Consider the problem.\n"
            "Step 2: Form a response.\n</think>\n"
            "The answer is 42."
        )
        assert strip_think_blocks(text) == "The answer is 42."

    def test_strips_multiple_think_blocks(self):
        text = "<think>First thought.</think>Part one. <think>Second thought.</think>Part two."
        assert strip_think_blocks(text) == "Part one. Part two."

    def test_no_think_blocks_returns_unchanged(self):
        text = "Just a normal response with no thinking."
        assert strip_think_blocks(text) == text

    def test_empty_string(self):
        assert strip_think_blocks("") == ""

    def test_only_think_block(self):
        text = "<think>All reasoning, no output.</think>"
        assert strip_think_blocks(text) == ""

    def test_preserves_other_xml_tags(self):
        text = "<think>Reasoning.</think>Here is <code>some code</code> for you."
        assert strip_think_blocks(text) == "Here is <code>some code</code> for you."


class TestThinkBlockFilterStreaming:
    """Streaming filter that processes chunks incrementally."""

    def test_think_block_split_across_chunks(self):
        f = ThinkBlockFilter()
        # Think block starts in chunk 1, ends in chunk 3
        assert f.filter("Hello <thi") == "Hello "
        assert f.filter("nk>secret reasoning") == ""
        assert f.filter("</think> world") == " world"

    def test_clean_chunks_pass_through(self):
        f = ThinkBlockFilter()
        assert f.filter("Hello ") == "Hello "
        assert f.filter("world.") == "world."

    def test_complete_think_block_in_one_chunk(self):
        f = ThinkBlockFilter()
        assert f.filter("<think>reasoning</think>answer") == "answer"

    def test_content_before_and_after(self):
        f = ThinkBlockFilter()
        assert f.filter("before <think>hidden</think> after") == "before  after"

    def test_multiple_blocks_across_chunks(self):
        f = ThinkBlockFilter()
        assert f.filter("<think>a</think>one") == "one"
        assert f.filter(" <think>b</think>two") == " two"
