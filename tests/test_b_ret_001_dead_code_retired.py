"""
tests/test_b_ret_001_dead_code_retired.py

Pins B-RET-001 cleanup against regression:

  - src/memory/search_conversation.py was deleted (dead module). Importing
    it must fail.
  - src/context/retriever.py no longer exposes get_conversation_items
    (dead wrapper).
  - scripts/rebuild_indexes.py no longer includes "conversation" in
    JSON_INDEX_TYPES (the JSON index was orphaned; SQLite is the only
    live backend for conversation memory).

If any of these resurface, semantic search will diverge from the live
SQLite store again and recent conversations will become invisible to
retrieval -- which is what produced the stale-index symptom on
2026-05-11.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_search_conversation_module_is_gone() -> None:
    """The dead src/memory/search_conversation.py module must not be
    importable. Any code that tries to import it should fail loud."""
    # Make sure pytest's prior runs in the same process haven't cached
    # the now-missing module under sys.modules.
    sys.modules.pop("src.memory.search_conversation", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.memory.search_conversation")


def test_context_retriever_no_longer_has_get_conversation_items() -> None:
    """ContextRetriever.get_conversation_items was a dead wrapper around
    the deleted search_conversation_memories. Removed. Retrieval for
    conversation memories now flows through get_memory_items, which hits
    memory.db via semantic_search."""
    from src.context.retriever import ContextRetriever
    assert not hasattr(ContextRetriever, "get_conversation_items")


def test_rebuild_indexes_excludes_conversation() -> None:
    """scripts/rebuild_indexes.py's JSON_INDEX_TYPES used to include
    'conversation' even though the JSON index for it was orphaned.
    Listing 'conversation' would have manual rebuilds keep producing
    stale-by-default files. The cleanup removes it."""
    # Load the script as a module to inspect the constant.
    spec = importlib.util.spec_from_file_location(
        "rebuild_indexes_under_test",
        # Resolve via repo layout: tests/ is sibling of scripts/.
        __import__("os").path.join(
            __import__("os").path.dirname(__file__),
            "..",
            "scripts",
            "rebuild_indexes.py",
        ),
    )
    if spec is None or spec.loader is None:
        pytest.skip("Could not locate scripts/rebuild_indexes.py via spec")
    # Skip module execution if its imports require an Ollama server or
    # other heavy deps -- just parse the file text for the constant.
    import re
    with open(spec.origin, encoding="utf-8") as f:
        src = f.read()
    match = re.search(r"JSON_INDEX_TYPES\s*=\s*\[([^\]]*)\]", src)
    assert match is not None, "JSON_INDEX_TYPES list not found in script"
    body = match.group(1)
    assert '"conversation"' not in body and "'conversation'" not in body, (
        "JSON_INDEX_TYPES must not contain 'conversation' after B-RET-001 "
        "cleanup -- conversation is SQLite-backed and the JSON index was "
        "orphaned."
    )


def test_no_stray_imports_of_search_conversation() -> None:
    """No source file in src/ should still IMPORT the deleted module.

    Substring matches in comments / docstrings are acceptable (and useful
    -- they document the retirement). Only actual import statements
    would break at runtime, so the regression scan is import-specific."""
    import os
    import re
    src_root = os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
    )
    import_re = re.compile(
        r"^\s*(?:from\s+src\.memory\.search_conversation\b|"
        r"import\s+src\.memory\.search_conversation\b)",
        re.MULTILINE,
    )
    offenders: list[str] = []
    for dirpath, _dirs, files in os.walk(src_root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if import_re.search(content):
                offenders.append(path)
    assert not offenders, (
        f"Stray IMPORTS of the deleted search_conversation module: "
        f"{offenders}"
    )
