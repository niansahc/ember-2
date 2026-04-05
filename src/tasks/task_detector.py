"""
src/tasks/task_detector.py

Post-generation task detection.

Analyzes Ember's draft response for task-worthy content -- when Ember is
implicitly taking on work or when the user has stated something that should
be tracked as a task.

Detection uses pattern matching against known task-indicating phrases.
Conservative threshold -- same pattern as commitment_detector.py.
False negatives are acceptable; false positives (spurious task offers)
are not.

When detected, returns a suggested_response that Ember can append to
offer creating the task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("ember.task_detector")


@dataclass
class TaskDetectionResult:
    detected: bool
    task_title: str | None = None
    suggested_response: str | None = None


# Task-indicating patterns -- phrases that suggest Ember is taking on
# trackable work or the user has expressed a concrete action item.
TASK_PATTERNS = (
    # Ember taking on work
    "i'll set up",
    "i'll create",
    "i'll build",
    "i'll write",
    "i'll implement",
    "i'll add",
    "i'll fix",
    "i'll update",
    "i'll refactor",
    "i'll test",
    "i'll configure",
    "i'll migrate",
    # Structured plans
    "here's the plan:",
    "here's what needs to happen:",
    "the steps are:",
    "action items:",
    "todo:",
    "to-do:",
    # User-directed tasks detected in response
    "you should",
    "you need to",
    "you'll want to",
    "make sure to",
    "don't forget to",
    "remember to",
)

# Non-task patterns -- phrases that sound like tasks but are just
# explanations, observations, or conditional suggestions.
NON_TASK_PATTERNS = (
    "you could",
    "you might",
    "if you want",
    "one option is",
    "for example",
    "in general",
    "typically",
    "here's how",
    "here's an example",
    "this is because",
    "the reason is",
    "that means",
)


def detect_task(response_text: str) -> TaskDetectionResult:
    """
    Analyze Ember's response for task-worthy content.

    Returns a TaskDetectionResult with detected=True if a task pattern
    is found and no strong non-task signal overrides it.

    The task_title is extracted as the sentence containing the match,
    truncated to 80 characters.
    """
    if not response_text or len(response_text.strip()) < 20:
        return TaskDetectionResult(detected=False)

    lower = response_text.lower()

    # Count non-task signals
    non_task_count = sum(1 for p in NON_TASK_PATTERNS if p in lower)

    for pattern in TASK_PATTERNS:
        if pattern in lower:
            # If there are more non-task signals than task signals,
            # this is probably an explanation, not a task
            if non_task_count > 1:
                logger.info(
                    "[TASK_DETECT] Pattern '%s' found but overridden by %d non-task signals",
                    pattern, non_task_count,
                )
                continue

            task_title = _extract_task_title(response_text, pattern)
            suggested = f"Want me to add \"{task_title}\" as a task?"
            logger.info("[TASK_DETECT] Detected: %s", task_title[:60])

            return TaskDetectionResult(
                detected=True,
                task_title=task_title,
                suggested_response=suggested,
            )

    return TaskDetectionResult(detected=False)


def _extract_task_title(text: str, pattern: str) -> str:
    """Extract a short task title from the sentence containing the pattern."""
    lower = text.lower()
    idx = lower.find(pattern)
    if idx == -1:
        return _clean_detected_title(text[:80])

    # Walk backward to find sentence start
    start = idx
    while start > 0 and text[start - 1] not in '.!?\n':
        start -= 1

    # Walk forward to find sentence end
    end = idx + len(pattern)
    while end < len(text) and text[end] not in '.!?\n':
        end += 1
    if end < len(text):
        end += 1  # include the punctuation

    sentence = text[start:end].strip()
    return _clean_detected_title(sentence)


def _clean_detected_title(title: str) -> str:
    """Clean a detected task title into a short imperative phrase."""
    title = title.strip().rstrip('.!?…')

    # Strip filler prefixes
    filler_prefixes = (
        "i'll ", "i will ", "i need to ", "i want to ", "i should ",
        "i have to ", "you should ", "you need to ", "you'll want to ",
        "make sure to ", "don't forget to ", "remember to ",
        "me to ", "please ", "can you ", "could you ",
    )
    lower = title.lower()
    for prefix in filler_prefixes:
        if lower.startswith(prefix):
            title = title[len(prefix):]
            lower = title.lower()

    if title:
        title = title[0].upper() + title[1:]

    words = title.split()
    if len(words) > 8:
        title = " ".join(words[:8])

    return title.rstrip('.…').strip()
