"""
src/tasks/task_handler.py

Task creation handler. Two paths:

1. Explicit request: user says "create a task for X" -> write immediately
2. Offer/confirm: detector flags response -> Ember offers -> user confirms -> write

Both paths write via TaskService.write() with proper metadata.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from src.tasks.task_service import TaskService

logger = logging.getLogger("ember.task_handler")


# Module-level pending offer storage, keyed by session_id.
# Each entry is a list of task titles. Cleared after use or on next turn.
_pending_offers: dict[str, list[str]] = {}


# ---------------------------------------------------------------------------
# Explicit task creation patterns
# ---------------------------------------------------------------------------

# Each pattern has one capture group for the task title/list.
# Ordered most specific first. All case-insensitive.
EXPLICIT_TASK_PATTERNS = (
    # Direct creation: "create a task for/called/to X"
    r"(?:can you |please |could you )?create (?:a )?tasks? (?:for |to |called |named |about )(.+)",
    r"(?:can you |please |could you )?add (?:a )?tasks? (?:for |to |called |named |about )(.+)",
    r"(?:can you |please |could you )?make (?:a )?tasks? (?:for |to |called |named |about )(.+)",
    # "I need a task for X"
    r"i need (?:a )?tasks? (?:for |to |called |named |about )(.+)",
    # "new task: X" or "new task for X"
    r"new tasks?(?:: ?| (?:for |to |called |named |about ))(.+)",
    # "track X as a task"
    r"track (.+?) as (?:a )?tasks?",
    # "add X to my task list" / "put X on my task list"
    r"(?:add|put) (.+?) (?:to|on) my task list",
    # "remind me to X" / "I need to remember to X"
    r"remind me to (.+)",
    r"i need to remember to (.+)",
)

# Compile once
_EXPLICIT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in EXPLICIT_TASK_PATTERNS]


# ---------------------------------------------------------------------------
# Confirmation patterns
# ---------------------------------------------------------------------------

CONFIRM_PATTERNS = (
    "yes", "yeah", "yep", "yup",
    "sure", "sure thing",
    "please", "please do", "yes please",
    "ok", "okay", "ok go ahead",
    "go ahead", "go for it",
    "do it", "do them",
    "add it", "just add it", "add them", "just add them", "add them all",
    "create it", "create them", "yes create it", "yes create them",
    "sounds good", "that works",
    "just add them if that's okay", "just add them if that's ok",
    "add those", "yes add those", "add all of those",
)

DECLINE_PATTERNS = (
    "no", "nah", "nope", "don't", "skip", "never mind", "cancel",
    "no thanks", "not now",
)


@dataclass
class TaskCreationResult:
    """Result of a task creation attempt."""
    created: bool
    task_title: str | None = None
    task_titles: list[str] | None = None
    error: str | None = None


def detect_explicit_task_request(user_message: str) -> list[str]:
    """
    Check if the user message is an explicit task creation request.

    Returns a list of extracted task titles. Empty list if not a task request.
    Handles comma/and-separated lists: "create tasks for X, Y, and Z" -> ["X", "Y", "Z"]
    """
    if not user_message or len(user_message.strip()) < 10:
        return []

    text = user_message.strip()
    for pattern in _EXPLICIT_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = next((g for g in match.groups() if g), None)
            if raw:
                titles = _split_task_list(raw)
                return [_clean_title(t) for t in titles if _clean_title(t)]
    return []


def _split_task_list(raw: str) -> list[str]:
    """
    Split a comma/and-separated task list into individual titles.

    "weeding, mowing, and picking up sticks" -> ["weeding", "mowing", "picking up sticks"]
    "fix the bug" -> ["fix the bug"]
    """
    # Split on comma or " and " (but not "and" inside a phrase like "salt and pepper")
    # Strategy: split on ", and ", then on ", ", then on " and " only if result has 2+ items
    parts = re.split(r',\s*and\s+|,\s*', raw)
    if len(parts) == 1:
        # Try splitting on standalone " and " only if it looks like a list
        and_parts = re.split(r'\s+and\s+', raw)
        if len(and_parts) >= 2 and all(len(p.strip()) < 60 for p in and_parts):
            parts = and_parts
    return [p.strip() for p in parts if p.strip()]


def _clean_title(title: str) -> str:
    """
    Clean a raw task title into a short imperative action phrase.

    Strips filler prefixes ("me to", "I need to", etc.), enforces
    imperative form, caps at 8 words, no trailing ellipsis.
    """
    title = title.strip().rstrip('.!?…')

    # Strip common filler prefixes (case-insensitive)
    filler_prefixes = (
        "me to ",
        "i need to remember to ",
        "i need to ",
        "i want to ",
        "i should ",
        "i have to ",
        "i'll ",
        "i will ",
        "you should ",
        "you need to ",
        "you'll want to ",
        "make sure to ",
        "don't forget to ",
        "remember to ",
        "remind me to ",
        "please ",
        "can you ",
        "could you ",
    )
    # Apply repeatedly — some prefixes chain ("please remind me to")
    changed = True
    lower = title.lower()
    while changed:
        changed = False
        for prefix in filler_prefixes:
            if lower.startswith(prefix):
                title = title[len(prefix):]
                lower = title.lower()
                changed = True
                break

    # Capitalize first letter (imperative form)
    if title:
        title = title[0].upper() + title[1:]

    # Cap at 8 words
    words = title.split()
    if len(words) > 8:
        title = " ".join(words[:8])

    # Strip any trailing ellipsis or truncation artifacts
    title = title.rstrip('.…')

    return title.strip()


def create_task(
    title: str,
    source: str = "user_input",
    session_id: str | None = None,
    project_id: str | None = None,
    vault_path=None,
) -> TaskCreationResult:
    """
    Write a task to the vault. Returns a TaskCreationResult.

    Deduplicates by title: if an active or proposed task with the same
    title (case-insensitive) already exists, the write is skipped.
    """
    try:
        service = TaskService(vault_path=vault_path)

        # Dedup check: skip if an active/proposed task with same title exists
        existing = service.read_active()
        normalized_title = title.strip().lower()
        for existing_task in existing:
            if existing_task.title.strip().lower() == normalized_title:
                logger.info("[TASK_HANDLER] Dedup: '%s' already exists, skipping", title[:60])
                return TaskCreationResult(created=True, task_title=title)

        metadata = {}
        if session_id:
            metadata["session_id"] = session_id
        record = TaskService.make_record(
            title=title,
            status="active",
            source=source,
            project_id=project_id,
            metadata=metadata,
        )
        path = service.write(record)
        logger.info("[TASK_HANDLER] Wrote task: %s -> %s", title[:60], path)
        return TaskCreationResult(created=True, task_title=title)
    except Exception as exc:
        logger.warning("[TASK_HANDLER] Failed to write task: %s", exc)
        return TaskCreationResult(created=False, task_title=title, error=str(exc))


def store_pending_offer(session_id: str, task_title: str) -> None:
    """Store a pending task offer for the next turn. Appends to existing list."""
    if session_id not in _pending_offers:
        _pending_offers[session_id] = []
    _pending_offers[session_id].append(task_title)
    logger.info("[TASK_HANDLER] Stored pending offer for session %s: %s (total: %d)",
                session_id, task_title[:60], len(_pending_offers[session_id]))


def check_pending_confirmation(
    session_id: str,
    user_message: str,
    project_id: str | None = None,
) -> TaskCreationResult | None:
    """
    Check if the user is confirming or declining pending task offer(s).

    Returns:
        TaskCreationResult with task_titles list if confirmed
        TaskCreationResult(created=False) if declined
        None if no pending offer exists for this session
    """
    if session_id not in _pending_offers:
        return None

    task_titles = _pending_offers.pop(session_id)
    lower = user_message.strip().lower()

    # Check for decline first
    if _matches_any(lower, DECLINE_PATTERNS):
        logger.info("[TASK_HANDLER] User declined %d task(s)", len(task_titles))
        return TaskCreationResult(created=False, task_titles=task_titles)

    # Check for confirmation
    if _matches_any(lower, CONFIRM_PATTERNS):
        created = []
        failed = []
        for title in task_titles:
            result = create_task(
                title=title,
                source="task_detector",
                session_id=session_id,
                project_id=project_id,
            )
            if result.created:
                created.append(title)
            else:
                failed.append(title)
        if created:
            return TaskCreationResult(created=True, task_titles=created)
        return TaskCreationResult(created=False, task_titles=task_titles, error="All writes failed")

    # Ambiguous -- not clearly a confirm or decline. Clear the offer silently.
    logger.info("[TASK_HANDLER] Ambiguous response, clearing %d pending offer(s)", len(task_titles))
    return None


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    """Check if text matches any pattern (exact match or starts with pattern + separator)."""
    return any(
        text == p or text.startswith(p + " ") or text.startswith(p + ",") or text.startswith(p + ".")
        for p in patterns
    )


def clear_pending_offer(session_id: str) -> None:
    """Clear any pending offer for a session."""
    _pending_offers.pop(session_id, None)
