"""
Tests for loop detection in agent tool calls.

Covers:
- LoopDetector unit tests (signature hashing, rolling window, threshold)
- compute_signature helper tests
- AgentCore._handle_tools_base integration tests (loop trigger, warning injection)
"""

from unittest.mock import Mock

import pytest
from wichy.agent.core import AgentCore
from wichy.agent.loop_detector import LoopDetector, compute_signature
from wichy.constants import ROLE_USER
from wichy.llm_backend import called_tool, function

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class ConcreteAgent(AgentCore):
    """Concrete implementation of AgentCore for testing."""

    def __init__(self):
        super().__init__()
        self._name = "TestAgent"
        self.model_str = "test-model"
        self.tools = []
        self.context = []
        # Override _log to be a no-op
        self._log_msgs = []

    @property
    def name(self) -> str:
        return self._name

    def _log(self, message: str) -> None:
        self._log_msgs.append(message)

    def _log_dict(self, data) -> None:
        pass


def make_tool_call(
    name: str = "test_tool",
    arguments: str = '{"key": "value"}',
    call_id: str = "call-1",
) -> called_tool:
    """Build a called_tool object for testing."""
    return called_tool(
        id=call_id,
        type="function",
        function=function(name=name, arguments=arguments),
    )


def make_response(tool_calls=None, finish_reason="tool_calls", content="thinking"):
    """Build a mock LLM response with tool calls."""
    mock_response = Mock()
    mock_response.finish_reason = finish_reason
    mock_response.content = content
    mock_response.tool_calls = tool_calls or []
    mock_response.reasoning = None
    return mock_response


# ---------------------------------------------------------------------------
# compute_signature tests
# ---------------------------------------------------------------------------


class TestComputeSignature:
    """Tests for the compute_signature helper."""

    def test_deterministic_same_inputs_same_hash(self):
        """Same tool name, args, and result produce the same signature."""
        sig1 = compute_signature("read_file", '{"path": "/a"}', "hello")
        sig2 = compute_signature("read_file", '{"path": "/a"}', "hello")
        assert sig1 == sig2

    def test_different_tool_name_different_hash(self):
        """Different tool names produce different signatures."""
        sig1 = compute_signature("read_file", '{"path": "/a"}', "hello")
        sig2 = compute_signature("write_file", '{"path": "/a"}', "hello")
        assert sig1 != sig2

    def test_different_args_different_hash(self):
        """Different arguments produce different signatures."""
        sig1 = compute_signature("read_file", '{"path": "/a"}', "hello")
        sig2 = compute_signature("read_file", '{"path": "/b"}', "hello")
        assert sig1 != sig2

    def test_different_result_different_hash(self):
        """Different results produce different signatures."""
        sig1 = compute_signature("read_file", '{"path": "/a"}', "hello")
        sig2 = compute_signature("read_file", '{"path": "/a"}', "world")
        assert sig1 != sig2

    def test_key_order_independent(self):
        """JSON key ordering doesn't affect the signature."""
        sig1 = compute_signature("read_file", '{"a": 1, "b": 2}', "hello")
        sig2 = compute_signature("read_file", '{"b": 2, "a": 1}', "hello")
        assert sig1 == sig2

    def test_empty_result(self):
        """Empty result string is a valid input."""
        sig = compute_signature("read_file", '{"path": "/a"}', "")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest

    def test_empty_arguments(self):
        """Empty JSON object arguments is valid."""
        sig = compute_signature("read_file", "{}", "hello")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_malformed_json_fallback(self):
        """Malformed JSON arguments fall back to raw string hashing."""
        sig = compute_signature("read_file", "not-json{", "hello")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_returns_hex_digest(self):
        """Signature is a 64-char hex string (SHA-256)."""
        sig = compute_signature("tool", '{"a": 1}', "result")
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)


# ---------------------------------------------------------------------------
# LoopDetector unit tests
# ---------------------------------------------------------------------------


