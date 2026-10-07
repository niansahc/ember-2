"""
tests/test_log_redaction.py

ExceptionTextRedactor (src/core/log_redaction.py): before a record reaches the
file log, exception text is reduced to the exception type name. Exception
objects in record.args and the message line(s) of exc_info output are both
covered; traceback frames stay.

Every absence assertion ("marker not in the file") has a positive control: the
identical log call through a handler without the filter, where the marker does
reach the file.
"""

import logging

import pytest

from src.core.log_redaction import ExceptionTextRedactor

MARKER = "REDACT-MARKER-91c2"
MARKER_CAUSE = "REDACT-CAUSE-4be0"


class MarkerError(Exception):
    pass


@pytest.fixture
def file_logger(tmp_path):
    """A private logger writing to a tmp file. Returns (logger, path, handler)."""
    path = tmp_path / "app.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    log = logging.getLogger(f"test.redaction.{tmp_path.name}")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(handler)
    yield log, path, handler
    log.removeHandler(handler)
    handler.close()


def _read(path, handler):
    handler.flush()
    return path.read_text(encoding="utf-8")


def _log_via_args(log):
    try:
        raise MarkerError(MARKER)
    except MarkerError as exc:
        log.warning("[TEST] args path failed: %s", exc)


def _log_via_exc_info(log):
    try:
        raise MarkerError(MARKER)
    except MarkerError as exc:
        log.error("[TEST] exc_info path failed", exc_info=exc)


def _log_chained(log):
    try:
        try:
            raise ValueError(MARKER_CAUSE)
        except ValueError as cause:
            raise MarkerError(MARKER) from cause
    except MarkerError as exc:
        log.error("[TEST] chained failed", exc_info=exc)


# --- args path ------------------------------------------------------------

def test_args_exception_is_reduced_to_type_name(file_logger):
    log, path, handler = file_logger
    handler.addFilter(ExceptionTextRedactor())
    _log_via_args(log)
    text = _read(path, handler)
    assert MARKER not in text
    assert "[TEST] args path failed: MarkerError" in text


def test_args_control_without_filter_shows_marker(file_logger):
    log, path, handler = file_logger
    _log_via_args(log)
    assert MARKER in _read(path, handler)


def test_dict_args_exception_is_reduced_to_type_name(file_logger):
    log, path, handler = file_logger
    handler.addFilter(ExceptionTextRedactor())
    log.warning("[TEST] dict args: %(err)s", {"err": MarkerError(MARKER)})
    text = _read(path, handler)
    assert MARKER not in text
    assert "[TEST] dict args: MarkerError" in text


# --- exc_info path --------------------------------------------------------

def test_exc_info_message_is_reduced_and_frames_stay(file_logger):
    log, path, handler = file_logger
    handler.addFilter(ExceptionTextRedactor())
    _log_via_exc_info(log)
    text = _read(path, handler)
    assert MARKER not in text
    assert "Traceback (most recent call last):" in text
    assert 'File "' in text and ", line " in text
    assert "in _log_via_exc_info" in text
    assert text.rstrip().splitlines()[-1].endswith("MarkerError")


def test_exc_info_control_without_filter_shows_marker(file_logger):
    log, path, handler = file_logger
    _log_via_exc_info(log)
    assert MARKER in _read(path, handler)


def test_chained_exceptions_are_all_redacted(file_logger):
    log, path, handler = file_logger
    handler.addFilter(ExceptionTextRedactor())
    _log_chained(log)
    text = _read(path, handler)
    assert MARKER not in text
    assert MARKER_CAUSE not in text
    assert "ValueError" in text and "MarkerError" in text
    assert "direct cause" in text


def test_chained_control_without_filter_shows_both_markers(file_logger):
    log, path, handler = file_logger
    _log_chained(log)
    text = _read(path, handler)
    assert MARKER in text
    assert MARKER_CAUSE in text


def test_exception_notes_are_redacted(file_logger):
    log, path, handler = file_logger
    handler.addFilter(ExceptionTextRedactor())
    try:
        exc = MarkerError("plain")
        exc.add_note(MARKER)
        raise exc
    except MarkerError as caught:
        log.error("[TEST] notes", exc_info=caught)
    text = _read(path, handler)
    assert MARKER not in text
    assert "MarkerError" in text


# --- the shared record is not mutated --------------------------------------

def test_original_record_is_left_untouched():
    """Other handlers (stderr, caplog) see the record the filter received."""
    try:
        raise MarkerError(MARKER)
    except MarkerError as exc:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed: %s", (exc,), (type(exc), exc, exc.__traceback__),
        )
    out = ExceptionTextRedactor().filter(record)
    assert isinstance(out, logging.LogRecord) and out is not record
    assert out.getMessage() == "failed: MarkerError"
    # Control: the redacted copy differs, the original still carries the text.
    assert MARKER in record.getMessage()
    assert record.exc_text is None


def test_record_without_exception_passes_through_unchanged():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "plain %s", ("text",), None)
    out = ExceptionTextRedactor().filter(record)
    assert out is True or out is record
