"""
src/onboarding/service.py

OnboardingService manages the first-run onboarding conversation flow.

When a new user has no profile records and no onboarding state, Ember enters
onboarding mode: it asks 7 questions one at a time, structures each answer into
a clean first-person profile record via a small LLM call, writes it to the vault,
and transitions to normal conversation when complete.

State is persisted as a vault record (type="onboarding") so onboarding survives
server restarts. Once complete, the _confirmed_complete flag short-circuits
the vault check on all subsequent requests.

Detection logic:
  1. _confirmed_complete is True  → inactive (no vault read)
  2. onboarding state record exists with text="onboarding_complete"  → inactive
  3. onboarding state record exists with text="onboarding_in_progress" → active
  4. No state record AND no profile records → active (first run)
  5. No state record AND profile records exist → inactive (seeded externally)
"""

from __future__ import annotations

import logging

import ollama

from src.core.config import get_ember_model
from src.memory.service import MemoryService
from src.onboarding.steps import ONBOARDING_STEPS
from src.state.state_service import StateService

logger = logging.getLogger("ember.onboarding")

_WELCOME = """\
Welcome to Ember. I'm your personal intelligence system — I remember, reflect, \
and reason alongside you over time.

Before we get started, I'd like to ask you a few questions so I can learn who you are. \
This helps me give you relevant, personalized responses from day one. \
Seven questions — answer as briefly or in as much detail as you'd like.

{first_question}"""

_TRANSITION = """\
That's everything I need to get started. Your answers have been saved to your memory vault.

Ember is ready. What's on your mind?"""

_FORMAT_SYSTEM = """\
You are formatting a user's answer to an onboarding question into a clean \
first-person profile record for a personal AI system's memory vault.

Write 1-3 sentences in first person that capture the essential information. \
Use "I" statements. Be specific. Do not add or infer anything not present in the answer. \
Do not mention the question. Output only the formatted sentence(s) — nothing else."""


class OnboardingService:

    def __init__(self) -> None:
        self._memory = MemoryService()
        self._state = StateService()
        self._confirmed_complete: bool = False  # skip vault check once completion is known

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return True if onboarding should handle the next user message."""
        if self._confirmed_complete:
            return False

        records = self._state.read_by_category("onboarding")
        if records:
            if records[0].text == "onboarding_complete":
                self._confirmed_complete = True
                return False
            return True  # onboarding_in_progress

        # No state record — check for existing profile records
        if self._memory.read("profile", limit=1):
            self._confirmed_complete = True
            return False

        return True  # no state, no profiles → first run

    def handle(self, user_message: str) -> str:
        """
        Process a user message during onboarding. Returns Ember's next response.

        First call: ignores message content, writes step-0 state, returns welcome + Q0.
        Middle calls: saves formatted answer, advances step, returns next question.
        Final call: saves last answer, writes completion state, returns transition message.
        """
        records = self._state.read_by_category("onboarding")

        # First ever call — no state record yet
        if not records:
            self._write_progress(step=0, completed=[])
            logger.info("[ONBOARDING] Starting - asking step 0 (%s)", ONBOARDING_STEPS[0].key)
            return _WELCOME.format(first_question=ONBOARDING_STEPS[0].question)

        # Subsequent calls — save answer for current step, then advance
        current = records[0].metadata.get("current_step", 0)
        completed: list[str] = records[0].metadata.get("completed_steps", [])
        step = ONBOARDING_STEPS[current]

        if user_message.strip():
            formatted = self._format_answer(user_message.strip(), step.category)
            self._memory.write(
                text=formatted,
                memory_type="profile",
                source="onboarding",
                tags=step.tags,
                metadata={"category": step.category, "onboarding_step": step.key},
            )
            completed = completed + [step.key]
            logger.info("[ONBOARDING] Saved answer for step %d (%s)", current, step.key)

        next_step = current + 1

        if next_step >= len(ONBOARDING_STEPS):
            self._write_complete()
            self._confirmed_complete = True  # cache — skip vault on next call
            logger.info("[ONBOARDING] Complete")
            return _TRANSITION

        self._write_progress(step=next_step, completed=completed)
        return ONBOARDING_STEPS[next_step].question

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_progress(self, step: int, completed: list[str]) -> None:
        self._state.write(StateService.make_record(
            state_type="onboarding",
            text="onboarding_in_progress",
            source="onboarding_service",
            tags=["onboarding", "system"],
            metadata={"current_step": step, "completed_steps": completed},
        ))

    def _write_complete(self) -> None:
        self._state.write(StateService.make_record(
            state_type="onboarding",
            text="onboarding_complete",
            source="onboarding_service",
            tags=["onboarding", "system"],
        ))

    def _format_answer(self, answer: str, category: str) -> str:
        """
        Use a small LLM call to convert the user's raw answer into a structured
        first-person profile sentence suitable for long-term memory retrieval.
        Falls back to the raw answer if the call fails or returns too little.
        """
        try:
            resp = ollama.chat(
                model=get_ember_model(),
                messages=[
                    {"role": "system", "content": _FORMAT_SYSTEM},
                    {"role": "user", "content": f"Category: {category}\nAnswer: {answer}"},
                ],
            )
            formatted = resp["message"]["content"].strip()
            return formatted if len(formatted) >= 10 else answer
        except Exception as exc:
            logger.warning("[ONBOARDING] LLM format call failed: %s - storing raw answer", exc)
            return answer