class TestLoopDetectorBasic:
    """Tests for LoopDetector initialization and basic behavior."""

    def test_defaults_from_settings(self):
        """Detector pulls defaults from settings."""
        detector = LoopDetector()
        assert detector.enabled is True
        assert detector.window_size == 10
        assert detector.threshold == 5

    def test_custom_params_override_settings(self):
        """Explicit params override settings."""
        detector = LoopDetector(window_size=5, threshold=3, enabled=False)
        assert detector.window_size == 5
        assert detector.threshold == 3
        assert detector.enabled is False

    def test_window_starts_empty(self):
        """Window is empty on init."""
        detector = LoopDetector()
        assert len(detector.window) == 0

    def test_reset_clears_window(self):
        """reset() empties the window."""
        detector = LoopDetector()
        detector.record("sig1")
        detector.record("sig2")
        assert len(detector.window) == 2
        detector.reset()
        assert len(detector.window) == 0


class TestLoopDetectorDisabled:
    """Tests for the disabled state."""

    def test_disabled_record_returns_false(self):
        """record() returns False when disabled."""
        detector = LoopDetector(enabled=False)
        assert detector.record("any-sig") is False

    def test_disabled_does_not_track(self):
        """Disabled detector doesn't add to the window."""
        detector = LoopDetector(enabled=False)
        detector.record("sig1")
        detector.record("sig1")
        assert len(detector.window) == 0

    def test_is_looping_returns_false_when_disabled(self):
        """is_looping() returns False when disabled."""
        detector = LoopDetector(enabled=False)
        assert detector.is_looping() is False


class TestLoopDetectorThreshold:
    """Tests for threshold/triggering behavior."""

    def test_no_trigger_below_threshold(self):
        """No trigger when count is at or below threshold."""
        detector = LoopDetector(window_size=10, threshold=5)
        for _ in range(5):
            assert detector.record("same-sig") is False

    def test_triggers_above_threshold(self):
        """Triggers when count exceeds threshold (6th occurrence with threshold=5)."""
        detector = LoopDetector(window_size=10, threshold=5)
        for _ in range(5):
            detector.record("same-sig")
        # 6th occurrence → count=6 > threshold=5 → trigger
        assert detector.record("same-sig") is True

    def test_different_signatures_dont_trigger(self):
        """Mixing different signatures doesn't trigger if no single one exceeds threshold."""
        detector = LoopDetector(window_size=10, threshold=3)
        detector.record("sig-a")
        detector.record("sig-b")
        detector.record("sig-a")
        detector.record("sig-b")
        # Each appears 2 times, threshold=3 → 2 is not > 3
        assert detector.record("sig-a") is False  # 3rd occurrence, count=3, not > 3

    def test_triggers_on_4th_with_threshold_3(self):
        """Triggers when count exceeds threshold=3 (4th occurrence)."""
        detector = LoopDetector(window_size=10, threshold=3)
        for _ in range(3):
            detector.record("same-sig")
        assert detector.record("same-sig") is True  # 4th, count=4 > 3


class TestLoopDetectorRollingWindow:
    """Tests for the rolling-window eviction behavior."""

    def test_window_evicts_oldest(self):
        """Oldest signature is evicted when window is full."""
        detector = LoopDetector(window_size=3, threshold=5)
        detector.record("sig-1")
        detector.record("sig-2")
        detector.record("sig-3")
        detector.record("sig-4")  # should evict sig-1
        assert list(detector.window) == ["sig-2", "sig-3", "sig-4"]

    def test_evicted_signature_no_longer_counts(self):
        """After eviction, old signatures don't count toward triggering."""
        detector = LoopDetector(window_size=3, threshold=2)
        detector.record("sig-a")
        detector.record("sig-a")
        # Now add 3 different sigs to evict sig-a
        detector.record("sig-b")
        detector.record("sig-c")
        detector.record("sig-d")
        # sig-a is gone, only sig-b/c/d remain, each once
        assert detector.window.count("sig-a") == 0

    def test_can_loop_again_after_eviction(self):
        """Same signature can re-accumulate after being evicted."""
        detector = LoopDetector(window_size=3, threshold=2)
        detector.record("sig-x")
        detector.record("sig-x")
        # Evict sig-x by filling window with others
        detector.record("sig-y")
        detector.record("sig-z")
        # Now sig-x is gone, start fresh
        assert detector.record("sig-x") is False  # count=1
        assert detector.record("sig-x") is False  # count=2, not > 2
        assert detector.record("sig-x") is True  # count=3 > 2


