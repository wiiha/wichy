"""Integration tests for new lifecycle hooks in RootAgent.process()."""

import json
from unittest.mock import patch

import pytest

from wichy.constants import ROLE_ASSISTANT, ROLE_USER
from wichy.context.handler import context_from_file
from wichy.hooks import (
    clear_hooks,
    pre_response_to_user,
    pre_user_message,
    HookContext,
    HookResult,
)
from wichy.llm_backend import Message
from wichy.root_agent.root_agent import RootAgent


@pytest.fixture(autouse=True)
def setup_hooks():
    """Clear hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


@pytest.fixture
def no_tools():
    """Empty tool list for tests that don't exercise tool calls."""
    return []


def _make_response(content="assistant reply", reasoning=None, usage=None):
    """Build a fake LLM response."""
    return type(
        "LLMResponse",
        (object,),
        {
            "message": Message(
                role=ROLE_ASSISTANT,
                content=content,
                reasoning=reasoning,
                finish_reason="stop",
            ),
            "usage": usage,
        },
    )()


def _make_response_with_none_content(reasoning=None, usage=None):
    """Build a fake LLM response whose message content is None."""
    fake_message = type(
        "FakeMessage",
        (object,),
        {
            "role": ROLE_ASSISTANT,
            "content": None,
            "reasoning": reasoning,
            "finish_reason": "stop",
        },
    )()
    return type(
        "LLMResponse",
        (object,),
        {
            "message": fake_message,
            "usage": usage,
        },
    )()


def _fresh_context(tmp_path, filename="2026-07-03_12345.json"):
    """Create a minimal context file and load it."""
    ctx_path = tmp_path / filename
    ctx_path.write_text(json.dumps({"role": "system", "content": "test context"}) + "\n")
    return context_from_file(str(ctx_path))


class TestPreUserMessageInProcess:
    """PRE_USER_MESSAGE fires during RootAgent.process()."""

    def test_fires_before_message_appended(self, no_tools, tmp_path):
        """Hook runs before the user message reaches context."""
        lengths_seen = []

        @pre_user_message
        def check_length(ctx: HookContext) -> HookResult:
            lengths_seen.append(len(ctx.event_data["context_handler"]))
            return HookResult.approve()

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("hi")
            agent.process("hello")

        assert lengths_seen == [1]  # fresh context has 1 system message before user append
        # After process, the user message + assistant response are in context
        assert len(agent.context) == 3
        last_message = agent.context()[-2]
        assert last_message["role"] == ROLE_USER
        assert last_message["content"] == "hello"

    def test_event_data_contains_message(self, no_tools, tmp_path):
        """Hook receives the exact user message."""
        received = []

        @pre_user_message
        def capture(ctx: HookContext) -> HookResult:
            received.append(ctx.event_data.get("message"))
            return HookResult.approve()

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("hi")
            agent.process("user says this")

        assert received == ["user says this"]


