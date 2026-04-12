"""
tests/test_think_block_filter.py

Tests for <think> block stripping from qwen3 model output.
Covers both the batch strip_think_blocks() function (used in
non-streaming path) and the streaming ThinkBlockFilter class.

Includes coverage for unicode mathematical italic tag variants,
case-insensitive matching, and whitespace/BOM tolerance.
"""

from src.llm.adapter import strip_think_blocks, _normalize_unicode_tags
from src.api.openai_adapter import ThinkBlockFilter


def _to_math_italic(text: str) -> str:
    """Convert ASCII letters to Mathematical Italic Unicode (U+1D434-U+1D467).

    Used in tests to simulate qwen3's unicode italic formatting.
    Only converts a-z and A-Z; leaves all other characters unchanged.
    """
    result = []
    for ch in text:
        if 'a' <= ch <= 'z':
            result.append(chr(0x1D44E + (ord(ch) - ord('a'))))
        elif 'A' <= ch <= 'Z':
            result.append(chr(0x1D434 + (ord(ch) - ord('A'))))
        else:
            result.append(ch)
    return "".join(result)


class TestNormalizeUnicodeTags:
    """Unit tests for the unicode math italic normalizer."""

    def test_normalizes_lowercase_italic(self):
        # Mathematical Italic 't' 'h' 'i' 'n' 'k' -> ASCII 'think'
        italic_think = _to_math_italic("think")
        assert _normalize_unicode_tags(italic_think) == "think"

    def test_normalizes_uppercase_italic(self):
        italic_think = _to_math_italic("THINK")
        assert _normalize_unicode_tags(italic_think) == "THINK"

    def test_leaves_ascii_unchanged(self):
        assert _normalize_unicode_tags("hello world") == "hello world"

    def test_leaves_non_letter_chars_unchanged(self):
        assert _normalize_unicode_tags("<>/\ufeff ") == "<>/\ufeff "

    def test_mixed_italic_and_ascii(self):
        mixed = "<" + _to_math_italic("think") + ">"
        assert _normalize_unicode_tags(mixed) == "<think>"


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

    # -- Case-insensitive matching --

    def test_strips_capitalized_think_tag(self):
        text = "<Think>Internal reasoning here.</Think>The visible answer."
        assert strip_think_blocks(text) == "The visible answer."

    def test_strips_uppercase_think_tag(self):
        text = "<THINK>Loud reasoning.</THINK>The calm answer."
        assert strip_think_blocks(text) == "The calm answer."

    def test_strips_mixed_case_think_tag(self):
        text = "<tHiNk>Mixed case reasoning.</tHiNk>Result here."
        assert strip_think_blocks(text) == "Result here."

    # -- Whitespace/BOM tolerance --

    def test_strips_tag_with_inner_whitespace(self):
        text = "< think >Internal reasoning.</ think >Visible output."
        assert strip_think_blocks(text) == "Visible output."

    def test_strips_tag_with_bom(self):
        text = "<\ufeffthink>Reasoning with BOM.</\ufeffthink>Clean output."
        assert strip_think_blocks(text) == "Clean output."

    def test_strips_tag_with_whitespace_and_bom(self):
        text = "< \ufeff think >Reasoning.</ \ufeff think >Output."
        assert strip_think_blocks(text) == "Output."

    # -- Unicode mathematical italic --

    def test_strips_unicode_italic_think_tag(self):
        # Model emits <think> with 'think' in math italic unicode
        italic_open = "<" + _to_math_italic("think") + ">"
        italic_close = "</" + _to_math_italic("think") + ">"
        text = f"{italic_open}Unicode italic reasoning.{italic_close}The answer."
        assert strip_think_blocks(text) == "The answer."

    def test_strips_unicode_italic_with_ascii_mix(self):
        # Open tag in unicode italic, close tag in regular ASCII
        italic_open = "<" + _to_math_italic("think") + ">"
        text = f"{italic_open}Reasoning.</think>Output here."
        assert strip_think_blocks(text) == "Output here."

    def test_strips_unicode_italic_multiline(self):
        italic_open = "<" + _to_math_italic("think") + ">"
        italic_close = "</" + _to_math_italic("think") + ">"
        text = f"{italic_open}\nStep 1.\nStep 2.\n{italic_close}\nThe final answer."
        assert strip_think_blocks(text) == "The final answer."

    def test_strips_unicode_italic_uppercase(self):
        italic_open = "<" + _to_math_italic("THINK") + ">"
        italic_close = "</" + _to_math_italic("THINK") + ">"
        text = f"{italic_open}Loud italic reasoning.{italic_close}Result."
        assert strip_think_blocks(text) == "Result."

    # -- Orphaned tag handling (root cause of Q1/Q15 leaks) --

    def test_strips_orphaned_closing_tag_with_preceding_content(self):
        """Q1 regression: leaked reasoning ending in </think> with no
        matching opener passed through unchanged. The preceding content
        must be stripped along with the orphaned close tag."""
        text = "leaked reasoning discussing minorities</think>Here is the answer."
        assert strip_think_blocks(text) == "Here is the answer."

    def test_strips_orphaned_closing_tag_alone(self):
        text = "</think>Just the answer."
        assert strip_think_blocks(text) == "Just the answer."

    def test_strips_orphaned_opening_tag_to_end(self):
        """Model started a think block and never closed it — strip from
        the opening tag through end of string."""
        text = "Real answer here.<think>internal reasoning never closed"
        assert strip_think_blocks(text) == "Real answer here."

    def test_strips_orphaned_opening_tag_alone(self):
        text = "<think>unclosed thinking forever"
        assert strip_think_blocks(text) == ""

    def test_strips_orphaned_open_containing_regional_indicator_emoji(self):
        """Q15 regression: stray regional-indicator glyph 🇼 (U+1F1FC)
        appeared at the start of a response. Root cause: the glyph was
        inside an unclosed think block."""
        text = "<think>internal reasoning with 🇼 marker not closed"
        assert strip_think_blocks(text) == ""

    def test_orphaned_close_after_paired_block_still_stripped(self):
        """Paired block gets stripped in pass 1. A trailing orphaned
        </think> with content between them means the content is
        pre-close leakage — strip preceding content too."""
        text = "<think>first</think>real one leaked minorities</think>final answer."
        assert strip_think_blocks(text) == "final answer."

    def test_orphaned_tags_with_unicode_italic_variant(self):
        """Orphan handling must survive unicode italic normalization."""
        italic_close = "</" + _to_math_italic("think") + ">"
        text = f"leaked content {italic_close}visible answer."
        assert strip_think_blocks(text) == "visible answer."

    def test_orphaned_closing_with_bom_and_whitespace(self):
        text = "leaked< \ufeff /think >the actual answer."
        assert strip_think_blocks(text) == "the actual answer."

    def test_clean_response_unchanged_after_orphan_passes(self):
        """Ensure the orphan passes don't touch well-formed responses."""
        text = "A calm response with no think tags at all."
        assert strip_think_blocks(text) == text

    def test_clean_response_with_paired_block_unchanged_after_orphan_passes(self):
        text = "<think>internal</think>Visible answer with no orphans."
        assert strip_think_blocks(text) == "Visible answer with no orphans."


