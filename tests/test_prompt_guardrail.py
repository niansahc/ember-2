"""
tests/test_prompt_guardrail.py

Unit tests for src/llm/prompt_guardrail.py. All fixtures are synthetic
(no vault data per project rule). A stub builder controls prompt size
deterministically so each cascade step can be exercised in isolation.
"""
from __future__ import annotations

from src.context.models import ContextItem, ContextPacket
from src.llm.prompt_guardrail import (
    OLLAMA_NUM_PREDICT,
    _STATE_PROTECTED_CATEGORIES,
    estimate_tokens,
    get_input_budget,
    trim_to_fit,
)
from src.state.models import StateItem
from src.tasks.models import TaskItem


# ---------------------------------------------------------------------------
# Stub builder
# ---------------------------------------------------------------------------

class StubBuilder:
    """Returns a deterministic string whose length scales with packet size.

    Formula:
      static_layer_chars
      + sum(item.content for memory_items)
      + sum(item.content for reflection_items)
      + sum(s.text for state_items)
      + sum(t.text for task_items)
      + sum(item.snippet for web_items)
      + (lodestone_chars unless suppress_lodestone_living=True)
    """

    def __init__(self, static_chars: int = 1000, lodestone_chars: int = 500) -> None:
        self.static_chars = static_chars
        self.lodestone_chars = lodestone_chars
        self.calls: list[dict] = []

    def build_prompt(self, packet: ContextPacket, **kwargs) -> str:
        self.calls.append({"packet_id": id(packet), **kwargs})
        chars = self.static_chars
        chars += sum(len(getattr(m, "content", "")) for m in packet.memory_items)
        chars += sum(len(getattr(r, "content", "")) for r in packet.reflection_items)
        chars += sum(len(s.text) for s in packet.state_items)
        chars += sum(len(getattr(t, "title", "")) for t in packet.task_items)
        chars += sum(len(item.get("snippet", "")) for item in packet.web_items)
        if not kwargs.get("suppress_lodestone_living"):
            chars += self.lodestone_chars
        return "X" * chars


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ctx_item(content: str, score: float = 0.5, memory_type: str | None = None) -> ContextItem:
    return ContextItem(
        id=f"id-{score}",
        content=content,
        source="synthetic",
        item_type="memory",
        score=score,
        memory_type=memory_type,
    )


def _state_item(category: str, text: str, timestamp: str = "2026-01-01T00-00-00") -> StateItem:
    return StateItem(category=category, text=text, timestamp=timestamp)


def _task_item(title: str) -> TaskItem:
    return TaskItem(id="task-1", title=title, status="active")


def _packet(**overrides) -> ContextPacket:
    base = dict(
        user_message="hello",
        memory_items=[],
        reflection_items=[],
        state_items=[],
        task_items=[],
        web_items=[],
    )
    base.update(overrides)
    return ContextPacket(**base)


# ---------------------------------------------------------------------------
# 1. estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:

    def test_empty_string_returns_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_ascii_returns_floor_of_len_over_three(self) -> None:
        assert estimate_tokens("abcdefghi") == 3
        assert estimate_tokens("abcdefghij") == 3
        assert estimate_tokens("abcdefghijkl") == 4

    def test_multibyte_counts_python_characters(self) -> None:
        assert estimate_tokens("ééé") == 1


# ---------------------------------------------------------------------------
# 2. get_input_budget
# ---------------------------------------------------------------------------

class TestGetInputBudget:

    def test_applies_5_percent_headroom_above_floor(self) -> None:
        assert get_input_budget(num_ctx=32768, num_predict=2048) == int((32768 - 2048) * 0.95)

    def test_default_num_predict_matches_module_constant(self) -> None:
        explicit = get_input_budget(num_ctx=32768, num_predict=OLLAMA_NUM_PREDICT)
        default = get_input_budget(num_ctx=32768)
        assert explicit == default

    def test_sanity_floor_applies_at_1024(self) -> None:
        assert get_input_budget(num_ctx=2048, num_predict=2048) == 1024
        assert get_input_budget(num_ctx=512, num_predict=2048) == 1024


# ---------------------------------------------------------------------------
# 3. trim_to_fit no-op path
# ---------------------------------------------------------------------------