# ---------------------------------------------------------------------------
# AgentCore._handle_tools_base integration tests
# ---------------------------------------------------------------------------


class TestLoopDetectionIntegration:
    """Tests for loop detection integration in _handle_tools_base."""

    def test_loop_detector_present_on_agent(self):
        """AgentCore subclasses have a loop_detector attribute."""
        agent = ConcreteAgent()
        assert hasattr(agent, "loop_detector")
        assert isinstance(agent.loop_detector, LoopDetector)

    def test_no_trigger_on_first_tool_call(self):
        """Single tool call doesn't trigger loop detection."""
        agent = ConcreteAgent()

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="result")
        agent.tools = [mock_tool]

        tool_call = make_tool_call()
        response = make_response(tool_calls=[tool_call])

        modified, _ = agent._handle_tools_base([mock_tool], response)

        assert modified is True
        assert (
            agent.loop_detector.window.count(
                compute_signature("test_tool", '{"key": "value"}', "result")
            )
            == 1
        )

    def test_loop_triggers_warning_message(self):
        """When loop is detected, a warning user message is appended to context."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(window_size=10, threshold=2)

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="same-result")
        agent.tools = [mock_tool]

        # Call the same tool 3 times (threshold=2, so 3rd triggers: count=3 > 2)
        for i in range(3):
            tool_call = make_tool_call(call_id=f"call-{i}")
            response = make_response(tool_calls=[tool_call])
            agent._handle_tools_base([mock_tool], response)

        # The last call should have appended a warning user message
        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) >= 1
        assert "loop" in user_msgs[-1]["content"].lower()

    def test_loop_triggers_returns_early(self):
        """When loop is detected, no multimodal parts are returned."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(window_size=10, threshold=1)

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="same-result")
        agent.tools = [mock_tool]

        # First call: count=1, not > 1
        tool_call = make_tool_call(call_id="call-1")
        response = make_response(tool_calls=[tool_call])
        modified, mm = agent._handle_tools_base([mock_tool], response)
        assert modified is True
        assert mm == []

        # Second call: count=2 > 1 → trigger
        tool_call2 = make_tool_call(call_id="call-2")
        response2 = make_response(tool_calls=[tool_call2])
        modified2, mm2 = agent._handle_tools_base([mock_tool], response2)
        # Still True (context was modified), but no multimodal
        assert modified2 is True
        assert mm2 == []

    def test_different_results_dont_trigger(self):
        """Same tool+args but different results don't trigger a loop."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(window_size=10, threshold=2)

        results = iter(["result-1", "result-2", "result-3"])

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(side_effect=lambda **kw: next(results))
        agent.tools = [mock_tool]

        for i in range(3):
            tool_call = make_tool_call(call_id=f"call-{i}")
            response = make_response(tool_calls=[tool_call])
            modified, _ = agent._handle_tools_base([mock_tool], response)
            assert modified is True

        # No warning should have been injected
        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) == 0

    def test_disabled_detector_no_warning(self):
        """When loop detection is disabled, no warning is ever injected."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(enabled=False)

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="same-result")
        agent.tools = [mock_tool]

        for i in range(10):
            tool_call = make_tool_call(call_id=f"call-{i}")
            response = make_response(tool_calls=[tool_call])
            agent._handle_tools_base([mock_tool], response)

        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) == 0

    def test_different_tools_dont_trigger(self):
        """Calling different tools doesn't trigger loop detection."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(window_size=10, threshold=5)

        mock_tool_a = Mock()
        mock_tool_a.name = "tool_a"
        mock_tool_a.validate_and_execute = Mock(return_value="result-a")

        mock_tool_b = Mock()
        mock_tool_b.name = "tool_b"
        mock_tool_b.validate_and_execute = Mock(return_value="result-b")

        agent.tools = [mock_tool_a, mock_tool_b]

        # Alternate between two tools — each appears 3 times, below threshold of 5
        for i in range(6):
            tool = mock_tool_a if i % 2 == 0 else mock_tool_b
            tool_name = "tool_a" if i % 2 == 0 else "tool_b"
            tool_call = make_tool_call(name=tool_name, call_id=f"call-{i}")
            response = make_response(tool_calls=[tool_call])
            agent._handle_tools_base([tool], response)

        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) == 0

    def test_warning_message_content(self):
        """The warning message mentions the loop and suggests a different approach."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(window_size=10, threshold=1)

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="same-result")
        agent.tools = [mock_tool]

        # First call: count=1, not > 1
        tool_call = make_tool_call(call_id="call-1")
        response = make_response(tool_calls=[tool_call])
        agent._handle_tools_base([mock_tool], response)

        # Second call: count=2 > 1 → trigger
        tool_call2 = make_tool_call(call_id="call-2")
        response2 = make_response(tool_calls=[tool_call2])
        agent._handle_tools_base([mock_tool], response2)

        warning_msg = [m for m in agent.context if m.get("role") == ROLE_USER][-1]
        assert "loop" in warning_msg["content"].lower()
        assert "different approach" in warning_msg["content"].lower()


