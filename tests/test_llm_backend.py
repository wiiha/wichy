"""
Test cases for llm_backend module functions.
"""

import threading
import time
import pytest
from unittest.mock import MagicMock, patch

from wichy.llm_backend import (
    parse_generic_backend,
    LLMResponse,
    Message,
    call,
    _get_backend_semaphore,
    _reset_backend_semaphore,
    LLMBackendUnhandledException,
)


class TestParseGenericBackend:
    """Tests for parse_generic_backend function."""

    # Basic parsing tests
    def test_localhost_with_port(self):
        result = parse_generic_backend("generic/localhost:8080##llama-3")
        assert result == ("http://localhost:8080/v1", "llama-3")

    def test_localhost_no_port(self):
        result = parse_generic_backend("generic/localhost##model-name")
        assert result == ("http://localhost/v1", "model-name")

    def test_remote_host_https(self):
        result = parse_generic_backend("generic/api.myservice.com##gpt-4")
        assert result == ("https://api.myservice.com/v1", "gpt-4")

    def test_remote_host_with_port(self):
        result = parse_generic_backend("generic/api.example.com:9000##my-model")
        assert result == ("https://api.example.com:9000/v1", "my-model")

    # Local/private IP tests (should use http)
    def test_127_loopback(self):
        result = parse_generic_backend("generic/127.0.0.1:8080##test-model")
        assert result == ("http://127.0.0.1:8080/v1", "test-model")

    def test_192_168_private_ip(self):
        result = parse_generic_backend("generic/192.168.1.10:9000##my-model")
        assert result == ("http://192.168.1.10:9000/v1", "my-model")

    def test_10_private_ip(self):
        result = parse_generic_backend("generic/10.0.0.5##internal-model")
        assert result == ("http://10.0.0.5/v1", "internal-model")

    def test_172_private_ip(self):
        result = parse_generic_backend("generic/172.16.0.1##private-model")
        assert result == ("http://172.16.0.1/v1", "private-model")

    # Model name variations
    def test_model_with_slashes(self):
        result = parse_generic_backend("generic/api.openai.com##org/model-name")
        assert result == ("https://api.openai.com/v1", "org/model-name")

    def test_model_with_multiple_slashes(self):
        result = parse_generic_backend("generic/api.provider.com##org/team/model-v2")
        assert result == ("https://api.provider.com/v1", "org/team/model-v2")

    def test_model_with_dashes_and_underscores(self):
        result = parse_generic_backend("generic/myserver.com##my_awesome-model_v2")
        assert result == ("https://myserver.com/v1", "my_awesome-model_v2")

    def test_model_with_colon_version(self):
        result = parse_generic_backend("generic/localhost:8080##llama-3:8b")
        assert result == ("http://localhost:8080/v1", "llama-3:8b")

    # Whitespace handling
    def test_leading_trailing_whitespace(self):
        result = parse_generic_backend("  generic/localhost:8080##model  ")
        assert result == ("http://localhost:8080/v1", "model")

    def test_whitespace_around_model(self):
        result = parse_generic_backend("generic/localhost:8080##  model-name  ")
        assert result == ("http://localhost:8080/v1", "model-name")

    # Error cases
    def test_missing_double_hash(self):
        with pytest.raises(ValueError, match="Expected 'generic/<host>##<model>'"):
            parse_generic_backend("generic/localhost:8080/model")

    def test_empty_host(self):
        with pytest.raises(ValueError, match="Host is empty"):
            parse_generic_backend("generic/##model")

    def test_empty_model(self):
        with pytest.raises(ValueError, match="Model is empty"):
            parse_generic_backend("generic/localhost:8080##")

    def test_missing_backend_prefix(self):
        with pytest.raises(ValueError, match="Invalid generic backend format"):
            parse_generic_backend("localhost:8080##model")

    def test_only_backend_prefix(self):
        with pytest.raises(ValueError, match="Invalid generic backend format"):
            parse_generic_backend("generic/")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid generic backend format"):
            parse_generic_backend("")