class TestTrimNoOp:

    def test_under_budget_returns_unchanged_packet(self) -> None:
        builder = StubBuilder(static_chars=300)
        packet = _packet(memory_items=[_ctx_item("short")])
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=32768,
            builder=builder,
            build_kwargs={},
        )
        assert log["sections_dropped"] == []
        assert log["iterations"] == 0
        assert log["overflow"] is False
        assert returned.memory_items == packet.memory_items
        assert len(builder.calls) == 1


# ---------------------------------------------------------------------------
# 4-9. Cascade steps
# ---------------------------------------------------------------------------

class TestCascade:

    # Cascade tests use num_ctx=8192 throughout, giving:
    #   budget = (8192 - 2048) * 0.95 = 5836 tokens = 17508 chars.
    # Each test sizes its inputs so the cascade triggers at the
    # intended step and the post-trim prompt fits the budget.

    def test_step1_drops_web_items_when_sufficient(self) -> None:
        builder = StubBuilder(static_chars=500, lodestone_chars=500)
        packet = _packet(web_items=[{"snippet": "Z" * 20_000}])
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=8192,
            builder=builder,
            build_kwargs={},
        )
        assert log["sections_dropped"] == ["web_items"]
        assert returned.web_items == []

    def test_step2_invokes_buffer_compress_callback(self) -> None:
        # Web empty; static + lodestone alone tip over the budget.
        # Callback "shrinks" the static portion so step 2 succeeds.
        builder = StubBuilder(static_chars=20_000, lodestone_chars=500)
        packet = _packet(web_items=[])
        compress_calls: list[int] = []

        def cb() -> None:
            compress_calls.append(1)
            builder.static_chars = 1_000

        prompt, _, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=8192,
            builder=builder,
            build_kwargs={},
            buffer_compress_callback=cb,
        )
        assert log["sections_dropped"] == ["buffer_compress"]
        assert compress_calls == [1]

    def test_step3_drops_task_items(self) -> None:
        builder = StubBuilder(static_chars=500, lodestone_chars=500)
        tasks = [_task_item("T" * 7_000) for _ in range(3)]   # 21k chars
        packet = _packet(task_items=tasks)
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=8192,
            builder=builder,
            build_kwargs={},
        )
        assert log["sections_dropped"] == ["task_items"]
        assert returned.task_items == []

    def test_step4_drops_state_items_keeping_protected_categories(self) -> None:
        builder = StubBuilder(static_chars=500, lodestone_chars=500)
        states = [
            _state_item("current_focus", "K" * 200),         # protected
            _state_item("open_loop", "K" * 200),              # protected
            _state_item("blocker", "K" * 200),                # protected
            _state_item("next_action", "K" * 200),            # protected
            _state_item("pending_confirmation", "K" * 200),   # protected
            _state_item("decision", "Z" * 12_000),            # NOT protected
            _state_item("note", "Z" * 12_000),                # NOT protected
        ]
        packet = _packet(state_items=states)
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=8192,
            builder=builder,
            build_kwargs={},
        )
        assert log["sections_dropped"] == ["state_items_unprotected"]
        kept_categories = {s.category for s in returned.state_items}
        assert kept_categories <= _STATE_PROTECTED_CATEGORIES
        assert kept_categories == _STATE_PROTECTED_CATEGORIES

    def test_step5_drops_memory_items_keeping_profile(self) -> None:
        builder = StubBuilder(static_chars=500, lodestone_chars=500)
        items = [
            _ctx_item("P" * 100, score=0.9, memory_type="profile"),
            _ctx_item("M" * 12_000, score=0.5, memory_type="conversation"),
            _ctx_item("M" * 12_000, score=0.3, memory_type="ingested"),
        ]
        packet = _packet(memory_items=items)
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=8192,
            builder=builder,
            build_kwargs={},
        )
        assert log["sections_dropped"] == ["memory_items_non_profile"]
        kept_types = {m.memory_type for m in returned.memory_items}
        assert kept_types == {"profile"}

    def test_step6_suppresses_lodestone_living_via_kwargs(self) -> None:
        # Static layer alone is under budget; lodestone alone tips over.
        # Suppression at step 6 brings total back under.
        builder = StubBuilder(static_chars=500, lodestone_chars=20_000)
        packet = _packet()
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=8192,
            builder=builder,
            build_kwargs={},
        )
        assert log["sections_dropped"] == ["lodestone_living"]
        last_call = builder.calls[-1]
        assert last_call.get("suppress_lodestone_living") is True


