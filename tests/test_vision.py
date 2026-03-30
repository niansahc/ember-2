"""
tests/test_vision.py

Unit tests for image analysis / vision model integration.

Covers:
- get_ember_vision_model(): env var set, unset, empty
- ContextPacket.image_data: default and populated
- ContextFormatter.format(): passes image_data through
- ContextService.build_context(): accepts and forwards image_data
- LLMAdapter._chat(): model_override, images kwarg, fallback to self.model
- LLMAdapter.generate_response(): routes to vision model when configured;
  falls back to text-only when EMBER_VISION_MODEL is unset
"""

import pytest
from unittest.mock import MagicMock, patch

from src.context.models import ContextPacket, ContextItem
from src.context.formatter import ContextFormatter


# ---------------------------------------------------------------------------
# get_ember_vision_model()
# ---------------------------------------------------------------------------

def test_vision_model_returns_value_when_set():
    with patch.dict("os.environ", {"EMBER_VISION_MODEL": "llama3.2-vision:11b"}):
        from importlib import reload
        import src.core.config as cfg
        reload(cfg)
        assert cfg.get_ember_vision_model() == "llama3.2-vision:11b"


def test_vision_model_returns_none_when_unset():
    # Patch os.getenv directly — reloading config re-runs load_dotenv() which
    # would pick up EMBER_VISION_MODEL from the .env file on disk.
    with patch("src.core.config.os.getenv", return_value=None):
        from src.core.config import get_ember_vision_model
        assert get_ember_vision_model() is None


def test_vision_model_returns_none_when_empty_string():
    with patch.dict("os.environ", {"EMBER_VISION_MODEL": ""}):
        from importlib import reload
        import src.core.config as cfg
        reload(cfg)
        assert cfg.get_ember_vision_model() is None


# ---------------------------------------------------------------------------
# ContextPacket.image_data
# ---------------------------------------------------------------------------

def test_context_packet_image_data_defaults_empty():
    packet = ContextPacket(user_message="hello")
    assert packet.image_data == []


def test_context_packet_image_data_accepts_list():
    packet = ContextPacket(user_message="hello", image_data=["base64data"])
    assert packet.image_data == ["base64data"]


def test_context_packet_image_data_independent_of_web_items():
    packet = ContextPacket(
        user_message="hello",
        web_items=[{"title": "t", "url": "u", "snippet": "s"}],
        image_data=["imgdata"],
    )
    assert len(packet.web_items) == 1
    assert len(packet.image_data) == 1


# ---------------------------------------------------------------------------
# ContextFormatter.format()
# ---------------------------------------------------------------------------

def test_formatter_passes_image_data_through():
    formatter = ContextFormatter()
    packet = formatter.format(
        user_message="describe this",
        memory_items=[],
        reflection_items=[],
        image_data=["base64abc"],
    )
    assert packet.image_data == ["base64abc"]


def test_formatter_image_data_defaults_empty():
    formatter = ContextFormatter()
    packet = formatter.format(
        user_message="hello",
        memory_items=[],
        reflection_items=[],
    )
    assert packet.image_data == []


def test_formatter_multiple_images():
    formatter = ContextFormatter()
    packet = formatter.format(
        user_message="describe these",
        memory_items=[],
        reflection_items=[],
        image_data=["img1", "img2", "img3"],
    )
    assert len(packet.image_data) == 3


# ---------------------------------------------------------------------------
# ContextService.build_context()
# ---------------------------------------------------------------------------

def test_service_passes_image_data_to_packet():
    from src.context.service import ContextService
    from src.context.retriever import ContextRetriever
    from src.context.ranker import ContextRanker
    from src.context.formatter import ContextFormatter

    mock_retriever = MagicMock(spec=ContextRetriever)
    mock_retriever.retrieve.return_value = ([], [], [], [])

    service = ContextService(
        retriever=mock_retriever,
        ranker=ContextRanker(),
        formatter=ContextFormatter(),
    )
    packet = service.build_context("describe this", image_data=["base64xyz"])
    assert packet.image_data == ["base64xyz"]


def test_service_image_data_defaults_empty():
    from src.context.service import ContextService
    from src.context.retriever import ContextRetriever
    from src.context.ranker import ContextRanker
    from src.context.formatter import ContextFormatter

    mock_retriever = MagicMock(spec=ContextRetriever)
    mock_retriever.retrieve.return_value = ([], [], [], [])

    service = ContextService(
        retriever=mock_retriever,
        ranker=ContextRanker(),
        formatter=ContextFormatter(),
    )
    packet = service.build_context("hello")
    assert packet.image_data == []


# ---------------------------------------------------------------------------
# LLMAdapter._chat()
# ---------------------------------------------------------------------------

def test_chat_uses_model_override():
    from src.llm.adapter import LLMAdapter

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "llama3.1:8b"

    with patch("src.llm.adapter.ollama.chat") as mock_chat:
        mock_chat.return_value = {"message": {"content": "response"}}
        adapter._chat(
            system_prompt="sys",
            user_message="hello",
            model_override="llama3.2-vision:11b",
        )
        call_kwargs = mock_chat.call_args
        assert call_kwargs[1]["model"] == "llama3.2-vision:11b" or call_kwargs[0][0] == "llama3.2-vision:11b"


