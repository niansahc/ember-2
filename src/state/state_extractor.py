"""
src/state/state_extractor.py

Automatic state extraction from conversation turns.

After each chat turn completes, the StateExtractor analyzes the user
message and assistant reply for state-relevant signals (projects, blockers,
focus areas, next actions, etc.) and returns StateRecord objects to be
written to the vault.

Design:
  - Non-blocking: called after the response is already sent to the user
  - Non-fatal: all errors caught and logged, never raised
  - Conservative: only writes high/medium confidence extractions
  - Inspectable: logs what was extracted at INFO level
  - Uses a separate low-temperature LLM call for structured extraction
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import ollama

from src.core.config import get_ember_model
from src.state.models import VALID_STATE_CATEGORIES, StateRecord

logger = logging.getLogger("ember.state_extractor")

# Categories eligible for auto-extraction (exclude system-level ones)
EXTRACTABLE_CATEGORIES = VALID_STATE_CATEGORIES - {"onboarding", "pending_confirmation"}

# Minimum word count to attempt extraction — short messages rarely contain state
MIN_WORDS_FOR_EXTRACTION = 10


class StateExtractor:
    """
    Extracts state signals from conversation turns using an LLM call.

    Usage:
        extractor = StateExtractor()
        records = extractor.extract("I need to fix the auth bug by Friday", "I'll help you...")
        # records = [StateRecord(type="blocker", text="Fix the auth bug by Friday", ...)]
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or get_ember_model()

    def extract(self, user_message: str, assistant_reply: str) -> list[StateRecord]:
        """
        Analyze a conversation turn and extract state signals.

        Parameters
        ----------
        user_message : str
            The user's message from this turn.
        assistant_reply : str
            Ember's response from this turn.

        Returns
        -------
        list[StateRecord]
            Zero or more state records to write. Empty list on any error
            or if no state signals are found.
        """
        try:
            return self._do_extract(user_message, assistant_reply)
        except Exception as exc:
            logger.warning("[STATE_EXTRACT] Extraction failed (non-fatal): %s", exc)
            return []

    def _do_extract(self, user_message: str, assistant_reply: str) -> list[StateRecord]:
        """Internal extraction logic. May raise — caller catches."""

        # Skip short messages — unlikely to contain state
        word_count = len(user_message.split())
        if word_count < MIN_WORDS_FOR_EXTRACTION:
            logger.info("[STATE_EXTRACT] Skipped — user message too short (%d words)", word_count)
            return []

        # Build the extraction prompt
        categories_desc = ", ".join(sorted(EXTRACTABLE_CATEGORIES))
        prompt = (
            "You are a state extraction engine. Analyze this conversation turn and identify "
            "any signals about the user's current state: what they're working on, what's blocking them, "
            "what they need to do next, their priorities, or active projects.\n\n"
            f"Valid state types: {categories_desc}\n\n"
            f"User: {user_message}\n\n"
            f"Assistant: {assistant_reply[:500]}\n\n"
            "Return ONLY valid JSON in this exact format (no extra text):\n"
            '{"extractions": [{"type": "<state_type>", "text": "<brief description>", '
            '"confidence": "high|medium|low"}]}\n\n'
            "Rules:\n"
            "- Only extract clear, explicit signals — not vague or implied ones\n"
            "- If no state signals are present, return {\"extractions\": []}\n"
            "- Keep text descriptions concise (under 100 characters)\n"
            "- Casual conversation, greetings, and questions about Ember produce NO extractions\n"
        )

        # Make the LLM call at low temperature for structured output
        response = ollama.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise extraction engine. Return only valid JSON. No explanation.",
                },
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.1},
        )

        raw = response["message"]["content"]
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> list[StateRecord]:
        """Parse the LLM JSON response into StateRecord objects."""

        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            logger.info("[STATE_EXTRACT] No JSON found in LLM response")
            return []

        data = json.loads(json_match.group())
        extractions = data.get("extractions", [])

        if not extractions:
            logger.info("[STATE_EXTRACT] No state signals found this turn")
            return []

        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        records = []

        for i, item in enumerate(extractions):
            state_type = item.get("type", "")
            text = item.get("text", "")
            confidence = item.get("confidence", "low")

            # Validate category
            if state_type not in EXTRACTABLE_CATEGORIES:
                logger.warning(
                    "[STATE_EXTRACT] Skipping invalid type '%s'", state_type
                )
                continue

            # Skip low confidence
            if confidence == "low":
                logger.info(
                    "[STATE_EXTRACT] Skipping low-confidence: %s — %s",
                    state_type, text[:60],
                )
                continue

            # Skip empty text
            if not text.strip():
                continue

            record = StateRecord(
                id=f"{timestamp}_{i}",
                timestamp=timestamp,
                type=state_type,
                text=text.strip(),
                source="state_extractor",
                tags=["auto_extracted", state_type],
                metadata={
                    "confidence": confidence,
                    "extraction_source": "conversation",
                },
            )
            records.append(record)
            logger.info(
                "[STATE_EXTRACT] Found: %s — %s (confidence: %s)",
                state_type, text[:60], confidence,
            )

        if records:
            logger.info("[STATE_EXTRACT] Extracted %d state record(s) this turn", len(records))
        else:
            logger.info("[STATE_EXTRACT] No actionable state signals this turn")

        return records
