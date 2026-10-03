"""
tests/test_log_exception_types.py

INFO-and-above log calls must record the exception TYPE only, never the
exception message. The rotating application log persists everything at INFO
and above, and exception messages can embed user text (a path, a parsed value,
an upstream response body).

Each test makes the underlying call raise an exception whose message holds a
sentinel, then asserts:
  - control: the site logged (the exception type name is in the message), and
  - the sentinel is absent from every captured log message.
The control means the absence cannot be satisfied by a site that logged nothing.
"""

import logging
from pathlib import Path
from unittest.mock import patch

SENTINEL = "SENTINEL-EXC-TEXT-5527"


def _messages(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_corrupt_session_file_logs_type_only(caplog):
    from src.memory import session

    with patch.object(session.storage, "list_memory_files", return_value=[Path("a.json")]), \
         patch.object(session.storage, "read_json", side_effect=OSError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        records = session._read_all_session_records()

    text = _messages(caplog)
    assert records == []
    assert "OSError" in text          # control: the site logged
    assert SENTINEL not in text


def test_keyring_read_failure_logs_type_only(caplog):
    from src.security import pin_service

    with patch("keyring.get_password", side_effect=RuntimeError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        result = pin_service._get_keyring("ember-test-service")

    text = _messages(caplog)
    assert result is None
    assert "RuntimeError" in text     # control: the site logged
    assert SENTINEL not in text


def test_lodestone_embedding_failure_logs_type_only(caplog):
    from src.context import lodestone_resolver

    with patch.object(lodestone_resolver, "read_active", return_value=[{"value": "x"}]), \
         patch.object(lodestone_resolver, "embed_text", side_effect=ConnectionError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        result = lodestone_resolver.resolve("a synthetic query")

    text = _messages(caplog)
    assert result == []
    assert "ConnectionError" in text  # control: the site logged
    assert SENTINEL not in text


def test_grounding_outcome_log_failure_logs_type_only(caplog):
    from src.safety import grounding_check

    with patch.object(Path, "mkdir", side_effect=PermissionError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        grounding_check.log_grounding_outcome(
            intent_class="default", triggered=True, grounded=True, revision_triggered=False,
        )

    text = _messages(caplog)
    assert "PermissionError" in text  # control: the site logged
    assert SENTINEL not in text


def test_self_narrative_outcome_log_failure_logs_type_only(caplog):
    from src.safety import self_narrative_check

    with patch.object(Path, "mkdir", side_effect=PermissionError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        self_narrative_check.log_self_narrative_outcome(1, [0])

    text = _messages(caplog)
    assert "PermissionError" in text  # control: the site logged
    assert SENTINEL not in text


def test_web_search_failure_logs_type_only(caplog):
    from src.tools import web_search

    with patch.object(web_search.requests, "get", side_effect=RuntimeError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        results = web_search.web_search("a synthetic query")

    text = _messages(caplog)
    assert results == []
    assert "RuntimeError" in text     # control: the site logged
    assert SENTINEL not in text


def test_generation_failure_line_is_the_one_that_keeps_its_traceback():
    # The only intended exception: [GENERATION] failed keeps exc_info. This
    # pins that it is still the only call in the sweep that does.
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    carrying = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"exc_info\s*=\s*(exc|e)\b", text):
            carrying.append(path.name)
    assert carrying == ["openai_adapter.py"]   # control: the intended one is found


def test_openai_adapter_commitment_failure_logs_type_only(caplog):
    from src.api import openai_adapter as oa

    with patch("src.state.commitment_detector.detect_commitment",
               side_effect=ValueError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        oa._detect_and_write_commitment("a reply", "sess_test_exc_001")
    text = _messages(caplog)
    assert "ValueError" in text      # control: the site logged
    assert SENTINEL not in text


def test_openai_adapter_task_detection_failure_logs_type_only(caplog):
    from src.api import openai_adapter as oa

    with patch("src.tasks.task_detector.detect_task",
               side_effect=ValueError(SENTINEL)), \
         caplog.at_level(logging.INFO):
        oa._detect_task_in_response("a reply", "sess_test_exc_002")
    text = _messages(caplog)
    assert "ValueError" in text      # control: the site logged
    assert SENTINEL not in text