class TestLoopDetectorValidation:
    """Tests for input validation in LoopDetector.__init__."""

    def test_negative_window_size_raises(self):
        """Negative window_size raises ValueError."""
        with pytest.raises(ValueError, match="window_size"):
            LoopDetector(window_size=-1)

    def test_zero_window_size_raises(self):
        """Zero window_size raises ValueError (deque maxlen=0 is useless)."""
        with pytest.raises(ValueError, match="window_size"):
            LoopDetector(window_size=0)

    def test_negative_threshold_raises(self):
        """Negative threshold raises ValueError."""
        with pytest.raises(ValueError, match="threshold"):
            LoopDetector(threshold=-1)


class TestLoopDetectorThresholdZero:
    """Tests for threshold=0 edge case — triggers on first occurrence."""

    def test_threshold_zero_triggers_on_first(self):
        """With threshold=0, the first occurrence triggers (count=1 > 0)."""
        detector = LoopDetector(window_size=10, threshold=0)
        assert detector.record("any-sig") is True

    def test_threshold_zero_triggers_on_any(self):
        """With threshold=0, every new signature triggers."""
        detector = LoopDetector(window_size=10, threshold=0)
        assert detector.record("sig-a") is True
        assert detector.record("sig-b") is True


class TestMultiToolBatchLoopDetection:
    """Tests for loop detection with multiple tool calls in one batch."""

    def test_multiple_identical_calls_in_one_batch_trigger(self):
        """Same tool called N times in a single response triggers loop."""
        agent = ConcreteAgent()
        agent.loop_detector = LoopDetector(window_size=10, threshold=2)

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="same-result")
        agent.tools = [mock_tool]

        # Previous batch: 2 calls → count=2, not > 2
        batch1 = [make_tool_call(call_id="call-1"), make_tool_call(call_id="call-2")]
        response1 = make_response(tool_calls=batch1)
        agent._handle_tools_base([mock_tool], response1)

        # No warning yet
        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) == 0

        # Next batch: 1 more call → count=3 > 2 → trigger
        batch2 = [make_tool_call(call_id="call-3")]
        response2 = make_response(tool_calls=batch2)
        agent._handle_tools_base([mock_tool], response2)

        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) >= 1
        assert "loop" in user_msgs[-1]["content"].lower()

    def test_multiple_identical_calls_trigger_within_batch(self):
        """6 identical calls in a single batch trigger immediately (count=6 > 5)."""
        agent = ConcreteAgent()
        # Use defaults: window=10, threshold=5
        agent.loop_detector = LoopDetector(window_size=10, threshold=5)

        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.validate_and_execute = Mock(return_value="same-result")
        agent.tools = [mock_tool]

        # 6 identical tool calls in a single LLM response
        batch = [make_tool_call(call_id=f"call-{i}") for i in range(6)]
        response = make_response(tool_calls=batch)
        agent._handle_tools_base([mock_tool], response)

        user_msgs = [m for m in agent.context if m.get("role") == ROLE_USER]
        assert len(user_msgs) >= 1
        assert "loop" in user_msgs[-1]["content"].lower()