class TestLLMResponse:
    """Tests for LLMResponse class."""

    def test_llm_response_creation(self):
        """Test creating an LLMResponse with message and usage."""
        msg = Message(content="Hello", role="assistant", finish_reason="stop")
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        response = LLMResponse(message=msg, usage=usage)

        assert response.message == msg
        assert response.usage == usage

    def test_llm_response_with_none_usage(self):
        """Test creating an LLMResponse with None usage."""
        msg = Message(content="Hello", role="assistant", finish_reason="stop")
        response = LLMResponse(message=msg, usage=None)

        assert response.message == msg
        assert response.usage is None


class TestMessageReasoning:
    """Tests for reasoning extraction in Message.from_choice()."""

    def _make_mock_message(self, content, tool_calls=None, model_extra=None):
        """Helper to create a mock message with controlled model_extra."""
        msg = MagicMock()
        msg.content = content
        msg.role = "assistant"
        msg.tool_calls = tool_calls
        # Must explicitly set model_extra to avoid MagicMock's auto-mocking
        msg.model_extra = model_extra if model_extra is not None else {}
        return msg

    def _make_mock_choice(self, message, finish_reason="stop"):
        choice = MagicMock()
        choice.message = message
        choice.finish_reason = finish_reason
        return choice

    def test_no_model_extra_no_reasoning(self):
        """When model_extra is empty dict, reasoning is None."""
        msg = self._make_mock_message(content="Hello")
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.content == "Hello"
        assert result.reasoning is None

    def test_model_extra_reasoning_present(self):
        """When model_extra has 'reasoning', it is extracted."""
        msg = self._make_mock_message(
            content="Final answer", model_extra={"reasoning": "I think step by step..."}
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.content == "Final answer"
        assert result.reasoning == "I think step by step..."

    def test_model_extra_reasoning_content_present(self):
        """When model_extra has 'reasoning_content' (DeepSeek-style), it is extracted."""
        msg = self._make_mock_message(
            content="Final answer",
            model_extra={"reasoning_content": "Chain of thought..."},
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.content == "Final answer"
        assert result.reasoning == "Chain of thought..."

    def test_empty_content_with_reasoning_synthesizes(self):
        """When content is empty and reasoning exists with finish_reason=stop, synthesize content."""
        msg = self._make_mock_message(
            content="", model_extra={"reasoning": "The answer is 4."}
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert "REASONING" in result.content
        assert "The answer is 4." in result.content
        assert "END REASONING" in result.content
        assert result.reasoning == "The answer is 4."

    def test_empty_content_with_reasoning_tool_calls_no_synthesis(self):
        """When finish_reason is 'tool_calls', do not synthesize (tool calls take priority)."""
        mock_func = MagicMock()
        mock_func.name = "test"
        mock_func.arguments = "{}"
        tool_calls = [MagicMock(id="call_1", type="function", function=mock_func)]
        msg = self._make_mock_message(
            content="",
            tool_calls=tool_calls,
            model_extra={"reasoning": "some reasoning"},
        )
        choice = self._make_mock_choice(msg, finish_reason="tool_calls")
        result = Message.from_choice(choice)
        assert result.content == ""
        assert result.tool_calls is not None
        assert result.reasoning == "some reasoning"

    def test_model_extra_is_none(self):
        """When model_extra is None, no reasoning is extracted."""
        msg = MagicMock()
        msg.content = "Hello"
        msg.role = "assistant"
        msg.tool_calls = None
        msg.model_extra = None
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.reasoning is None

    def test_content_whitespace_with_reasoning_synthesizes(self):
        """When content is whitespace-only and reasoning exists, synthesize."""
        msg = self._make_mock_message(
            content="   ", model_extra={"reasoning": "Thinking step..."}
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert "REASONING" in result.content
        assert "Thinking step..." in result.content
        assert result.reasoning == "Thinking step..."

    def test_empty_content_without_reasoning_stays_empty(self):
        """When content is empty and no reasoning, content stays empty."""
        msg = self._make_mock_message(content=None, model_extra={})
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.content == ""
        assert result.reasoning is None

    def test_call_through_llm_backend_propagates_reasoning(self):
        """End-to-end: call() returns LLMResponse with reasoning propagated."""
        with (
            patch("wichy.llm_backend.OpenAI") as mock_openai,
            patch("wichy.llm_backend.settings") as mock_settings,
        ):
            mock_settings.ollama_base_url = "http://localhost:11434/v1"
            mock_settings.max_backend_connections = None

            mock_client = MagicMock()
            mock_openai.return_value = mock_client

            mock_message = MagicMock()
            mock_message.content = ""
            mock_message.role = "assistant"
            mock_message.tool_calls = None
            mock_message.model_extra = {"reasoning": "Hidden chain of thought."}

            mock_choice = MagicMock()
            mock_choice.message = mock_message
            mock_choice.finish_reason = "stop"

            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.model = "test-model"
            mock_response.usage = None

            mock_client.chat.completions.create.return_value = mock_response

            result = call(
                context=[{"role": "user", "content": "Think about X"}],
                model_str="ollama/test-model",
            )

            assert isinstance(result, LLMResponse)
            assert result.message.reasoning == "Hidden chain of thought."
            assert "REASONING" in result.message.content
            assert "Hidden chain of thought." in result.message.content

    def test_dual_field_precedence_reasoning_wins(self):
        """When both 'reasoning' and 'reasoning_content' exist, 'reasoning' takes precedence."""
        msg = self._make_mock_message(
            content="Final answer",
            model_extra={"reasoning": "Preferred", "reasoning_content": "Fallback"},
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.reasoning == "Preferred"

    def test_empty_string_reasoning_prefers_reasoning_content(self):
        """When 'reasoning' is empty string, it is still preferred over 'reasoning_content'."""
        msg = self._make_mock_message(
            content="Final answer",
            model_extra={"reasoning": "", "reasoning_content": "Fallback"},
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        # With hardened logic: empty string is not None so reasoning wins
        assert result.reasoning == ""

    def test_empty_content_no_reasoning_content(self):
        """When reasoning is None and no reasoning_content, empty content stays empty, reasoning None."""
        msg = self._make_mock_message(content="", model_extra={})
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.content == ""
        assert result.reasoning is None

    def test_reasoning_present_with_nonempty_content_no_synthesis(self):
        """When content is non-empty and reasoning exists, content is not synthesized."""
        msg = self._make_mock_message(
            content="Direct answer",
            model_extra={"reasoning": "Thinking..."},
        )
        choice = self._make_mock_choice(msg)
        result = Message.from_choice(choice)
        assert result.content == "Direct answer"
        assert result.reasoning == "Thinking..."


class TestCallFunction:
    """Tests for the call() function - token usage tracking."""

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_call_returns_llm_response_with_usage(self, mock_settings, mock_openai):
        """Test that call() returns an LLMResponse with usage data."""
        # Setup mock settings
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_settings.max_backend_connections = None  # No semaphore limit

        # Setup mock client and response
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock usage object
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 15
        mock_usage.completion_tokens = 25
        mock_usage.total_tokens = 40

        # Mock choice and message
        mock_message = MagicMock()
        mock_message.content = "Test response"
        mock_message.role = "assistant"
        mock_message.tool_calls = None
        mock_message.model_extra = {}

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        # Mock response
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "test-model"
        mock_response.usage = mock_usage

        mock_client.chat.completions.create.return_value = mock_response

        # Call function
        context = [{"role": "user", "content": "Hello"}]
        result = call(context, model_str="ollama/llama2")

        # Verify result is LLMResponse
        assert isinstance(result, LLMResponse)
        assert isinstance(result.message, Message)
        assert result.usage is not None
        assert result.usage["prompt_tokens"] == 15
        assert result.usage["completion_tokens"] == 25
        assert result.usage["total_tokens"] == 40

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_call_handles_missing_usage(self, mock_settings, mock_openai):
        """Test that call() handles response without usage data gracefully."""
        # Setup mock settings
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_settings.max_backend_connections = None  # No semaphore limit

        # Setup mock client and response
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock response without usage
        mock_message = MagicMock()
        mock_message.content = "Test response"
        mock_message.role = "assistant"
        mock_message.tool_calls = None
        mock_message.model_extra = {}

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "test-model"
        mock_response.usage = None  # No usage data

        mock_client.chat.completions.create.return_value = mock_response

        # Call function
        context = [{"role": "user", "content": "Hello"}]
        result = call(context, model_str="ollama/llama2")

        # Verify result is LLMResponse with None usage
        assert isinstance(result, LLMResponse)
        assert result.usage is None
        assert result.message.content == "Test response"

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_call_with_partial_usage_attributes(self, mock_settings, mock_openai):
        """Test handling of usage object with missing attributes (defaults to 0)."""
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_settings.max_backend_connections = None  # No semaphore limit

        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        # Mock usage with only total_tokens, missing others
        mock_usage = MagicMock(spec=[])  # No attributes by default
        del mock_usage.prompt_tokens  # Ensure AttributeError if accessed
        del mock_usage.completion_tokens
        mock_usage.total_tokens = 30

        mock_message = MagicMock()
        mock_message.content = "Test"
        mock_message.role = "assistant"
        mock_message.tool_calls = None
        mock_message.model_extra = {}

        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "test-model"
        mock_response.usage = mock_usage


class TestMaxBackendConnections:
    """Tests for the max_backend_connections semaphore."""

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_no_semaphore_when_limit_is_none(self, mock_settings, mock_openai):
        """When max_backend_connections is None, no semaphore is created."""
        mock_settings.max_backend_connections = None
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        _reset_backend_semaphore()
        assert _get_backend_semaphore() is None

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_semaphore_created_when_limit_is_set(self, mock_settings, mock_openai):
        """When a limit is set, a Semaphore with that count is created."""
        mock_settings.max_backend_connections = 2
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        _reset_backend_semaphore()
        sem = _get_backend_semaphore()
        assert sem is not None
        assert sem._value == 2  # Semaphore internal counter

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_second_caller_blocks_until_slot_frees(self, mock_settings, mock_openai):
        """Second caller blocks while first holds the semaphore; proceeds when freed."""
        mock_settings.max_backend_connections = 1
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        call_times = []

        def track_time(**kw):
            call_times.append(time.time())
            time.sleep(0.3)
            return self._mock_response("ok")

        mock_client.chat.completions.create.side_effect = track_time
        _reset_backend_semaphore()

        secondStarted = threading.Event()
        second_done = threading.Event()

        def second_caller():
            secondStarted.set()
            call([{"role": "user", "content": "hi"}], model_str="ollama/test")
            second_done.set()

        t = threading.Thread(target=second_caller)
        t.start()
        secondStarted.wait()  # wait for thread to start and block on semaphore
        time.sleep(0.05)

        # First call is still running; second is blocked
        assert len(call_times) == 1

        _ = call([{"role": "user", "content": "hi"}], model_str="ollama/test")

        second_done.wait()
        # Second call happened after first finished
        assert len(call_times) == 2
        assert call_times[1] >= call_times[0] + 0.3

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_semaphore_released_on_exception(self, mock_settings, mock_openai):
        """Semaphore is released even when call() raises, allowing next call to proceed."""
        mock_settings.max_backend_connections = 1
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("boom")
        _reset_backend_semaphore()

        with pytest.raises(LLMBackendUnhandledException):
            call([{"role": "user", "content": "hi"}], model_str="ollama/test")

        # Slot is free; second call succeeds (would deadlock if semaphore not released)
        mock_client.chat.completions.create.side_effect = None
        mock_client.chat.completions.create.return_value = self._mock_response("ok")
        result = call([{"role": "user", "content": "hi"}], model_str="ollama/test")
        assert result.message.content == "ok"

    # --- shared helper ---
    def _mock_response(self, content: str):
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 10
        mock_message = MagicMock()
        mock_message.content = content
        mock_message.role = "assistant"
        mock_message.tool_calls = None
        mock_message.model_extra = {}
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "test-model"
        mock_response.usage = mock_usage
        return mock_response
