"""
src/memory/exchange.py

Persistence for one exchange: its user turn, its assistant turn, and the
exchange outcome when no assistant turn was stored (ADR-047). Terms follow
CONTEXT.md.

One ExchangeRecorder exists per exchange. The chat handler creates it in
Phase A and every exit from the exchange goes through it:

  - record_user_turn() writes the user turn as soon as the message arrives,
    then the conversation record if the conversation is new. The user record
    comes first, so Ember never creates an empty conversation.
  - record_reply() or record_outcome() finishes the exchange. The first finish
    wins under a lock; a second finish writes nothing. This is what keeps the
    rule "every stored user turn ends in exactly one assistant turn or exactly
    one exchange outcome" in one place instead of at every exit.

Record writes are canonical only (write_canonical_record). Indexing happens in
a vault-bound background thread, and only for records that pass
should_index(), the one filter shared by every derived artifact. An exchange
outcome is never indexed and never returned in history.

Storage failures surface as ExchangeStorageError, so the caller can tell the
user the reply was not saved (the storage_failed error code, ADR-040) instead
of losing it silently.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from src.core.config import VaultWriteBlocked, spawn_vault_bound_thread, vault_binding
from src.core.jsonio import JsonIoError
from src.memory.session import create_session, session_exists
from src.memory.write_memory import (
    EXCHANGE_OUTCOME_KIND,
    index_record,
    should_index,
    write_canonical_record,
)

logger = logging.getLogger("ember.exchange")

# Exchange outcome values (CONTEXT.md: Exchange outcome).
OUTCOME_FAILED = "failed"
OUTCOME_INTERRUPTED = "interrupted"

# Errors that mean "the vault did not take the write". Anything else is a bug
# and propagates unchanged.
_STORAGE_ERRORS = (OSError, JsonIoError, VaultWriteBlocked)

# Conversation title rules, moved here from the chat handler with the
# conversation-record write itself.
DEFAULT_CONVERSATION_TITLE = "New conversation"
_TITLE_LIMIT = 50


class ExchangeStorageError(Exception):
    """A record of this exchange could not be written to the vault.

    `step` names the record ("user turn", "conversation record", "reply") and
    `cause_type` the underlying exception type. Exception text is never kept:
    it can carry vault paths, and only type names go to the log.

    sse_error_code is the ADR-040 error code the stream guard sends for this
    failure (src.api.sse.STORAGE_FAILED_CODE).
    """

    sse_error_code = "storage_failed"

    def __init__(self, step: str, cause: BaseException):
        self.step = step
        self.cause_type = type(cause).__name__
        super().__init__(f"{step} write failed: {self.cause_type}")


def conversation_title(text: str) -> str:
    """Title a new conversation from its first user turn.

    First 50 characters, trimmed back to a word boundary with an ellipsis when
    the text was longer. Empty text, such as an image-only first message, gets
    the default title.
    """
    title = text[:_TITLE_LIMIT].strip()
    if not title:
        return DEFAULT_CONVERSATION_TITLE
    if len(text) > _TITLE_LIMIT and " " in title:
        title = title.rsplit(" ", 1)[0] + "..."
    return title


def _index_job(record: dict, file_path: Path) -> None:
    """Index one record if it passes should_index. Runs in a bound thread.

    A failure here leaves the canonical record in place, unindexed until a
    rebuild; it must never reach the exchange, which has already moved on.
    """
    try:
        if should_index(record):
            index_record(record, file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[EXCHANGE] index failed (a rebuild restores it): %s", type(exc).__name__,
        )


class ExchangeRecorder:
    """Writes one exchange's records and finishes the exchange exactly once."""

    def __init__(
        self,
        exchange_id: str,
        session_id: str,
        *,
        project_id: Optional[str] = None,
        vault: Any = None,
        enabled: bool = True,
    ):
        """
        exchange_id : the response id the client receives (CONTEXT.md).
        session_id  : the conversation's id (code name: session_id).
        vault       : the vault captured at exchange start. Every write and
                      index job is bound to it, so a vault swap mid-exchange
                      cannot move this exchange's records (issue #144).
        enabled     : False when vault writes are off (vault toggle, ADR-031,
                      or a test request). Nothing is written at all.
        """
        self.exchange_id = exchange_id
        self.session_id = session_id
        self.project_id = project_id
        self.vault = vault
        self.enabled = enabled
        self._lock = threading.RLock()
        self._user_record: Optional[dict] = None
        self._user_path: Optional[Path] = None
        self._user_index_scheduled = False
        self._finished = False

    @property
    def user_turn_stored(self) -> bool:
        return self._user_record is not None

    @property
    def finished(self) -> bool:
        return self._finished

    def _metadata(self, role: str, content_kind: str) -> dict:
        metadata: dict[str, Any] = {
            "role": role,
            "content_kind": content_kind,
            "session_id": self.session_id,
            "exchange_id": self.exchange_id,
        }
        if self.project_id:
            metadata["project_id"] = self.project_id
        return metadata

    def _schedule_index(self, record: dict, file_path: Path) -> None:
        spawn_vault_bound_thread(
            _index_job, args=(record, file_path), vault=self.vault, name="exchange-index",
        )

    def record_user_turn(self, text: str, image_count: int = 0) -> bool:
        """Write the user turn, then the conversation record if it is new.

        `text` is what the client sent, unchanged (CONTEXT.md: Turn). An
        image-only message stores empty text plus `image_count`; empty text
        with no images is refused. The turn is not indexed here: the handler
        calls ensure_user_indexed() once retrieval for this exchange is done,
        so the exchange cannot retrieve its own user turn.

        Returns True when the user record was written. Raises
        ExchangeStorageError when a write fails; user_turn_stored then says
        whether the user record made it before the failure.
        """
        if not self.enabled:
            return False
        if not text.strip() and image_count <= 0:
            logger.warning("[EXCHANGE] empty user turn with no images: not stored")
            return False

        metadata = self._metadata("user", "user_content")
        if image_count > 0:
            metadata["image_count"] = image_count

        with vault_binding(self.vault):
            try:
                record, path = write_canonical_record(
                    text=text,
                    memory_type="conversation",
                    source="chat",
                    tags=["conversation"],
                    metadata=metadata,
                )
            except _STORAGE_ERRORS as exc:
                raise ExchangeStorageError("user turn", exc) from exc
            self._user_record, self._user_path = record, path

            try:
                if not session_exists(self.session_id):
                    create_session(self.session_id, conversation_title(text))
                    logger.info("[EXCHANGE] created conversation record")
            except _STORAGE_ERRORS as exc:
                raise ExchangeStorageError("conversation record", exc) from exc
        return True

    def ensure_user_indexed(self) -> None:
        """Schedule the user turn's index job, once per exchange."""
        with self._lock:
            if self._user_record is None or self._user_index_scheduled:
                return
            self._user_index_scheduled = True
            record, path = self._user_record, self._user_path
        self._schedule_index(record, path)

    def record_reply(
        self,
        text: str,
        *,
        metadata: Optional[dict] = None,
        tags: Optional[list[str]] = None,
    ) -> bool:
        """Write the assistant turn and finish the exchange.

        Returns False, writing nothing, when the exchange already finished.
        A failed write raises ExchangeStorageError and leaves the exchange
        unfinished, so the caller's failure path can still record an outcome.
        """
        if not self.enabled:
            return False
        with self._lock:
            if self._finished:
                logger.warning("[EXCHANGE] already finished: reply not recorded")
                return False
            reply_metadata = self._metadata("assistant", "answer")
            reply_metadata.update(metadata or {})
            with vault_binding(self.vault):
                try:
                    record, path = write_canonical_record(
                        text=text,
                        memory_type="conversation",
                        source="chat",
                        tags=tags or ["conversation"],
                        metadata=reply_metadata,
                    )
                except _STORAGE_ERRORS as exc:
                    raise ExchangeStorageError("reply", exc) from exc
            self._finished = True
        self._schedule_index(record, path)
        return True

    def record_outcome(self, outcome: str, reason: Optional[str] = None) -> bool:
        """Finish the exchange with an exchange outcome record.

        outcome is "failed" or "interrupted"; reason is an exception type name
        or a fixed label such as "vision_unavailable". Best effort: this runs
        on failure paths, so a write error is logged and swallowed rather than
        masking the failure that brought us here. Returns True only when the
        outcome record was written.
        """
        if not self.enabled:
            return False
        with self._lock:
            if self._finished:
                logger.warning(
                    "[EXCHANGE] already finished: %s outcome not recorded", outcome,
                )
                return False
            if self._user_record is None:
                # No stored user turn, so there is nothing to account for.
                return False
            try:
                with vault_binding(self.vault):
                    write_canonical_record(
                        text=f"Exchange {outcome}",
                        memory_type="system_event",
                        source="exchange_recorder",
                        tags=[EXCHANGE_OUTCOME_KIND],
                        metadata={
                            "kind": EXCHANGE_OUTCOME_KIND,
                            "session_id": self.session_id,
                            "exchange_id": self.exchange_id,
                            "outcome": outcome,
                            "reason": reason or "",
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("[EXCHANGE] outcome write failed: %s", type(exc).__name__)
                return False
            self._finished = True
        logger.info("[EXCHANGE] outcome=%s reason=%s", outcome, reason or "-")
        return True

    def record_failure(self, exc: BaseException) -> bool:
        """Finish a failed exchange: index the user turn, record `failed`.

        Used by the handler-level guard (plan v3 Q3). The user turn is
        indexed here when the failure came before retrieval finished, so a
        failed exchange never leaves its user turn unindexed until a rebuild.
        """
        reason = exc.cause_type if isinstance(exc, ExchangeStorageError) else type(exc).__name__
        self.ensure_user_indexed()
        return self.record_outcome(OUTCOME_FAILED, reason)
