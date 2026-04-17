"""tests/test_chatgpt_role_normalization.py — task #25 role normalization."""

from __future__ import annotations

import pytest

from src.ingest.models import NormalizedDocument
from src.ingest.chunking import chunk_document, _detect_role


class TestImporterRoles:
    """The importer stores per-message roles in metadata.roles."""

    def _make_doc(self, messages, roles=None):
        meta = {
            "type": "chatgpt_export",
            "file": "test.json",
            "messages": messages,
        }
        if roles is not None:
            meta["roles"] = roles
        return NormalizedDocument(
            source="chatgpt",
            doc_id="test_0",
            title="Test Chat",
            created_at="2026-04-15",
            content="\n\n".join(messages),
            metadata=meta,
        )

    def test_roles_propagate_to_chunks(self):
        doc = self._make_doc(
            messages=[
                "User: What is the population of Tokyo?",
                "Assistant: Tokyo has approximately 37 million people in its metropolitan area.",
            ],
            roles=["user", "assistant"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 2
        assert chunks[0].metadata["role"] == "user"
        assert chunks[1].metadata["role"] == "assistant"

    def test_role_matches_original_value(self):
        doc = self._make_doc(
            messages=["System: You are a helpful assistant with these instructions."],
            roles=["system"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].metadata["role"] == "system"

    def test_text_prefix_preserved(self):
        doc = self._make_doc(
            messages=["User: Tell me about the weather forecast for this week."],
            roles=["user"],
        )
        chunks = chunk_document(doc)
        assert chunks[0].content.startswith("User:")

    def test_fallback_to_prefix_detection_when_no_roles(self):
        doc = self._make_doc(
            messages=[
                "User: What is the best programming language?",
                "Assistant: It depends on the use case and context for the project.",
            ],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 2
        assert chunks[0].metadata["role"] == "user"
        assert chunks[1].metadata["role"] == "assistant"

    def test_fallback_when_roles_list_shorter(self):
        doc = self._make_doc(
            messages=[
                "User: First message here with enough content.",
                "Assistant: Second message here with enough content.",
                "User: Third message without a corresponding role entry.",
            ],
            roles=["user", "assistant"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 3
        assert chunks[0].metadata["role"] == "user"
        assert chunks[1].metadata["role"] == "assistant"
        assert chunks[2].metadata["role"] == "user"


class TestDetectRoleFallback:

    def test_user_prefix(self):
        assert _detect_role("user: some text") == "user"

    def test_assistant_prefix(self):
        assert _detect_role("assistant: some text") == "assistant"

    def test_tool_prefix(self):
        assert _detect_role("tool: some output") == "tool"

    def test_unknown_prefix(self):
        assert _detect_role("something without a known prefix") == "unknown"
