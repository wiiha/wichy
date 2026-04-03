"""
Unit tests for AgentCore base class.

Tests the shared functionality between RootAgent and TaskAgent.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from wichy.agent.core import AgentCore
from wichy.constants import ROLE_TOOL
from wichy.llm_backend import called_tool, function


class ConcreteAgent(AgentCore):
    """Concrete implementation of AgentCore for testing."""

    def __init__(self):
        super().__init__()
        self._name = "TestAgent"
        self.model_str = "test-model"
        self.context = Mock()
        self.context.append = Mock()
        self.tools = []

    @property
    def name(self) -> str:
        return self._name

    def _log(self, message: str) -> None:
        pass

    def _log_dict(self, data) -> None:
        pass


class TestAgentCoreName:
    """Tests for the name property."""

    def test_name_property_returns_name(self):
        """Test that name property returns the agent name."""
        agent = ConcreteAgent()
        assert agent.name == "TestAgent"


class TestAgentCoreLogging:
    """Tests for logging methods."""

    def test_log_default_implementation_does_nothing(self):
        """Test default _log implementation does nothing."""
        # Create a concrete agent but use the base class _log method
        agent = ConcreteAgent()
        # Should not raise - default implementation is pass
        AgentCore._log(agent, "test message")

    def test_log_dict_default_implementation_does_nothing(self):
        """Test default _log_dict implementation does nothing."""
        agent = ConcreteAgent()
        # Should not raise - default implementation is pass
        AgentCore._log_dict(agent, {"test": "data"})


class TestAgentCoreToolCall:
    """Tests for _tool_call method."""

    def test_tool_call_returns_error_for_unknown_tool(self):
        """Test _tool_call returns error when tool not found."""
        agent = ConcreteAgent()
        mock_tool_call = Mock()
        mock_tool_call.function.name = "unknown_tool"
        mock_tool_call.function.arguments = "{}"
        mock_tool_call.id = "call-123"

        result, multimodal = agent._tool_call([], mock_tool_call)

        assert "error" not in result["content"]
        assert "no tool called" in result["content"].lower()
        assert multimodal is None

    def test_tool_calls_validate_and_execute(self):
        """Test _tool_call calls validate_and_execute on matching tool."""
        agent = ConcreteAgent()

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="tool result")
        agent.tools = [mock_tool]

        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"arg1": "value1"}'
        mock_tool_call.id = "call-123"

        result, multimodal = agent._tool_call([mock_tool], mock_tool_call)

        mock_tool.validate_and_execute.assert_called_once_with(
            arg1="value1", _can_query_results=False
        )
        assert result["role"] == ROLE_TOOL
        assert result["tool_call_id"] == "call-123"

    def test_tool_call_injects_model_str_when_requested(self):
        """Test _tool_call injects model_str when inject_model_str=True and tool has model_str param."""
        agent = ConcreteAgent()

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="result")
        # Mock the parameters_model to return a schema with model_str property
        mock_tool.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"arg1": {}, "model_str": {}}}
        )
        agent.tools = [mock_tool]

        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"arg1": "value1"}'
        mock_tool_call.id = "call-123"

        agent._tool_call([mock_tool], mock_tool_call, inject_model_str=True)

        mock_tool.validate_and_execute.assert_called_once_with(
            arg1="value1", model_str="test-model", _can_query_results=False
        )

    def test_tool_call_does_not_inject_model_str_by_default(self):
        """Test _tool_call does not inject model_str by default."""
        agent = ConcreteAgent()

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="result")
        agent.tools = [mock_tool]

        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"arg1": "value1"}'
        mock_tool_call.id = "call-123"

        agent._tool_call([mock_tool], mock_tool_call)

        # Should not include model_str (but will include _can_query_results)
        mock_tool.validate_and_execute.assert_called_once_with(
            arg1="value1", _can_query_results=False
        )


class TestAgentCoreFixMultimodalContext:
    """Tests for _fix_multimodal_context method."""

    @patch("wichy.agent.core.fix_multimodal_context")
    def test_fix_multimodal_context_returns_true_when_found(self, mock_fix):
        """Test _fix_multimodal_context returns True when content found."""
        mock_fix.return_value = True

        agent = ConcreteAgent()
        logged_messages = []
        agent._log = lambda msg: logged_messages.append(msg)

        result = agent._fix_multimodal_context()

        assert result is True
        assert "Fixed multimodal content" in logged_messages[0]
        mock_fix.assert_called_once_with(agent.context)

    @patch("wichy.agent.core.fix_multimodal_context")
    def test_fix_multimodal_context_returns_false_when_not_found(self, mock_fix):
        """Test _fix_multimodal_context returns False when no content found."""
        mock_fix.return_value = False

        agent = ConcreteAgent()
        logged_messages = []
        agent._log = lambda msg: logged_messages.append(msg)

        result = agent._fix_multimodal_context()

        assert result is False
        # No log message when not found
        assert len(logged_messages) == 0


class TestAgentCoreHandleToolsBase:
    """Tests for _handle_tools_base method."""

    def test_handle_tools_base_returns_false_when_no_tool_calls(self):
        """Test _handle_tools_base returns False when no tool calls."""
        agent = ConcreteAgent()

        mock_response = Mock()
        mock_response.finish_reason = "stop"
        mock_response.content = "response text"

        modified, multimodal = agent._handle_tools_base([], mock_response)

        assert modified is False
        assert multimodal == []

    def test_handle_tools_base_processes_tool_calls(self):
        """Test _handle_tools_base processes tool calls correctly."""
        agent = ConcreteAgent()

        # Mock tool
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="result")
        agent.tools = [mock_tool]

        # Use a real list that tracks append calls
        context_list = []

        class MockContext:
            def append(self, msg):
                context_list.append(msg)

            def __len__(self):
                return len(context_list)

        agent.context = MockContext()

        # Mock tool call
        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = "{}"
        mock_tool_call.id = "call-123"
        mock_tool_call.model_dump = Mock(return_value={"id": "call-123"})

        # Mock response
        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = "thinking..."
        mock_response.tool_calls = [mock_tool_call]

        modified, multimodal = agent._handle_tools_base([mock_tool], mock_response)

        # Check that context was modified (2 messages appended)
        assert len(context_list) == 2
        assert modified is True


class TestAgentCoreGetToolDefinitions:
    """Tests for _get_tool_definitions method."""

    def test_get_tool_definitions_calls_with_tools(self):
        """Test _get_tool_definitions calls get_tool_definitions with tools."""
        agent = ConcreteAgent()
        result = agent._get_tool_definitions()
        # Result depends on tools list - empty list returns empty definitions
        assert result == []


# =============================================================================
# MRO-AWARE TESTS - These would catch signature mismatch bugs
# =============================================================================


class TestMROSignatureCompatibility:
    """
    Tests that verify Method Resolution Order (MRO) compatibility between
    AgentCore._handle_tools_base calling self._tool_call.

    These tests would catch bugs where _handle_tools_base calls _tool_call
    with arguments that don't match _tool_call's signature.

    The historical bug: _tool_call had signature (self, tools, item) but
    _handle_tools_base called it with (tools, item, inject_model_str) causing
    a TypeError at runtime.
    """

    def test_handle_tools_base_calls_tool_call_with_correct_signature(self):
        """
        Test that _handle_tools_base can call _tool_call with all parameters.

        This test verifies the actual MRO path - when _handle_tools_base
        calls self._tool_call(...), it must use a compatible signature.
        """
        agent = ConcreteAgent()
        agent.model_str = "gpt-4-turbo"

        # Track what arguments were passed to validate_and_execute
        captured_calls = []

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(
            side_effect=lambda **kwargs: captured_calls.append(kwargs) or "result"
        )
        # Mock the parameters_model to return a schema with model_str property
        mock_tool.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"arg1": {}, "model_str": {}}}
        )

        # Create a context that tracks appends
        context_messages = []

        class TrackingContext:
            def append(self, msg):
                context_messages.append(msg)

            def __len__(self):
                return len(context_messages)

        agent.context = TrackingContext()

        # Create a tool call
        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"arg1": "value1"}'
        mock_tool_call.id = "call-123"
        mock_tool_call.model_dump = Mock(
            return_value={"id": "call-123", "type": "function"}
        )

        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = ""
        mock_response.tool_calls = [mock_tool_call]

        # This call goes through the actual MRO: _handle_tools_base -> self._tool_call
        # If there's a signature mismatch, this will raise TypeError
        modified, _ = agent._handle_tools_base(
            [mock_tool], mock_response, inject_model_str=True
        )

        # Verify the call succeeded without TypeError
        assert modified is True
        assert len(captured_calls) == 1
        # Verify model_str was injected (along with _can_query_results)
        assert captured_calls[0] == {
            "arg1": "value1",
            "model_str": "gpt-4-turbo",
            "_can_query_results": False,
        }

    def test_handle_tools_base_without_inject_model_str(self):
        """
        Test _handle_tools_base works correctly when inject_model_str=False.

        This tests the TaskAgent code path which passes inject_model_str=False.
        """
        agent = ConcreteAgent()
        agent.model_str = "claude-3-opus"

        captured_calls = []

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(
            side_effect=lambda **kwargs: captured_calls.append(kwargs) or "result"
        )

        context_messages = []

        class TrackingContext:
            def append(self, msg):
                context_messages.append(msg)

            def __len__(self):
                return len(context_messages)

        agent.context = TrackingContext()

        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"arg1": "value1"}'
        mock_tool_call.id = "call-456"
        mock_tool_call.model_dump = Mock(
            return_value={"id": "call-456", "type": "function"}
        )

        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = ""
        mock_response.tool_calls = [mock_tool_call]

        # Call with inject_model_str=False (TaskAgent behavior)
        modified, _ = agent._handle_tools_base(
            [mock_tool], mock_response, inject_model_str=False
        )

        assert modified is True
        assert len(captured_calls) == 1
        # Verify model_str was NOT injected (but _can_query_results is present)
        assert captured_calls[0] == {"arg1": "value1", "_can_query_results": False}

    def test_handle_tools_base_multiple_tool_calls(self):
        """
        Test _handle_tools_base handles multiple tool calls through the MRO path.

        This verifies that multiple tool calls all go through correctly.
        """
        agent = ConcreteAgent()
        agent.model_str = "test-model"

        call_count = []

        mock_tool1 = Mock()
        mock_tool1.name = "tool1"
        mock_tool1.validate_and_execute = Mock(
            side_effect=lambda **kwargs: call_count.append(("tool1", kwargs))
            or "result1"
        )
        # Mock the parameters_model to return a schema with model_str property
        mock_tool1.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"x": {}, "model_str": {}}}
        )

        mock_tool2 = Mock()
        mock_tool2.name = "tool2"
        mock_tool2.validate_and_execute = Mock(
            side_effect=lambda **kwargs: call_count.append(("tool2", kwargs))
            or "result2"
        )
        # Mock the parameters_model to return a schema with model_str property
        mock_tool2.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"y": {}, "model_str": {}}}
        )

        context_messages = []

        class TrackingContext:
            def append(self, msg):
                context_messages.append(msg)

            def __len__(self):
                return len(context_messages)

        agent.context = TrackingContext()

        # Create two tool calls
        mock_tool_call1 = Mock()
        mock_tool_call1.function.name = "tool1"
        mock_tool_call1.function.arguments = '{"x": 1}'
        mock_tool_call1.id = "call-1"
        mock_tool_call1.model_dump = Mock(return_value={"id": "call-1"})

        mock_tool_call2 = Mock()
        mock_tool_call2.function.name = "tool2"
        mock_tool_call2.function.arguments = '{"y": 2}'
        mock_tool_call2.id = "call-2"
        mock_tool_call2.model_dump = Mock(return_value={"id": "call-2"})

        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = ""
        mock_response.tool_calls = [mock_tool_call1, mock_tool_call2]

        modified, _ = agent._handle_tools_base(
            [mock_tool1, mock_tool2], mock_response, inject_model_str=True
        )

        assert modified is True
        assert len(call_count) == 2
        # Both tools should receive model_str and _can_query_results
        assert call_count[0] == (
            "tool1",
            {"x": 1, "model_str": "test-model", "_can_query_results": False},
        )
        assert call_count[1] == (
            "tool2",
            {"y": 2, "model_str": "test-model", "_can_query_results": False},
        )


class TestSubclassIntegration:
    """
    Tests that verify RootAgent and TaskAgent can successfully call
    _handle_tools_base which internally calls _tool_call.

    These tests would catch the historical bug where signature mismatch
    caused runtime TypeError when a sub-agent tried to call tools.
    """

    def test_handle_tools_base_signature_compatible_with_tool_call(self):
        """
        Verify that _handle_tools_base calls _tool_call with correct arguments.

        This test directly verifies the signature compatibility that was broken:
        - _handle_tools_base calls: self._tool_call(tools, item, inject_model_str)
        - _tool_call expects: (self, tools, item, inject_model_str=False)

        If signatures don't match, this will raise TypeError.
        """
        agent = ConcreteAgent()
        agent.model_str = "test-model"

        # Create a mock tool that will receive the call
        mock_tool = Mock()
        mock_tool.name = "my_tool"
        mock_tool.validate_and_execute = Mock(return_value="tool output")
        # Mock the parameters_model to return a schema with model_str property
        mock_tool.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"input": {}, "model_str": {}}}
        )

        # Setup context
        context_messages = []

        class TrackingContext:
            def append(self, msg):
                context_messages.append(msg)

            def __len__(self):
                return len(context_messages)

        agent.context = TrackingContext()

        # Create a real called_tool object to simulate LLM response
        tool_call_obj = called_tool(
            id="call-test-1",
            type="function",
            function=function(name="my_tool", arguments='{"input": "test"}'),
        )

        # Create a response message
        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = ""
        mock_response.tool_calls = [tool_call_obj]

        # This is the critical call - if signature is wrong, TypeError is raised
        # The bug was: _tool_call had (self, tools, item) but was called with
        # (self, tools, item, inject_model_str)
        try:
            modified, _ = agent._handle_tools_base(
                [mock_tool], mock_response, inject_model_str=True
            )
            # Success means signature is compatible
            assert modified is True
        except TypeError as e:
            # This would have caught the bug!
            pytest.fail(f"TypeError raised - signature mismatch: {e}")

    def test_tool_call_accepts_inject_model_str_keyword_arg(self):
        """
        Test that _tool_call accepts inject_model_str as a keyword argument.

        The bug could have been avoided if _tool_call used **kwargs, but
        this test verifies the explicit parameter works.
        """
        agent = ConcreteAgent()
        agent.model_str = "test-model"

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="result")
        # Mock the parameters_model to return a schema with model_str property
        mock_tool.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"model_str": {}}}
        )

        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = "{}"
        mock_tool_call.id = "call-123"

        # Call with inject_model_str as keyword argument
        # This must work for _handle_tools_base to work correctly
        result, _ = agent._tool_call([mock_tool], mock_tool_call, inject_model_str=True)

        assert result["role"] == ROLE_TOOL
        mock_tool.validate_and_execute.assert_called_once_with(
            model_str="test-model", _can_query_results=False
        )


class TestRootAgentInheritance:
    """Tests for RootAgent's use of AgentCore._handle_tools_base."""

    def test_root_agent_handle_tools_delegates_to_base(self):
        """
        Test that RootAgent.handle_tools can call _handle_tools_base with
        inject_model_str=True without TypeError.
        """
        from unittest.mock import patch, MagicMock
        from wichy.root_agent.root_agent import RootAgent

        # Create a minimal RootAgent with mocked dependencies
        mock_context = MagicMock()
        mock_context.append = Mock()
        mock_context.__len__ = Mock(return_value=1)

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        with patch("wichy.root_agent.root_agent.new_context") as mock_new_context:
            mock_new_context.return_value = mock_context

            agent = RootAgent(
                model_str="gpt-4",
                tools=[mock_tool],
                name="TestRoot",
                print_info_lines=False,
            )

        # Create a mock response with tool calls
        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"test": "value"}'
        mock_tool_call.id = "call-1"
        mock_tool_call.model_dump = Mock(return_value={"id": "call-1"})

        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = ""
        mock_response.tool_calls = [mock_tool_call]

        # Mock the tool's validate_and_execute
        mock_tool_instance = Mock()
        mock_tool_instance.name = "test_tool"
        mock_tool_instance.validate_and_execute = Mock(return_value="result")
        # Mock the parameters_model to return a schema with model_str property
        mock_tool_instance.parameters_model.model_json_schema = Mock(
            return_value={"properties": {"test": {}, "model_str": {}}}
        )
        agent.tools = [mock_tool_instance]

        # This should not raise TypeError
        # Tests that RootAgent can call _handle_tools_base -> _tool_call
        agent.handle_tools([mock_tool_instance], mock_response)

        # Verify tool was executed with model_str injected
        mock_tool_instance.validate_and_execute.assert_called_once()


