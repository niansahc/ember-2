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
# Each entry is a task title string. Cleared after use or on next turn.
_pending_offers: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Explicit task creation patterns
# ---------------------------------------------------------------------------

EXPLICIT_TASK_PATTERNS = (
    r"create a task (?:for |to |called |named |about )?(.+)",
    r"add a task (?:for |to |called |named |about )?(.+)",
    r"track (?:this |that )?as a task(?:: ?(.+))?",
    r"make a task (?:for |to |called |named |about )?(.+)",
    r"new task(?:: ?| (?:for |to |called |named |about ))(.+)",
)

# Compile once
_EXPLICIT_PATTERNS = [re.compile(p, re.IGNORECASE) for p in EXPLICIT_TASK_PATTERNS]


# ---------------------------------------------------------------------------
# Confirmation patterns
# ---------------------------------------------------------------------------

CONFIRM_PATTERNS = (
    "yes", "yeah", "yep", "yup", "sure", "please", "do it",
    "go ahead", "add it", "create it", "yes please", "yes create it",
    "sounds good", "ok", "okay",
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
    error: str | None = None


def detect_explicit_task_request(user_message: str) -> str | None:
    """
    Check if the user message is an explicit task creation request.

    Returns the extracted task title, or None if not a task request.
    """
    if not user_message or len(user_message.strip()) < 10:
        return None

    text = user_message.strip()
    for pattern in _EXPLICIT_PATTERNS:
        match = pattern.search(text)
        if match:
            # Get the first non-None capture group
            title = next((g for g in match.groups() if g), None)
            if title:
                title = title.strip().rstrip('.!?')
                if len(title) > 80:
                    title = title[:77] + '...'
                return title
    return None


def create_task(
    title: str,
    source: str = "user_input",
    session_id: str | None = None,
    project_id: str | None = None,
    vault_path=None,
) -> TaskCreationResult:
    """
    Write a task to the vault. Returns a TaskCreationResult.
    """
    try:
        service = TaskService(vault_path=vault_path)
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
    """Store a pending task offer for the next turn."""
    _pending_offers[session_id] = task_title
    logger.info("[TASK_HANDLER] Stored pending offer for session %s: %s", session_id, task_title[:60])


def check_pending_confirmation(
    session_id: str,
    user_message: str,
    project_id: str | None = None,
) -> TaskCreationResult | None:
    """
    Check if the user is confirming or declining a pending task offer.

    Returns:
        TaskCreationResult if confirmed (created=True) or declined (created=False)
        None if no pending offer exists for this session
    """
    if session_id not in _pending_offers:
        return None

    task_title = _pending_offers.pop(session_id)
    lower = user_message.strip().lower()

    # Check for decline first
    if any(lower == p or lower.startswith(p + " ") or lower.startswith(p + ",") for p in DECLINE_PATTERNS):
        logger.info("[TASK_HANDLER] User declined task: %s", task_title[:60])
        return TaskCreationResult(created=False, task_title=task_title)

    # Check for confirmation
    if any(lower == p or lower.startswith(p + " ") or lower.startswith(p + ",") or lower.startswith(p + ".") for p in CONFIRM_PATTERNS):
        return create_task(
            title=task_title,
            source="task_detector",
            session_id=session_id,
            project_id=project_id,
        )

    # Ambiguous -- not clearly a confirm or decline. Clear the offer silently.
    logger.info("[TASK_HANDLER] Ambiguous response, clearing pending offer: %s", task_title[:60])
    return None


def clear_pending_offer(session_id: str) -> None:
    """Clear any pending offer for a session."""
    _pending_offers.pop(session_id, None)
