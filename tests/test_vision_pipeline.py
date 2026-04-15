"""
tests/test_vision_pipeline.py

Tests for the vision preprocessor pipeline:
- VisionService.analyze() — image-to-text via vision model
- PromptBuilder._build_vision_context_section() — XML rendering
- Vision description integration into assembled prompt
"""

from unittest.mock import patch, MagicMock

import pytest

from src.llm.vision_service import VisionService, VISION_MAX_TOKENS, VISION_PROMPT
from src.llm.prompt_builder import PromptBuilder
from src.context.models import ContextPacket


# ---------------------------------------------------------------------------
# VisionService.analyze()
# ---------------------------------------------------------------------------

class TestVisionServiceAnalyze:
    """VisionService.analyze() with mocked ollama.chat."""

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_returns_description(self, mock_chat):
        """analyze() returns the text description from ollama response."""
        mock_chat.return_value = {
            "message": {"content": "A photo of a cat sitting on a keyboard."}
        }
        service = VisionService(model="test-vision:latest")
        result = service.analyze(["base64encodeddata"])

        assert result == "A photo of a cat sitting on a keyboard."
        mock_chat.assert_called_once()

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_returns_empty_on_failure(self, mock_chat):
        """analyze() returns empty string when ollama raises an exception."""
        mock_chat.side_effect = Exception("Connection refused")
        service = VisionService()
        result = service.analyze(["base64encodeddata"])

        assert result == ""

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_returns_empty_for_empty_input(self, mock_chat):
        """analyze() returns empty string when no images are provided."""
        service = VisionService()
        result = service.analyze([])

        assert result == ""
        mock_chat.assert_not_called()

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_strips_whitespace(self, mock_chat):
        """analyze() strips leading/trailing whitespace from the description."""
        mock_chat.return_value = {
            "message": {"content": "  A screenshot with text.  \n"}
        }
        service = VisionService()
        result = service.analyze(["img1"])

        assert result == "A screenshot with text."

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_passes_num_predict_300(self, mock_chat):
        """analyze() passes num_predict=300 to ollama.chat options."""
        mock_chat.return_value = {
            "message": {"content": "Description text."}
        }
        service = VisionService(model="test-vision:latest")
        service.analyze(["base64data"])

        call_kwargs = mock_chat.call_args
        options = call_kwargs.kwargs.get("options") or call_kwargs[1].get("options")
        assert options["num_predict"] == VISION_MAX_TOKENS
        assert VISION_MAX_TOKENS == 300

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_sends_vision_prompt(self, mock_chat):
        """analyze() sends the VISION_PROMPT as the user message content."""
        mock_chat.return_value = {
            "message": {"content": "Some description."}
        }
        service = VisionService()
        service.analyze(["img_data_1", "img_data_2"])

        call_kwargs = mock_chat.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        assert len(messages) == 1
        assert messages[0]["content"] == VISION_PROMPT
        assert messages[0]["images"] == ["img_data_1", "img_data_2"]

    @patch("src.llm.vision_service.ollama.chat")
    def test_analyze_uses_configured_model(self, mock_chat):
        """analyze() uses the model specified at construction time."""
        mock_chat.return_value = {
            "message": {"content": "Description."}
        }
        service = VisionService(model="custom-vision:7b")
        service.analyze(["img"])

        call_kwargs = mock_chat.call_args
        model_arg = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert model_arg == "custom-vision:7b"


# ---------------------------------------------------------------------------
# PromptBuilder._build_vision_context_section()
# ---------------------------------------------------------------------------