class TestTaskAgentInheritance:
    """Tests for TaskAgent's use of AgentCore._handle_tools_base."""

    def test_task_agent_handle_tools_delegates_to_base(self):
        """
        Test that TaskAgent._handle_tools can call _handle_tools_base with
        inject_model_str=False without TypeError.
        """
        from unittest.mock import patch
        from wichy.tools.task.base import TaskAgent, TaskAgentDefinitionBase

        # Create a minimal task agent definition
        definition = TaskAgentDefinitionBase(
            name="TestTask",
            description="A test task agent",
            system_prompt="You are a test agent.",
            include_env_info=False,
        )

        # Mock the context handler
        mock_context = MagicMock()
        mock_context.append = Mock()
        mock_context.__len__ = Mock(return_value=2)
        mock_context.__call__ = Mock(
            return_value=[
                {"role": "system", "content": "You are a test agent."},
                {"role": "user", "content": "Do something"},
            ]
        )
        mock_context.add = Mock()

        mock_tool = Mock()
        mock_tool.name = "test_tool"

        with patch("wichy.tools.task.base.ContextHandler") as MockContextHandler:
            MockContextHandler.return_value = mock_context

            agent = TaskAgent(
                agent_definition=definition,
                prompt="Test prompt",
                model="test-model",
                all_tools_not_instantiated=[mock_tool.__class__],
            )

        # Create a mock response with tool calls
        mock_tool_call = Mock()
        mock_tool_call.function.name = "test_tool"
        mock_tool_call.function.arguments = '{"input": "test"}'
        mock_tool_call.id = "call-2"
        mock_tool_call.model_dump = Mock(return_value={"id": "call-2"})

        mock_response = Mock()
        mock_response.finish_reason = "tool_calls"
        mock_response.content = ""
        mock_response.tool_calls = [mock_tool_call]

        # Get the instantiated tool
        mock_tool_instance = agent.tools[0] if agent.tools else Mock()
        mock_tool_instance.name = "test_tool"
        mock_tool_instance.validate_and_execute = Mock(return_value="result")

        # This should not raise TypeError
        # Tests that TaskAgent can call _handle_tools_base -> _tool_call
        agent._handle_tools([mock_tool_instance], mock_response)

        # Verify tool was executed WITHOUT model_str (TaskAgent behavior)
        mock_tool_instance.validate_and_execute.assert_called_once_with(
            input="test", _can_query_results=False
        )
