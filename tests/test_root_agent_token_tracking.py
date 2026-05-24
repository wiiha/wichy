"""Tests for token tracking in RootAgent."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from wichy.root_agent.root_agent import RootAgent
from wichy.tools.base import BaseTool, ParametersModel


class MockToolParameters(ParametersModel):
    """Parameters model for mock tool."""

    pass


class MockTool(BaseTool):
    """Mock tool for testing."""

    name: str = "mock_tool"
    description: str = "A mock tool for testing"
    parameters_model = MockToolParameters

    def execute(self, **kwargs) -> str:
        return "Mocked result"


@pytest.fixture
def mock_context():
    """Create a mock context handler."""
    context = Mock()
    context.append = MagicMock()
    context.add_log = MagicMock()
    context.__len__ = MagicMock(return_value=1)
    context.start_watching = MagicMock()
    return context


@pytest.fixture
def root_agent(mock_context):
    """Create a RootAgent instance for testing."""
    tools = [MockTool()]
    return RootAgent(
        model_str="ollama/test",
        tools=tools,
        context=mock_context,
        name="test-agent",
        agent_has_first_initiative=False,
    )


class TestCheckTokenThreshold:
    """Tests for check_token_threshold() method."""

    def test_returns_false_when_threshold_is_none(self, root_agent):
        """Test that check_token_threshold returns False when threshold is None."""
        root_agent.auto_compact_threshold = None
        root_agent.current_prompt_tokens = 1500
        assert root_agent.check_token_threshold() is False

    def test_returns_true_when_tokens_exceed_threshold(self, root_agent):
        """Test that check_token_threshold returns True when tokens exceed threshold."""
        root_agent.auto_compact_threshold = 1000
        root_agent.current_prompt_tokens = 1500
        assert root_agent.check_token_threshold() is True

    def test_returns_false_when_tokens_below_threshold(self, root_agent):
        """Test that check_token_threshold returns False when tokens are below threshold."""
        root_agent.auto_compact_threshold = 1000
        root_agent.current_prompt_tokens = 500
        assert root_agent.check_token_threshold() is False


class TestUpdateTokenCounts:
    """Tests for _update_token_counts() method."""

    def test_updates_prompt_tokens_by_replacement(self, root_agent):
        """Test that prompt_tokens is replaced (not accumulated)."""
        root_agent.current_prompt_tokens = 100

        usage1 = {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250}
        root_agent._update_token_counts(usage1)

        assert root_agent.current_prompt_tokens == 200

        usage2 = {"prompt_tokens": 300, "completion_tokens": 75, "total_tokens": 375}
        root_agent._update_token_counts(usage2)

        # Should be replaced, not accumulated
        assert root_agent.current_prompt_tokens == 300

    def test_no_context_log_entry_when_usage_is_none(self, root_agent, mock_context):
        """No context log entry is created when usage data is None."""
        # Reset the mock to ignore any calls from __init__
        mock_context.add_log.reset_mock()

        root_agent._update_token_counts(None)

        # Verify add_log was NOT called
        mock_context.add_log.assert_not_called()

    def test_handles_none_gracefully(self, root_agent):
        """Test that _update_token_counts handles None usage."""
        root_agent.current_prompt_tokens = 100

        root_agent._update_token_counts(None)

        # Should not change values
        assert root_agent.current_prompt_tokens == 100


class TestAutoCompactContext:
    """Tests for _auto_compact_context() method."""

    def test_calls_compact_context_and_resets_tokens(self, root_agent, mock_context):
        """Test that _auto_compact_context calls compact_context and resets tokens."""
        # Mock compact_context method
        with patch.object(root_agent, "compact_context") as mock_compact:
            root_agent.current_prompt_tokens = 1500
            root_agent.auto_compact_threshold = 1000

            root_agent._auto_compact_context()

            # Verify compact_context was called
            mock_compact.assert_called_once()

            # Verify tokens were reset
            assert root_agent.current_prompt_tokens == 0


class TestProcessWithAutoCompaction:
    """End-to-end integration tests for process() with auto-compaction triggered."""

    def setup_method(self):
        """Set up a RootAgent with mocks for end-to-end testing."""
        # Create mock context with messages
        self.messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Tell me about Python."},
            {"role": "assistant", "content": "Python is a programming language..."},
            {"role": "user", "content": "What about Django?"},
            {"role": "assistant", "content": "Django is a web framework..."},
        ]
        self.mock_context = MagicMock()
        self.mock_context.return_value = self.messages
        self.mock_context.context = self.messages
        self.mock_context.__len__ = MagicMock(return_value=len(self.messages))
        self.mock_context.append = MagicMock()
        self.mock_context.add = MagicMock()
        self.mock_context.add_log = MagicMock()
        self.mock_context.start_watching = MagicMock()
        self.mock_context.stop_watching = MagicMock()
        self.mock_context.delete = MagicMock()
        self.mock_context.drop = MagicMock()

        # Initialize the agent with the mocked context and low auto_compact_threshold
        tools = [MockTool()]
        self.root_agent = RootAgent(
            model_str="ollama/test",
            tools=tools,
            context=self.mock_context,
            name="test-agent",
            agent_has_first_initiative=False,
            auto_compact_threshold=1000,  # Low threshold to trigger compaction
        )

    def _create_mock_llm_response(
        self, content: str, prompt_tokens: int, tool_calls=None
    ):
        """Create a mock LLM response with the given content and token counts."""
        mock_response = MagicMock()
        mock_response.message = MagicMock()
        mock_response.message.content = content
        mock_response.message.role = "assistant"
        mock_response.message.finish_reason = "stop" if not tool_calls else "tool_calls"
        mock_response.message.tool_calls = tool_calls
        mock_response.usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 50,
            "total_tokens": prompt_tokens + 50,
        }
        return mock_response

    def test_process_triggers_auto_compaction_end_to_end(self):
        """Test that process() triggers auto-compaction exactly twice when threshold is exceeded.

        With auto_compact_threshold=1000 and prompt_tokens=2000, compact_context is called:
        1. After the first LLM call, before the tool handling loop (line 257)
        2. After appending the assistant response, before returning (line 298)
        """
        # Create mock response with high prompt_tokens (exceeds threshold)
        main_response = self._create_mock_llm_response(
            content="This is a helpful response.",
            prompt_tokens=2000,  # Exceeds threshold of 1000
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            # The main call from process()
            mock_call.return_value = main_response

            # Mock compact_context entirely so it doesn't make additional LLM calls
            with patch.object(self.root_agent, "compact_context") as mock_compact:
                result = self.root_agent.process("Hello")

                # Verify the main process call returned something
                assert result is not None

                # Verify compact_context was called exactly twice:
                # 1. After first LLM call threshold check (line 256-257)
                # 2. After final token threshold check (line 297-298)
                assert mock_compact.call_count == 2, (
                    f"Expected compact_context to be called exactly twice, "
                    f"but it was called {mock_compact.call_count} times"
                )

    def test_process_resets_tokens_after_auto_compaction(self):
        """Test that current_prompt_tokens is reset to 0 after auto-compaction."""
        # High prompt_tokens to trigger compaction
        main_response = self._create_mock_llm_response(
            content="Helpful response.",
            prompt_tokens=2000,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = main_response

            # Mock compact_context entirely
            with patch.object(self.root_agent, "compact_context"):
                # Initial token count
                self.root_agent.current_prompt_tokens = 0

                self.root_agent.process("Hello")

                # After process() with auto-compaction, tokens should be reset to 0
                # (reset by _auto_compact_context)
                assert self.root_agent.current_prompt_tokens == 0

    def test_process_context_append_was_called(self):
        """Test that context.append was called with user message and assistant response.

        During a normal (non-compaction) flow:
        1. Line 218: User message is appended (role='user', content='Hello')
        2. Lines 292-294: Assistant response is appended (role='assistant', content from response)

        With prompt_tokens=200 (below threshold 1000), no compaction occurs.
        """
        assistant_content = "This is the assistant's response."
        main_response = self._create_mock_llm_response(
            content=assistant_content,
            prompt_tokens=200,  # Below threshold of 1000
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = main_response

            # Don't patch compact_context - it should NOT be called (tokens below threshold)
            self.root_agent.process("Hello")

            # Verify exactly 2 append calls (user message + assistant response)
            assert self.mock_context.append.call_count == 2, (
                f"Expected context.append to be called exactly 2 times "
                f"(user message + assistant response), but was called "
                f"{self.mock_context.append.call_count} times"
            )

            # Verify the exact roles and content of what was appended
            first_call_args = self.mock_context.append.call_args_list[0][0][0]
            second_call_args = self.mock_context.append.call_args_list[1][0][0]

            # First append should be the user message (line 218 in process())
            assert first_call_args["role"] == "user", (
                f"First append should have role='user', got role='{first_call_args['role']}'"
            )
            assert first_call_args["content"] == "Hello", (
                f"First append should have content='Hello', got content='{first_call_args['content']}'"
            )

            # Second append should be the assistant response (lines 292-294 in process())
            assert second_call_args["role"] == "assistant", (
                f"Second append should have role='assistant', got role='{second_call_args['role']}'"
            )
            assert second_call_args["content"] == assistant_content, (
                f"Second append should have content='{assistant_content}', "
                f"got content='{second_call_args['content']}'"
            )

            # Verify compact_context was NOT called (tokens 200 < threshold 1000)
            self.mock_context.compact_context.assert_not_called()

    def test_process_no_compaction_when_below_threshold(self):
        """Test that no compaction occurs when tokens are below threshold."""
        # Low prompt_tokens - below threshold of 1000
        response = self._create_mock_llm_response(
            content="Short response.",
            prompt_tokens=500,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = response

            with patch.object(
                self.root_agent, "_auto_compact_context"
            ) as mock_auto_compact:
                with patch.object(self.root_agent, "compact_context") as mock_compact:
                    result = self.root_agent.process("Hello")

                    # Verify process still works
                    assert result is not None

                    # Verify auto_compact was NOT called (tokens below threshold)
                    mock_auto_compact.assert_not_called()
                    mock_compact.assert_not_called()

    def test_process_with_tool_calls_still_triggers_compaction(self):
        """Test that auto-compaction still triggers even when response has tool_calls."""
        # Create a tool call for the response
        mock_tool_call = MagicMock()
        mock_tool_call.id = "call_123"
        mock_tool_call.type = "function"
        mock_tool_call.function = MagicMock()
        mock_tool_call.function.name = "mock_tool"
        mock_tool_call.function.arguments = "{}"

        main_response = self._create_mock_llm_response(
            content="I will use a tool.",
            prompt_tokens=2000,
            tool_calls=[mock_tool_call],
        )
        # Tool result triggers another call (after tool execution)
        tool_result_response = self._create_mock_llm_response(
            content="Tool result here.",
            prompt_tokens=2000,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.side_effect = [main_response, tool_result_response]

            with patch.object(self.root_agent, "compact_context") as mock_compact:
                result = self.root_agent.process("Hello")

                # Verify process completed
                assert result is not None

                # Auto-compaction should be triggered exactly twice:
                # 1. After first LLM call (line 256-257), before tool handling loop
                #    - tokens=2000 exceeds threshold=1000
                # 2. After final threshold check (line 297-298), after tool loop ends
                #    - tokens still 2000 (no _update_token_counts in the while loop)
                #    - threshold still exceeded
                assert mock_compact.call_count == 2, (
                    f"Expected compact_context to be called exactly twice, "
                    f"but it was called {mock_compact.call_count} times"
                )


class TestCompactContext:
    """Integration tests for compact_context() — verifies context is replaced with summary."""

    def setup_method(self):
        """Create a RootAgent instance with mocks for compact_context testing."""
        # Create mock context with messages
        # Use a list to hold the messages, wrapped in a MagicMock for proper mocking
        self.messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Tell me about Python."},
            {"role": "assistant", "content": "Python is a programming language..."},
            {"role": "user", "content": "What about Django?"},
            {"role": "assistant", "content": "Django is a web framework..."},
        ]
        self.mock_context = MagicMock()
        # Make context callable to return the messages list
        self.mock_context.return_value = self.messages
        # Allow access to the context attribute for iteration in compact_context
        self.mock_context.context = self.messages
        self.mock_context.__len__ = MagicMock(return_value=len(self.messages))
        self.mock_context.append = MagicMock()
        self.mock_context.add = MagicMock()
        self.mock_context.add_log = MagicMock()
        self.mock_context.start_watching = MagicMock()
        self.mock_context.stop_watching = MagicMock()
        self.mock_context.delete = MagicMock()

        # Initialize the agent with the mocked context
        tools = [MockTool()]
        self.root_agent = RootAgent(
            model_str="ollama/test",
            tools=tools,
            context=self.mock_context,
            name="test-agent",
            agent_has_first_initiative=False,
        )

    def test_compact_context_replaces_history_with_summary(self):
        """Original messages should be replaced by a single summary message."""
        # Create mock response from LLM
        mock_response = Mock()
        mock_response.message = Mock()
        mock_response.message.content = "User discussed Python and Django. Assistant explained both topics in detail."

        # Mock the call() function used internally by compact_context
        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = mock_response

            self.root_agent.compact_context()

            # Verify call was made (LLM was asked to summarize)
            mock_call.assert_called_once()

            # The new context should have exactly 2 messages:
            # 1. System prompt (original first message)
            # 2. Summary user message
            new_context = self.root_agent.context()
            assert len(new_context) == 2

    def test_compact_context_preserves_system_prompt(self):
        """System prompt should be preserved as the first message after compaction."""
        # Create mock response from LLM
        mock_response = Mock()
        mock_response.message = Mock()
        mock_response.message.content = (
            "User and assistant discussed programming topics."
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = mock_response

            self.root_agent.compact_context()

            new_context = self.root_agent.context()

            # First message should be the original system prompt
            assert new_context[0]["role"] == "system"
            assert new_context[0]["content"] == "You are a helpful assistant."

    def test_compact_context_reduces_context_size(self):
        """Context should be significantly shorter after compaction."""
        # Create a much larger context
        large_messages = [
            {"role": "system", "content": "You are a helpful assistant."},
        ]
        # Add 10 pairs of user/assistant messages
        for i in range(10):
            large_messages.append({"role": "user", "content": f"What is topic {i}?"})
            large_messages.append(
                {
                    "role": "assistant",
                    "content": f"Here is detailed information about topic {i}...",
                }
            )

        # Update the mock to use the larger context
        self.mock_context.context = large_messages
        self.mock_context.return_value = large_messages
        self.mock_context.__len__ = MagicMock(return_value=len(large_messages))

        # Update root_agent's context reference to point to the updated mock
        self.root_agent.context = self.mock_context

        original_size = len(self.root_agent.context())
        assert original_size == 21  # 1 system + 10 user + 10 assistant

        # Create mock response
        mock_response = Mock()
        mock_response.message = Mock()
        mock_response.message.content = (
            "The conversation covered various topics from 0 to 9."
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = mock_response

            self.root_agent.compact_context()

            new_size = len(self.root_agent.context())

            # Context should be MUCH smaller (2 instead of 21)
            assert new_size < original_size
            assert new_size == 2  # Only system prompt + summary


class TestReasoningPersistence:
    """Tests that reasoning survives context append and persistence."""

    def _create_mock_llm_response_with_reasoning(
        self, content: str, reasoning: str | None, prompt_tokens: int
    ):
        """Create a mock LLM response with optional reasoning."""
        mock_response = MagicMock()
        mock_response.message = MagicMock()
        mock_response.message.content = content
        mock_response.message.role = "assistant"
        mock_response.message.finish_reason = "stop"
        mock_response.message.tool_calls = None
        mock_response.message.reasoning = reasoning
        mock_response.usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 50,
            "total_tokens": prompt_tokens + 50,
        }
        return mock_response

    def test_reasoning_appended_to_context(self):
        """When assistant response has reasoning, it is appended to context."""
        from unittest.mock import MagicMock, patch
        from wichy.root_agent.root_agent import RootAgent
        from wichy.tools.base import BaseTool, ParametersModel

        class MockToolParams(ParametersModel):
            pass

        class MockTool(BaseTool):
            name: str = "mock_tool"
            description: str = "A mock tool"
            parameters_model = MockToolParams

            def execute(self, **kwargs) -> str:
                return "Mocked result"

        mock_context = MagicMock()
        mock_context.append = MagicMock()
        mock_context.__len__ = MagicMock(return_value=1)
        mock_context.start_watching = MagicMock()
        mock_context.return_value = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        agent = RootAgent(
            model_str="ollama/test",
            tools=[MockTool()],
            context=mock_context,
            name="test-agent",
            agent_has_first_initiative=False,
        )

        main_response = self._create_mock_llm_response_with_reasoning(
            content="The answer is 42.",
            reasoning="Let me think step by step...",
            prompt_tokens=200,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = main_response
            agent.process("Hello")

        # Find assistant append calls (there may be 2: user + assistant)
        assistant_calls = [
            call[0][0]
            for call in mock_context.append.call_args_list
            if call[0][0].get("role") == "assistant"
        ]
        assert len(assistant_calls) >= 1
        assert assistant_calls[0]["reasoning"] == "Let me think step by step..."

    def test_reasoning_not_present_when_none(self):
        """When assistant response has no reasoning, key should not appear."""
        from unittest.mock import MagicMock, patch
        from wichy.root_agent.root_agent import RootAgent
        from wichy.tools.base import BaseTool, ParametersModel

        class MockToolParams(ParametersModel):
            pass

        class MockTool(BaseTool):
            name: str = "mock_tool"
            description: str = "A mock tool"
            parameters_model = MockToolParams

            def execute(self, **kwargs) -> str:
                return "Mocked result"

        mock_context = MagicMock()
        mock_context.append = MagicMock()
        mock_context.__len__ = MagicMock(return_value=1)
        mock_context.start_watching = MagicMock()
        mock_context.return_value = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        agent = RootAgent(
            model_str="ollama/test",
            tools=[MockTool()],
            context=mock_context,
            name="test-agent",
            agent_has_first_initiative=False,
        )

        main_response = self._create_mock_llm_response_with_reasoning(
            content="The answer is 42.",
            reasoning=None,
            prompt_tokens=200,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = main_response
            agent.process("Hello")

        assistant_calls = [
            call[0][0]
            for call in mock_context.append.call_args_list
            if call[0][0].get("role") == "assistant"
        ]
        assert len(assistant_calls) >= 1
        assert "reasoning" not in assistant_calls[0]