def test_chat_falls_back_to_self_model_when_no_override():
    from src.llm.adapter import LLMAdapter

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "llama3.1:8b"

    with patch("src.llm.adapter.ollama.chat") as mock_chat:
        mock_chat.return_value = {"message": {"content": "response"}}
        adapter._chat(system_prompt="sys", user_message="hello")
        call_args = mock_chat.call_args
        model_used = call_args[1].get("model") or call_args[0][0]
        assert model_used == "llama3.1:8b"


def test_chat_passes_images_when_image_data_provided():
    from src.llm.adapter import LLMAdapter

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "llama3.1:8b"

    with patch("src.llm.adapter.ollama.chat") as mock_chat:
        mock_chat.return_value = {"message": {"content": "I see a cat"}}
        adapter._chat(
            system_prompt="sys",
            user_message="what do you see?",
            image_data=["base64imagedata"],
        )
        call_args = mock_chat.call_args
        messages = call_args[1].get("messages") or call_args[0][1]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "images" in user_msg
        assert user_msg["images"] == ["base64imagedata"]


def test_chat_omits_images_key_when_no_image_data():
    from src.llm.adapter import LLMAdapter

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "llama3.1:8b"

    with patch("src.llm.adapter.ollama.chat") as mock_chat:
        mock_chat.return_value = {"message": {"content": "hello"}}
        adapter._chat(system_prompt="sys", user_message="hello")
        call_args = mock_chat.call_args
        messages = call_args[1].get("messages") or call_args[0][1]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "images" not in user_msg


# ---------------------------------------------------------------------------
# LLMAdapter.generate_response() — vision routing
# ---------------------------------------------------------------------------

def _make_adapter_with_mock_chat(chat_response: str = "mock response"):
    """Build a minimal LLMAdapter with all dependencies mocked."""
    from src.llm.adapter import LLMAdapter
    from src.llm.prompt_builder import PromptBuilder
    from src.safety.policy_service import SafetyPolicyService
    from src.safety.review_service import ResponseReviewService
    from src.safety.review_logger import SafetyReviewLogger
    from src.safety.models import SafetyTriggerResult

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "llama3.1:8b"

    mock_builder = MagicMock(spec=PromptBuilder)
    mock_builder.build_prompt.return_value = "system prompt"
    mock_builder.conversation_buffer = MagicMock()

    mock_policy = MagicMock(spec=SafetyPolicyService)
    mock_policy.evaluate_trigger.return_value = SafetyTriggerResult(triggered=False, triggered_by=[])

    adapter.prompt_builder = mock_builder
    adapter.policy_service = mock_policy
    adapter.review_service = MagicMock(spec=ResponseReviewService)
    adapter.review_logger = MagicMock(spec=SafetyReviewLogger)
    adapter.memory_service = MagicMock()

    return adapter


def test_generate_response_uses_vision_model_when_image_present():
    adapter = _make_adapter_with_mock_chat()
    packet = ContextPacket(user_message="what's in this image?", image_data=["base64img"])

    with patch("src.llm.adapter.get_ember_vision_model", return_value="llama3.2-vision:11b"):
        with patch("src.llm.adapter.ollama.chat") as mock_chat:
            mock_chat.return_value = {"message": {"content": "I see a garden"}}
            adapter.generate_response(packet)

            # call_args_list[0] is the draft call — subsequent calls are buffer compression etc.
            first_call = mock_chat.call_args_list[0]
            model_used = first_call[1].get("model") or first_call[0][0]
            assert model_used == "llama3.2-vision:11b"

            messages = first_call[1].get("messages") or first_call[0][1]
            user_msg = next(m for m in messages if m["role"] == "user")
            assert "images" in user_msg


def test_generate_response_falls_back_to_text_when_no_vision_model():
    adapter = _make_adapter_with_mock_chat()
    packet = ContextPacket(user_message="what's in this image?", image_data=["base64img"])

    with patch("src.llm.adapter.get_ember_vision_model", return_value=None):
        with patch("src.llm.adapter.ollama.chat") as mock_chat:
            mock_chat.return_value = {"message": {"content": "I can't see images"}}
            adapter.generate_response(packet)

            first_call = mock_chat.call_args_list[0]
            model_used = first_call[1].get("model") or first_call[0][0]
            assert model_used == "llama3.1:8b"

            messages = first_call[1].get("messages") or first_call[0][1]
            user_msg = next(m for m in messages if m["role"] == "user")
            assert "images" not in user_msg


def test_generate_response_no_images_when_packet_has_no_image_data():
    adapter = _make_adapter_with_mock_chat()
    packet = ContextPacket(user_message="hello there")

    with patch("src.llm.adapter.get_ember_vision_model", return_value="llama3.2-vision:11b"):
        with patch("src.llm.adapter.ollama.chat") as mock_chat:
            mock_chat.return_value = {"message": {"content": "hello"}}
            adapter.generate_response(packet)

            first_call = mock_chat.call_args_list[0]
            model_used = first_call[1].get("model") or first_call[0][0]
            # Vision model configured but no image — should use text model
            assert model_used == "llama3.1:8b"