class TestBuildVisionContextSection:
    """Static method _build_vision_context_section() XML rendering."""

    def test_returns_xml_section_with_description(self):
        """Non-empty description returns XML-tagged section with header."""
        result = PromptBuilder._build_vision_context_section(
            "A photo of a sunset over the ocean."
        )
        # v0.16.0-dev: opening tag now carries provenance attribute, and
        # the header is reframed as first-person observation to counter
        # the RLHF "I can't see images" prior (UAT-120 / task #18).
        assert "<vision_context" in result
        assert 'provenance="third-party-content"' in result
        assert "</vision_context>" in result
        assert "[You have analyzed this image. Your observations:]" in result
        assert "A photo of a sunset over the ocean." in result

    def test_returns_empty_for_none(self):
        """None input returns empty string (section omitted)."""
        result = PromptBuilder._build_vision_context_section(None)
        assert result == ""

    def test_returns_empty_for_empty_string(self):
        """Empty string input returns empty string (section omitted)."""
        result = PromptBuilder._build_vision_context_section("")
        assert result == ""

    def test_returns_empty_for_whitespace_only(self):
        """Whitespace-only input returns empty string (section omitted)."""
        result = PromptBuilder._build_vision_context_section("   \n  ")
        assert result == ""

    def test_strips_description_whitespace(self):
        """Description whitespace is stripped in the rendered section."""
        result = PromptBuilder._build_vision_context_section("  trimmed text  ")
        assert "trimmed text" in result
        # Verify no leading/trailing whitespace around the description line
        assert "  trimmed text  " not in result


# ---------------------------------------------------------------------------
# Vision description integration in build_prompt()
# ---------------------------------------------------------------------------

class TestVisionInBuildPrompt:
    """Vision description appears/omitted in assembled prompt via build_prompt()."""

    def _make_minimal_packet(self, user_message: str = "Describe this image") -> ContextPacket:
        """Create a minimal ContextPacket for prompt assembly tests."""
        return ContextPacket(user_message=user_message)

    @patch.object(PromptBuilder, "_build_nature_section", return_value="")
    @patch.object(PromptBuilder, "_build_identity_rules_section", return_value="")
    @patch.object(PromptBuilder, "_build_lodestone_seed_section", return_value="")
    @patch.object(PromptBuilder, "_build_lodestone_living_section", return_value="")
    def test_vision_description_appears_in_prompt(self, *_mocks):
        """When vision_description is provided, it appears in the assembled prompt."""
        builder = PromptBuilder()
        packet = self._make_minimal_packet()
        prompt = builder.build_prompt(
            packet,
            vision_description="A screenshot showing an error message: KeyError",
        )

        # v0.16.0-dev: opening tag carries provenance, header is reframed.
        assert "<vision_context" in prompt
        assert 'provenance="third-party-content"' in prompt
        assert "A screenshot showing an error message: KeyError" in prompt
        assert "[You have analyzed this image. Your observations:]" in prompt

    @patch.object(PromptBuilder, "_build_nature_section", return_value="")
    @patch.object(PromptBuilder, "_build_identity_rules_section", return_value="")
    @patch.object(PromptBuilder, "_build_lodestone_seed_section", return_value="")
    @patch.object(PromptBuilder, "_build_lodestone_living_section", return_value="")
    def test_vision_description_absent_when_not_provided(self, *_mocks):
        """When vision_description is None, no vision section appears in prompt."""
        builder = PromptBuilder()
        packet = self._make_minimal_packet()
        prompt = builder.build_prompt(packet, vision_description=None)

        assert "<vision_context>" not in prompt
        assert "[Image attached by user" not in prompt

    @patch.object(PromptBuilder, "_build_nature_section", return_value="")
    @patch.object(PromptBuilder, "_build_identity_rules_section", return_value="")
    @patch.object(PromptBuilder, "_build_lodestone_seed_section", return_value="")
    @patch.object(PromptBuilder, "_build_lodestone_living_section", return_value="")
    def test_vision_description_absent_when_empty(self, *_mocks):
        """When vision_description is empty string, no vision section appears."""
        builder = PromptBuilder()
        packet = self._make_minimal_packet()
        prompt = builder.build_prompt(packet, vision_description="")

        assert "<vision_context>" not in prompt
