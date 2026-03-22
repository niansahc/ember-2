"""
scripts/add_state.py

Command line tool for writing state records to the private vault.

Wraps StateService.make_record() and StateService.write() so state items
can be added quickly from the terminal without touching the Python API directly.

Usage examples
--------------
# Set current focus:
python scripts/add_state.py --type current_focus --text "Building state layer for Ember-2"

# Set a blocker with priority:
python scripts/add_state.py --type blocker --text "Waiting on vault index rebuild" --priority high

# Add an open loop with tags:
python scripts/add_state.py --type open_loop --text "Follow up on eval harness scoring" --tags "ember2,eval"

# Set an active project with source:
python scripts/add_state.py --type active_project --text "Ember-2 state layer implementation" --source "planning_session" --tags "ember2,state"

Valid --type values
-------------------
active_project, open_loop, current_focus, blocker, routine, priority, next_action
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.state.models import VALID_STATE_CATEGORIES
from src.state.state_service import StateService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a state record to the Ember-2 private vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/add_state.py --type current_focus --text \"Building state layer\"\n"
            "  python scripts/add_state.py --type blocker --text \"Waiting on index rebuild\" --priority high\n"
            "  python scripts/add_state.py --type open_loop --text \"Follow up on eval\" --tags \"ember2,eval\"\n"
        ),
    )

    parser.add_argument(
        "--type",
        required=True,
        dest="state_type",
        metavar="TYPE",
        help=(
            f"State category. Must be one of: {', '.join(sorted(VALID_STATE_CATEGORIES))}"
        ),
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Human-readable description of this state artifact.",
    )
    parser.add_argument(
        "--priority",
        choices=["high", "medium", "low"],
        default=None,
        help="Optional priority signal (high, medium, low).",
    )
    parser.add_argument(
        "--tags",
        default=None,
        metavar="TAG1,TAG2",
        help="Comma-separated list of tags, e.g. --tags ember2,state",
    )
    parser.add_argument(
        "--source",
        default="user_input",
        help='Source identifier for this record. Defaults to "user_input".',
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Validate state type explicitly before constructing the record so we can
    # print a clear, actionable error message rather than a raw ValueError.
    if args.state_type not in VALID_STATE_CATEGORIES:
        print(
            f"Error: '{args.state_type}' is not a valid state type.\n"
            f"Valid types: {', '.join(sorted(VALID_STATE_CATEGORIES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse tags from comma-separated string.
    tags: list[str] = (
        [t.strip() for t in args.tags.split(",") if t.strip()]
        if args.tags
        else []
    )

    # Build metadata — only include priority if provided.
    metadata: dict = {}
    if args.priority:
        metadata["priority"] = args.priority

    service = StateService()

    record = StateService.make_record(
        state_type=args.state_type,
        text=args.text,
        source=args.source,
        tags=tags,
        metadata=metadata,
    )

    file_path = service.write(record)

    print(f"✓ State record written: [{record.type}] {record.text}")
    print(f"  Path: {file_path}")


if __name__ == "__main__":
    main()