class TestThinkBlockFilterStreaming:
    """Streaming filter that processes chunks incrementally.

    Note: ThinkBlockFilter normalizes all text to lowercase for tag
    detection, so assertions use lowercase expected values.
    """

    def test_think_block_split_across_chunks(self):
        f = ThinkBlockFilter()
        # Think block starts in chunk 1, ends in chunk 3
        assert f.filter("Hello <thi") == "hello "
        assert f.filter("nk>secret reasoning") == ""
        assert f.filter("</think> world") == " world"

    def test_clean_chunks_pass_through(self):
        f = ThinkBlockFilter()
        assert f.filter("Hello ") == "hello "
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

    # -- Case-insensitive streaming --

    def test_capitalized_tag_in_stream(self):
        f = ThinkBlockFilter()
        assert f.filter("<Think>reasoning</Think>answer") == "answer"

    def test_uppercase_tag_in_stream(self):
        f = ThinkBlockFilter()
        assert f.filter("<THINK>reasoning</THINK>answer") == "answer"

    def test_mixed_case_across_chunks(self):
        f = ThinkBlockFilter()
        assert f.filter("start <Thi") == "start "
        assert f.filter("nk>hidden</") == ""
        assert f.filter("Think>visible") == "visible"

    # -- Whitespace/BOM tolerance in streaming --

    def test_whitespace_in_tag_streaming(self):
        f = ThinkBlockFilter()
        assert f.filter("< think >hidden</ think >visible") == "visible"

    def test_bom_in_tag_streaming(self):
        f = ThinkBlockFilter()
        text = "<\ufeffthink>hidden</\ufeffthink>visible"
        result = f.filter(text)
        assert result == "visible"

    # -- Unicode italic in streaming --

    def test_unicode_italic_tag_streaming(self):
        f = ThinkBlockFilter()
        italic_open = "<" + _to_math_italic("think") + ">"
        italic_close = "</" + _to_math_italic("think") + ">"
        text = f"{italic_open}hidden{italic_close}visible"
        assert f.filter(text) == "visible"

    def test_unicode_italic_split_across_chunks(self):
        f = ThinkBlockFilter()
        italic_think = _to_math_italic("think")
        # Split the italic open tag across chunks
        assert f.filter("start <" + italic_think[:2]) == "start "
        assert f.filter(italic_think[2:] + ">hidden") == ""
        assert f.filter("</think>end") == "end"