class TestPreResponseToUserInProcess:
    """PRE_RESPONSE_TO_USER fires during RootAgent.process()."""

    def test_fires_after_assistant_append(self, no_tools, tmp_path):
        """Hook runs after assistant message is in context."""
        lengths_seen = []

        @pre_response_to_user
        def check_length(ctx: HookContext) -> HookResult:
            lengths_seen.append(len(ctx.event_data["context_handler"]))
            return HookResult.approve()

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply")
            agent.process("hello")

        assert lengths_seen == [3]  # system + user + assistant

    def test_modify_output_updates_return_and_context(self, no_tools, tmp_path):
        """MODIFY_OUTPUT changes returned value and last context entry."""

        @pre_response_to_user
        def append_prefix(ctx: HookContext) -> HookResult:
            return HookResult.modify_output(f"[prefix] {ctx.event_data['response_content']}")

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply")
            returned = agent.process("hello")

        assert returned == "[prefix] assistant reply"
        last_message = agent.context()[-1]
        assert last_message["role"] == ROLE_ASSISTANT
        assert last_message["content"] == "[prefix] assistant reply"

    def test_modify_output_persists_to_disk(self, no_tools, tmp_path):
        """Modified response is written to the context JSONL file."""

        @pre_response_to_user
        def append_prefix(ctx: HookContext) -> HookResult:
            return HookResult.modify_output(f"[disk] {ctx.event_data['response_content']}")

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply")
            agent.process("hello")

        # Read the file directly
        ctx_path = agent.context.path
        lines = ctx_path.read_text(encoding="utf-8").strip().split("\n")
        messages = [json.loads(line) for line in lines if json.loads(line).get("type") != "log"]
        assert messages[-1]["role"] == ROLE_ASSISTANT
        assert messages[-1]["content"] == "[disk] assistant reply"

    def test_none_content_returned_unless_modified(self, no_tools, tmp_path):
        """When LLM content is None and no hook modifies, return None."""

        @pre_response_to_user
        def do_nothing(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response_with_none_content()
            returned = agent.process("hello")

        assert returned is None
        last_message = agent.context()[-1]
        assert last_message["role"] == ROLE_ASSISTANT
        assert last_message["content"] is None

    def test_none_content_can_be_modified_to_string(self, no_tools, tmp_path):
        """A hook can turn None content into a string."""

        @pre_response_to_user
        def fill_none(ctx: HookContext) -> HookResult:
            if ctx.event_data["response_content"] is None:
                return HookResult.modify_output("(no content)")
            return HookResult.approve()

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response_with_none_content()
            returned = agent.process("hello")

        assert returned == "(no content)"
        last_message = agent.context()[-1]
        assert last_message["content"] == "(no content)"

    def test_reasoning_preserved_on_modify(self, no_tools, tmp_path):
        """Reasoning is preserved when content is modified."""

        @pre_response_to_user
        def append_prefix(ctx: HookContext) -> HookResult:
            return HookResult.modify_output("modified")

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply", reasoning="because")
            agent.process("hello")

        # __call__() strips reasoning, so read from disk to verify persistence
        ctx_path = agent.context.path
        lines = ctx_path.read_text(encoding="utf-8").strip().split("\n")
        messages = [json.loads(line) for line in lines if json.loads(line).get("type") != "log"]
        last_message = messages[-1]
        assert last_message["content"] == "modified"
        assert last_message.get("reasoning") == "because"

    def test_deny_is_noop(self, no_tools, tmp_path):
        """DENY action on PRE_RESPONSE_TO_USER does not block response."""

        @pre_response_to_user
        def try_deny(ctx: HookContext) -> HookResult:
            return HookResult.deny("should be ignored")

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply")
            returned = agent.process("hello")

        assert returned == "assistant reply"


class TestHookFailureSurvival:
    """Hook failures do not break process()."""

    def test_pre_user_message_exception_does_not_break_process(self, no_tools, tmp_path):
        """PRE_USER_MESSAGE hook exception is logged; process continues."""

        @pre_user_message
        def explode(ctx: HookContext) -> HookResult:
            raise RuntimeError("boom")

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply")
            returned = agent.process("hello")

        assert returned == "assistant reply"

    def test_pre_response_exception_preserves_earlier_modify(self, no_tools, tmp_path):
        """If a later modify hook raises, earlier modification is preserved."""

        @pre_response_to_user(priority=10)
        def first_modify(ctx: HookContext) -> HookResult:
            return HookResult.modify_output("earlier")

        @pre_response_to_user(priority=20)
        def explode(ctx: HookContext) -> HookResult:
            raise RuntimeError("boom")

        agent = RootAgent(
            model_str="test-model",
            tools=no_tools,
            context=_fresh_context(tmp_path),
            print_info_lines=False,
        )

        with patch("wichy.root_agent.root_agent.call") as mock_call:
            mock_call.return_value = _make_response("assistant reply")
            returned = agent.process("hello")

        assert returned == "earlier"
        assert agent.context()[-1]["content"] == "earlier"
