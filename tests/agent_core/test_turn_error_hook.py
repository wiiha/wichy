"""Tests for the ON_TURN_ERROR hook and the turn_failed event.

Turns are driven through the real ``AgentCore.turn_scope``; the raised
exception must come back out unchanged. Hook dispatch goes through the real
``HookExecutor`` and registry, never a hand-supplied mock.
"""

from unittest.mock import MagicMock, patch

import pytest

from wichy.agent.core import AgentCore
from wichy.hooks import HookResult, clear_hooks, on_turn_error
from wichy.llm_backend import LLMBackendMultimodalNotSupported
from wichy.root_agent.root_agent import RootAgent
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.task import TaskAgent
from wichy.tools.task.base import TaskAgentDefinitionBase


class _FakeContext:
    def __init__(self, session_id="sess-1", custom_suffix=""):
        self.session_id = session_id
        self.custom_suffix = custom_suffix


class _StubAgent(AgentCore):
    """Concrete agent whose _emit_event is captured instead of persisted."""

    def __init__(self, session_id="sess-1", custom_suffix="", with_context=True):
        super().__init__()
        self._name = "stub"
        self.model_str = "model-x"
        if with_context:
            self.context = _FakeContext(session_id, custom_suffix)
        self.emitted = []

    @property
    def name(self) -> str:
        return self._name

    def _emit_event(self, event_type, payload):
        self.emitted.append((event_type, payload))

    def run_turn(self, fn):
        with self.turn_scope():
            return fn()


@pytest.fixture(autouse=True)
def _clean_hooks():
    clear_hooks()
    yield
    clear_hooks()


def _fail(agent, exc=None):
    exc = exc or RuntimeError("boom")
    with pytest.raises(type(exc)) as info:
        agent.run_turn(lambda: (_ for _ in ()).throw(exc))
    return info.value


# -- INV-008: fires exactly once per failed turn ----------------------------


def test_fires_once_on_failed_turn():
    seen = []

    @on_turn_error
    def observe(ctx):
        seen.append(dict(ctx.event_data))
        return HookResult.approve()

    agent = _StubAgent()
    _fail(agent)
    assert len(seen) == 1
    assert seen[0]["error_type"] == "RuntimeError"
    assert seen[0]["error_message"] == "boom"
    assert seen[0]["agent_name"] == "stub"
    assert seen[0]["agent_id"] == "root"
    assert seen[0]["model_str"] == "model-x"
    assert seen[0]["session_id"] == "sess-1"


def test_does_not_fire_on_success():
    seen = []

    @on_turn_error
    def observe(ctx):
        seen.append(ctx)
        return HookResult.approve()

    _StubAgent().run_turn(lambda: "ok")
    assert seen == []


def test_nested_turns_fire_twice_by_agent_id():
    seen = []

    @on_turn_error
    def observe(ctx):
        seen.append(ctx.event_data["agent_id"])
        return HookResult.approve()

    outer = _StubAgent(custom_suffix="")
    inner = _StubAgent(custom_suffix="task-abc")

    def nested():
        inner.run_turn(lambda: (_ for _ in ()).throw(RuntimeError("inner")))

    with pytest.raises(RuntimeError, match="inner"):
        outer.run_turn(nested)

    assert seen == ["task-abc", "root"]


# -- INV-009: only Exceptions through the yield arm -------------------------


def test_turn_failed_event_emitted():
    agent = _StubAgent()
    _fail(agent, ValueError("bad"))
    events = [e for e in agent.emitted if e[0] == "turn_failed"]
    assert len(events) == 1
    payload = events[0][1]
    assert payload["error_type"] == "ValueError"
    assert payload["error_message"] == "bad"
    assert payload["session_id"] == "sess-1"


