#!/usr/bin/env python3
"""
scripts/journal.py — write a journal entry to the Ember-2 vault.

Usage:
  python scripts/journal.py --text "Today I finished the journal ingestion feature."
  python scripts/journal.py --text "Good session." --mood focused --tags work ember-2
  python scripts/journal.py  # opens $EDITOR, or prompts inline if $EDITOR not set
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.write_memory import write_memory


def get_text_from_editor() -> str:
    editor = os.environ.get("EDITOR", "")
    if editor:
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
            tmp_path = f.name
        subprocess.call([editor, tmp_path])
        with open(tmp_path, encoding="utf-8") as f:
            text = f.read().strip()
        os.unlink(tmp_path)
        return text
    else:
        print("No $EDITOR set. Enter your journal entry (Ctrl+D or Ctrl+Z when done):")
        return sys.stdin.read().strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a journal entry to Ember-2.")
    parser.add_argument(
        "--text",
        type=str,
        help="Journal entry text. Omit to open $EDITOR.",
    )
    parser.add_argument(
        "--mood",
        type=str,
        help="Optional mood (e.g. focused, tired, anxious, calm). Stored in metadata and tags.",
    )
    parser.add_argument(
        "--tags",
        nargs="*",
        default=[],
        help="Optional space-separated tags.",
    )
    args = parser.parse_args()

    text = args.text if args.text else get_text_from_editor()

    if not text:
        print("No text provided. Aborting.")
        sys.exit(1)

    metadata: dict = {}
    if args.mood:
        metadata["mood"] = args.mood

    tags: list[str] = list(args.tags)
    if args.mood and args.mood not in tags:
        tags = [args.mood] + tags  # mood surfaces as a tag for retrieval

    path = write_memory(
        text=text,
        memory_type="journal",
        source="cli",
        tags=tags,
        metadata=metadata,
    )

    if path is None:
        print("Entry not written — content was filtered (too short or low quality).")
        sys.exit(1)

    print(f"Journal entry written: {path}")


if __name__ == "__main__":
    main()
