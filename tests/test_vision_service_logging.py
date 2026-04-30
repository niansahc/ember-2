"""tests/test_vision_service_logging.py

File-logging tests for VisionService. Verifies that logs/vision/ entries
are written for all five event types and that log-write failures never
break vision (analyze() must always return normally).

The log dir is patched onto a tmp_path per-test so the real logs/vision/
directory is never touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.llm import vision_service as vs


@pytest.fixture
def vision_log_dir(tmp_path: Path, monkeypatch):
    """Patch the module-level _LOG_DIR onto a tmp directory for the test.
    Returns the patched path so tests can read the log file."""
    log_dir = tmp_path / "logs" / "vision"
    monkeypatch.setattr(vs, "_LOG_DIR", log_dir)
    return log_dir


def _today_log(log_dir: Path) -> Path:
    return log_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"


def _read_events(log_dir: Path) -> list[dict]:
    log_file = _today_log(log_dir)
    if not log_file.exists():
        return []
    return [json.loads(ln) for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------


def test_log_dir_is_created_on_first_analyze_call(vision_log_dir, monkeypatch):
    """logs/vision/ is created lazily on the first event write — no
    directory pre-creation at module import time."""
    assert not vision_log_dir.exists()

    # Stub ollama.chat so analyze() doesn't try to hit a real model.
    monkeypatch.setattr(
        vs.ollama, "chat",
        lambda **kwargs: {"message": {"content": "stub description"}},
    )
    service = vs.VisionService(model="qwen3-vl:8b")
    service.analyze(["fake_b64_image"])

    assert vision_log_dir.exists()
    assert _today_log(vision_log_dir).exists()


# ---------------------------------------------------------------------------
# Event-type coverage
# ---------------------------------------------------------------------------


def test_entry_and_success_events_written_on_success(vision_log_dir, monkeypatch):
    """A successful analyze() emits vision_entry, vision_ollama_call, and
    vision_success — and the success entry carries the description preview."""
    monkeypatch.setattr(
        vs.ollama, "chat",
        lambda **kwargs: {"message": {"content": "A clear photograph of a sunset over the ocean."}},
    )
    service = vs.VisionService(model="qwen3-vl:8b")
    service.analyze(["fake_b64_image"])

    events = _read_events(vision_log_dir)
    event_names = [e["event"] for e in events]
    assert "vision_entry" in event_names
    assert "vision_ollama_call" in event_names
    assert "vision_success" in event_names

    entry = next(e for e in events if e["event"] == "vision_entry")
    assert entry["model"] == "qwen3-vl:8b"
    assert entry["image_count"] == 1

    ollama_call = next(e for e in events if e["event"] == "vision_ollama_call")
    assert ollama_call["model"] == "qwen3-vl:8b"
    assert ollama_call["num_predict"] == vs.VISION_MAX_TOKENS

    success = next(e for e in events if e["event"] == "vision_success")
    assert success["description_chars"] == len("A clear photograph of a sunset over the ocean.")
    # First-80-chars preview as specified
    assert success["description_preview"] == "A clear photograph of a sunset over the ocean."[:80]


def test_failure_event_written_when_ollama_raises(vision_log_dir, monkeypatch):
    """When ollama.chat raises, a vision_failure event is logged with the
    exception type and message — and analyze() returns "" (no propagation)."""
    def _boom(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(vs.ollama, "chat", _boom)
    service = vs.VisionService(model="qwen3-vl:8b")
    result = service.analyze(["fake_b64_image"])

    assert result == ""

    events = _read_events(vision_log_dir)
    failure = next((e for e in events if e["event"] == "vision_failure"), None)
    assert failure is not None
    assert failure["exception_type"] == "RuntimeError"
    assert failure["exception_message"] == "model unavailable"


def test_empty_input_event_written_when_no_images(vision_log_dir):
    """Empty image_data short-circuits before ollama.chat. The vision_empty_input
    event is the only one written; vision_entry / vision_ollama_call must NOT
    appear."""
    service = vs.VisionService(model="qwen3-vl:8b")
    result = service.analyze([])

    assert result == ""

    events = _read_events(vision_log_dir)
    event_names = [e["event"] for e in events]
    assert "vision_empty_input" in event_names
    assert "vision_entry" not in event_names
    assert "vision_ollama_call" not in event_names


# ---------------------------------------------------------------------------
# Log-write failure must not break vision
# ---------------------------------------------------------------------------


def test_log_write_failure_does_not_raise(vision_log_dir, monkeypatch):
    """If the log file cannot be written (read-only disk, full volume,
    permissions), analyze() must still return the model output normally.
    Logging is a diagnostic side effect, never a hard dependency."""
    # Force every log write to fail by making the dir creation raise.
    def _no_write(*args, **kwargs):
        raise OSError("disk full")

    # Patching _LOG_DIR.mkdir would require deeper plumbing; patching
    # Path.mkdir at the class level affects all tests. Instead patch the
    # module-level logger sink: replace _LOG_DIR with a path whose mkdir
    # raises by pointing it at a path that cannot be created.
    monkeypatch.setattr(vs.Path, "mkdir", _no_write)

    monkeypatch.setattr(
        vs.ollama, "chat",
        lambda **kwargs: {"message": {"content": "stub"}},
    )
    service = vs.VisionService(model="qwen3-vl:8b")
    # Must not raise even though every _log_vision call fails internally.
    result = service.analyze(["fake_b64_image"])
    assert result == "stub"


def test_log_write_failure_on_failure_path_does_not_mask_return(vision_log_dir, monkeypatch):
    """Companion to the above: when ollama raises AND log writes fail,
    analyze() must still return "" (the failure path), not propagate either
    the model error or the log-write error."""
    def _no_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(vs.Path, "mkdir", _no_write)

    def _boom(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(vs.ollama, "chat", _boom)
    service = vs.VisionService(model="qwen3-vl:8b")
    assert service.analyze(["fake_b64_image"]) == ""