# ---------------------------------------------------------------------------
# 10. Reflection inviolability
# ---------------------------------------------------------------------------

class TestReflectionInviolable:

    def test_reflection_items_never_dropped_through_full_cascade(self) -> None:
        builder = StubBuilder(static_chars=2000, lodestone_chars=4000)
        reflection = _ctx_item("R" * 1000, score=0.8)
        # Force the cascade to exhaust by piling on every trimmable category.
        packet = _packet(
            reflection_items=[reflection],
            web_items=[{"snippet": "W" * 2000}],
            task_items=[_task_item("T" * 2000)],
            state_items=[_state_item("note", "S" * 2000)],
            memory_items=[_ctx_item("M" * 2000, score=0.1, memory_type="conversation")],
        )
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=4096,
            builder=builder,
            build_kwargs={},
        )
        assert returned.reflection_items == [reflection]


# ---------------------------------------------------------------------------
# 11. Fail-open
# ---------------------------------------------------------------------------

class TestFailOpen:

    def test_fail_open_returns_overflow_telemetry(self) -> None:
        # Static layer alone exceeds budget; nothing trimmable can recover it.
        builder = StubBuilder(static_chars=20_000, lodestone_chars=0)
        packet = _packet()
        prompt, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=4096,
            builder=builder,
            build_kwargs={},
        )
        assert log["overflow"] is True
        assert log["per_section_estimates"] is not None
        assert log["per_section_estimates"]["static_layer"] > 0
        # A prompt is still returned even though it overflows.
        assert isinstance(prompt, str) and len(prompt) > 0


# ---------------------------------------------------------------------------
# 12. Telemetry shape
# ---------------------------------------------------------------------------

class TestTelemetryShape:

    def test_keys_present_on_under_budget_path(self) -> None:
        builder = StubBuilder(static_chars=200)
        prompt, _, log = trim_to_fit(
            packet=_packet(),
            model="qwen3:8b",
            num_ctx=32768,
            builder=builder,
            build_kwargs={},
        )
        for key in (
            "model", "budget", "initial_estimate", "final_estimate",
            "sections_dropped", "iterations", "overflow",
            "per_section_estimates",
        ):
            assert key in log
        # per_section_estimates is None below the overflow path.
        assert log["per_section_estimates"] is None

    def test_per_section_estimates_only_on_overflow(self) -> None:
        # Trigger trim but not overflow.
        builder = StubBuilder(static_chars=500)
        packet = _packet(web_items=[{"snippet": "Z" * 4000}])
        _, _, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=4096,
            builder=builder,
            build_kwargs={},
        )
        assert log["overflow"] is False
        assert log["per_section_estimates"] is None


# ---------------------------------------------------------------------------
# 13. Original packet not mutated
# ---------------------------------------------------------------------------

class TestOriginalNotMutated:

    def test_original_lists_remain_intact_after_cascade(self) -> None:
        builder = StubBuilder(static_chars=500)
        original_web = [{"snippet": "Z" * 4000}]
        original_tasks = [_task_item("T" * 500)]
        original_states = [_state_item("note", "Z" * 4000)]
        packet = _packet(
            web_items=list(original_web),
            task_items=list(original_tasks),
            state_items=list(original_states),
        )
        web_id_before = id(packet.web_items)
        tasks_id_before = id(packet.task_items)
        states_id_before = id(packet.state_items)

        _, returned, log = trim_to_fit(
            packet=packet,
            model="qwen3:8b",
            num_ctx=4096,
            builder=builder,
            build_kwargs={},
        )

        # Trim happened on the clone; original packet untouched.
        assert id(packet.web_items) == web_id_before
        assert id(packet.task_items) == tasks_id_before
        assert id(packet.state_items) == states_id_before
        assert packet.web_items == original_web
        assert packet.task_items == original_tasks
        assert packet.state_items == original_states
        # The returned packet IS the trimmed clone (a different object).
        assert returned is not packet