def test_base_exception_does_not_fire():
    seen = []

    @on_turn_error
    def observe(ctx):
        seen.append(ctx)
        return HookResult.approve()

    agent = _StubAgent()
    with pytest.raises(KeyboardInterrupt):
        agent.run_turn(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert seen == []
    assert [e for e in agent.emitted if e[0] == "turn_failed"] == []


# -- INV-010: identity / args / traceback preserved -------------------------


def test_exception_identity_and_traceback_preserved():
    original = RuntimeError("keep me")

    @on_turn_error
    def observe(ctx):
        return HookResult.approve()

    agent = _StubAgent()
    try:
        agent.run_turn(lambda: (_ for _ in ()).throw(original))
    except RuntimeError as e:
        assert e is original
        assert e.args == ("keep me",)
        assert e.__traceback__ is not None
    else:  # pragma: no cover
        pytest.fail("exception was swallowed")


def test_turn_failed_traceback_contains_frame_and_is_bounded():
    agent = _StubAgent()
    _fail(agent, RuntimeError("x" * 4000))
    payload = [p for e, p in agent.emitted if e == "turn_failed"][0]
    assert "RuntimeError" in payload["traceback"]
    assert len(payload["traceback"]) <= 2000
    assert len(payload["error_message"]) == 500


# -- INV-011/012: result ignored, hook exceptions isolated ------------------


@pytest.mark.parametrize(
    "result",
    [HookResult.approve(), HookResult.deny("no"), HookResult.modify_output("x"), None],
)
def test_hook_result_is_ignored(result):
    @on_turn_error
    def observe(ctx):
        return result

    agent = _StubAgent()
    original = RuntimeError("original")
    try:
        agent.run_turn(lambda: (_ for _ in ()).throw(original))
    except RuntimeError as e:
        assert e is original
    else:  # pragma: no cover
        pytest.fail("result changed the exception")


def test_raising_hook_keeps_original_exception():
    @on_turn_error
    def boom(ctx):
        raise RuntimeError("hook exploded")

    agent = _StubAgent()
    original = ValueError("original")
    try:
        agent.run_turn(lambda: (_ for _ in ()).throw(original))
    except ValueError as e:
        assert e is original
    else:  # pragma: no cover
        pytest.fail("hook masked the original exception")


# -- AMEND-2: a context-less agent must not raise AttributeError ------------


def test_contextless_agent_preserves_original_exception():
    seen = []

    @on_turn_error
    def observe(ctx):
        seen.append(ctx)
        return HookResult.approve()

    agent = _StubAgent(with_context=False)
    original = RuntimeError("no context here")
    try:
        agent.run_turn(lambda: (_ for _ in ()).throw(original))
    except RuntimeError as e:
        assert e is original
    else:  # pragma: no cover
        pytest.fail("original exception lost")
    # The hook and event must still fire for an agent with no context -- a
    # bare `self.context` read would silently skip them.
    assert len(seen) == 1
    assert seen[0].event_data["session_id"] is None
    assert [t for t, _ in agent.emitted] == ["turn_failed"]


# -- INV-023: dispatch goes through the registry ----------------------------


def test_dispatch_bypassing_executor_never_fires():
    seen = []

    @on_turn_error
    def observe(ctx):
        seen.append(ctx)
        return HookResult.approve()

    from wichy.hooks.executor import HookExecutor

    original = HookExecutor.run_context_hooks
    HookExecutor.run_context_hooks = staticmethod(lambda *a, **k: None)
    try:
        _fail(_StubAgent())
    finally:
        HookExecutor.run_context_hooks = original
    assert seen == []


# -- INV-013: the hooks import is lazy --------------------------------------


def test_hooks_import_is_local_to_on_turn_error():
    """A module-top import would bind these names on agent.core.

    The import chain makes ``sys.modules`` useless here (importing the package
    already pulls in wichy.hooks), so the falsifiable signal is that the names
    are NOT module attributes of wichy.agent.core.
    """
    from wichy.agent import core as core_module

    assert not hasattr(core_module, "HookExecutor")
    assert not hasattr(core_module, "HookType")


# -- Stage 5: llm_call_failed at the call sites (INV-014, INV-015, INV-022) --


class _Params(ParametersModel):
    pass


class _MockTool(BaseTool):
    name: str = "mock_tool"
    description: str = "a mock tool"
    parameters_model = _Params

    def execute(self, **kwargs) -> str:
        return "ok"


def _root_agent():
    context = MagicMock()
    context.append = MagicMock()
    context.add_log = MagicMock()
    context.__len__ = MagicMock(return_value=3)
    context.context = [{"role": "user", "content": "hi"}]
    context.session_id = "sess-1"
    context.custom_suffix = ""
    agent = RootAgent(
        model_str="ollama/test",
        tools=[_MockTool()],
        context=context,
        name="test-agent",
        agent_has_first_initiative=False,
    )
    agent.emitted = []
    agent._emit_event = lambda t, p: agent.emitted.append((t, p))
    return agent


def _llm_events(agent):
    return [t for t, _ in agent.emitted if t.startswith("llm_call_")]


def test_primary_failure_emits_exactly_one_failed_no_completed():
    agent = _root_agent()
    with patch("wichy.root_agent.root_agent.call", side_effect=RuntimeError("down")):
        with pytest.raises(RuntimeError, match="down"):
            agent.process("hello")
    assert _llm_events(agent).count("llm_call_failed") == 1
    assert "llm_call_completed" not in _llm_events(agent)


def test_success_emits_no_failed():
    agent = _root_agent()
    response = MagicMock()
    response.message.finish_reason = "stop"
    response.message.tool_calls = None
    response.message.reasoning = None
    response.usage = None
    with patch("wichy.root_agent.root_agent.call", return_value=response):
        agent.process("hello")
    assert "llm_call_failed" not in _llm_events(agent)


def test_failed_payload_shape_and_truncation():
    agent = _root_agent()
    with patch("wichy.root_agent.root_agent.call", side_effect=RuntimeError("e" * 900)):
        with pytest.raises(RuntimeError):
            agent.process("hello")
    payload = [p for t, p in agent.emitted if t == "llm_call_failed"][0]
    assert payload["error_type"] == "RuntimeError"
    assert len(payload["error_message"]) <= 500
    assert payload["model_str"] == "ollama/test"
    assert payload["message_count"] >= 0
    assert payload["tool_count"] == 1


def test_multimodal_fixed_emits_no_failed_then_completed():
    """A corrected multimodal call is not an LLM-call failure."""
    agent = _root_agent()
    agent._fix_multimodal_context = lambda: True
    response = MagicMock()
    response.message.finish_reason = "stop"
    response.message.tool_calls = None
    response.message.reasoning = None
    response.usage = None
    exc = LLMBackendMultimodalNotSupported("no images")
    with patch("wichy.root_agent.root_agent.call", side_effect=[exc, response]):
        agent.process("hello")
    events = _llm_events(agent)
    assert "llm_call_failed" not in events
    assert events.count("llm_call_completed") == 1


def test_multimodal_retry_real_failure_is_not_an_llm_call_failure():
    agent = _root_agent()
    agent._fix_multimodal_context = lambda: True
    exc = LLMBackendMultimodalNotSupported("no images")
    with patch(
        "wichy.root_agent.root_agent.call", side_effect=[exc, ValueError("again")]
    ):
        with pytest.raises(ValueError, match="again"):
            agent.process("hello")
    assert "llm_call_failed" not in _llm_events(agent)


def test_multimodal_unfixable_emits_no_failed():
    agent = _root_agent()
    agent._fix_multimodal_context = lambda: False
    exc = LLMBackendMultimodalNotSupported("no images")
    with patch("wichy.root_agent.root_agent.call", side_effect=exc):
        with pytest.raises(LLMBackendMultimodalNotSupported):
            agent.process("hello")
    assert "llm_call_failed" not in _llm_events(agent)


def _task_agent():
    agent = TaskAgent(
        agent_definition=TaskAgentDefinitionBase(
            name="coder", description="c", system_prompt="s"
        ),
        prompt="do it",
        model="test/model",
        all_tools_not_instantiated=[],
        max_turns=1,
    )
    agent.emitted = []
    agent._emit_event = lambda t, p: agent.emitted.append((t, p))
    return agent


def test_task_primary_failure_emits_failed():
    agent = _task_agent()
    with patch("wichy.tools.task.base.call", side_effect=RuntimeError("task down")):
        with pytest.raises(RuntimeError, match="task down"):
            agent._process("hello")
    assert _llm_events(agent).count("llm_call_failed") == 1
    assert "llm_call_completed" not in _llm_events(agent)


# -- INV-022: one writer thread per store, not one per emit -----------------


def test_emission_does_not_spawn_per_call_writers():
    from wichy.event_log import get_event_store

    store = get_event_store("inv022-sess")
    try:
        writer_ids = set()
        for i in range(3):
            store.emit("turn_failed", {"i": i})
            writer_ids.add(id(store._thread))
        assert len(writer_ids) == 1
        assert all(w is not None for w in writer_ids)
    finally:
        store.close(timeout=2.0)
