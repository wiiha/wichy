"""
Test cases for the require_human_verification decorator.
"""

import pytest
from unittest.mock import patch

import wichy.tools.human_verification as hv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_call_count = 0


def make_target(name="default"):
    """Create a fresh decorated function with a unique call log."""

    @hv.require_human_verification
    def target(msg: str = ""):
        global _call_count
        _call_count += 1
        return f"called {name} ({msg})"

    target._action_label = name  # used by decorator label resolution
    return target


def clear_call_count():
    global _call_count
    _call_count = 0


# ---------------------------------------------------------------------------
# PIPELINE_MODE
# ---------------------------------------------------------------------------


class TestPipelineMode:
    """Tests for PIPELINE_MODE flag behavior via in_pipeline_mode()."""

    def setup_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False
        clear_call_count()

    def teardown_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False

    def test_pipeline_mode_raises_permission_error(self):
        """PIPELINE_MODE=True raises PermissionError immediately without prompting."""
        hv.PIPELINE_MODE = True
        target = make_target()

        with pytest.raises(PermissionError) as exc_info:
            target("arg")

        assert "pipeline mode" in str(exc_info.value).lower()
        assert _call_count == 0  # function was never called

    def test_pipeline_mode_includes_tool_name_and_args_in_error(self):
        """The error message includes the tool name and the attempted arguments."""
        hv.PIPELINE_MODE = True
        target = make_target("my_tool")

        with pytest.raises(PermissionError) as exc_info:
            target("hello", extra="world")

        err = str(exc_info.value)
        assert "my_tool" in err
        assert "pipeline mode" in err.lower()
        assert "hello" in err

    def test_pipeline_mode_uses_label_as_fallback_when_no_self(self):
        """When args[0] has no .name, label is used instead."""
        hv.PIPELINE_MODE = True
        target = make_target("fallback_tool")

        with pytest.raises(PermissionError) as exc_info:
            target("hello")  # args[0] is "hello" (a string), not a tool

        err = str(exc_info.value)
        assert "fallback_tool" in err

    def test_skip_flag_takes_precedence_over_pipeline_mode(self):
        """SKIP_HUMAN_VERIFICATION=True skips even when PIPELINE_MODE is also True."""
        hv.PIPELINE_MODE = True
        hv.SKIP_HUMAN_VERIFICATION = True
        target = make_target()

        result = target()

        # SKIP wins: function is called, no PermissionError raised
        assert result == "called default ()"
        assert _call_count == 1


# ---------------------------------------------------------------------------
# SKIP_HUMAN_VERIFICATION
# ---------------------------------------------------------------------------


class TestSkipHumanVerification:
    """Tests for SKIP_HUMAN_VERIFICATION flag behavior."""

    def setup_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False
        clear_call_count()

    def teardown_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False

    def test_skip_flag_calls_function_without_prompting(self):
        """SKIP_HUMAN_VERIFICATION=True bypasses the prompt entirely."""
        hv.SKIP_HUMAN_VERIFICATION = True
        target = make_target()

        result = target("hello")

        assert result == "called default (hello)"
        assert _call_count == 1

    def test_skip_flag_respects_should_verify_predicate(self):
        """Even with SKIP=True, _should_verify can still force verification."""
        hv.SKIP_HUMAN_VERIFICATION = True
        target = make_target()
        target._should_verify = lambda *a, **kw: True  # force verify

        with patch.object(hv, "prompt_session") as mock_session:
            mock_session.prompt.return_value = "y"
            _ = target("hello")

        assert _call_count == 1


# ---------------------------------------------------------------------------
# _should_verify predicate
# ---------------------------------------------------------------------------


class TestShouldVerifyPredicate:
    """Tests for the _should_verify predicate mechanism."""

    def setup_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False
        clear_call_count()

    def teardown_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False

    def test_predicate_false_skips_verification(self):
        """_should_verify returning False skips the prompt and calls the function."""
        target = make_target()
        target._should_verify = lambda *a, **kw: False

        result = target("skipped")

        assert result == "called default (skipped)"
        assert _call_count == 1

    def test_predicate_true_requires_verification(self):
        """_should_verify returning True triggers the prompt."""
        target = make_target()
        target._should_verify = lambda *a, **kw: True

        with patch.object(hv, "prompt_session") as mock_session:
            mock_session.prompt.return_value = "y"
            _ = target("verified")

        assert _call_count == 1

    def test_predicate_receives_function_args(self):
        """The predicate receives the same args/kwargs as the decorated function."""
        received = {}

        target = make_target()
        target._should_verify = (
            lambda *args, **kwargs: received.update({"args": args, "kwargs": kwargs})
            or False
        )

        target("foo")

        assert received["args"] == ("foo",)
        assert received["kwargs"] == {}

    def test_predicate_exception_errs_on_side_of_caution(self):
        """If the predicate raises, verification is triggered (not skipped)."""
        target = make_target()
        target._should_verify = lambda *a, **kw: (_ for _ in ()).throw(
            ValueError("boom")
        )

        with patch.object(hv, "prompt_session") as mock_session:
            mock_session.prompt.return_value = "y"
            _ = target("cautious")

        assert _call_count == 1


# ---------------------------------------------------------------------------
# y/n prompt behavior
# ---------------------------------------------------------------------------


class TestPromptBehavior:
    """Tests for the interactive y/n prompt flow."""

    def setup_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False
        clear_call_count()

    def teardown_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False

    def test_yes_variants_call_function(self):
        """Any response starting with 'y' grants execution."""
        for response in ("y", "Y", "yes", "YES", "y ", "  yes  "):
            clear_call_count()
            target = make_target()

            with patch.object(hv, "prompt_session") as mock_session:
                mock_session.prompt.return_value = response
                _ = target("test")

            assert _call_count == 1, f"Failed for response: {response!r}"

    def test_no_variants_raise_permission_error(self):
        """Any response starting with 'n' raises PermissionError."""
        for response in ("n", "N", "no", "NO", "n ", "  no  "):
            clear_call_count()
            target = make_target()

            with patch.object(hv, "prompt_session") as mock_session:
                mock_session.prompt.return_value = response

                with pytest.raises(PermissionError) as exc_info:
                    target("test")

            assert _call_count == 0, f"Failed for response: {response!r}"
            assert "denied" in str(exc_info.value).lower()

    def test_no_with_reason_includes_reason_in_error(self):
        """'n, reason here' includes the reason in the PermissionError message."""
        target = make_target()

        with patch.object(hv, "prompt_session") as mock_session:
            mock_session.prompt.return_value = "n, I need to check something first"

            with pytest.raises(PermissionError) as exc_info:
                target("test")

        assert "Reason for denied execution" in str(exc_info.value)
        assert "i need to check something first" in str(exc_info.value)
        assert "reason" in str(exc_info.value).lower()

    def test_invalid_input_reprompts(self):
        """Invalid responses (not y/n) re-prompt without executing."""
        target = make_target()

        responses = iter(["maybe", "hmm", "y"])  # third one is valid
        with patch.object(hv, "prompt_session") as mock_session:
            mock_session.prompt.side_effect = lambda _: next(responses)

            _ = target("test")

        assert mock_session.prompt.call_count == 3
        assert _call_count == 1


# ---------------------------------------------------------------------------
# Label and message resolution
# ---------------------------------------------------------------------------


class TestLabelResolution:
    """Tests for _action_label and _action_message resolution."""

    def setup_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False
        clear_call_count()

    def teardown_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False

    def test_explicit_action_label_used_in_pipeline_error(self):
        """Explicit _action_label takes priority over docstring and function name."""
        hv.PIPELINE_MODE = True

        @hv.require_human_verification
        def do_something(path: str):
            """Delete the world"""
            return "done"

        do_something._action_label = "Custom Label From Test"

        with pytest.raises(PermissionError) as exc_info:
            do_something("/")

        assert "Custom Label From Test" in str(exc_info.value)

    def test_explicit_action_message_printed(self):
        """_action_message is included in the printed prompt."""

        @hv.require_human_verification
        def do_dangerous(path: str):
            """Do dangerous thing"""
            return "done"

        do_dangerous._action_label = "Danger"
        do_dangerous._action_message = "This action cannot be undone."

        with patch.object(hv, "prompt_session") as mock_session:
            with patch.object(hv, "needs_user_attention"):
                with patch.object(hv.user_console, "print") as mock_print:
                    mock_session.prompt.return_value = "y"
                    do_dangerous("/var/data")

        printed = [str(c) for c in mock_print.call_args_list]
        printed_str = " ".join(printed)
        assert "This action cannot be undone" in printed_str


# ---------------------------------------------------------------------------
# Block_on decorator (regression + new PIPELINE_MODE coverage)
# ---------------------------------------------------------------------------


class TestBlockOnDecorator:
    """Tests for the block_on decorator."""

    def setup_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False
        clear_call_count()

    def teardown_method(self):
        hv.PIPELINE_MODE = False
        hv.SKIP_HUMAN_VERIFICATION = False

    def test_block_on_blocks_and_does_not_call_function(self):
        """block_on raises PermissionError and does not call the function."""

        class MockTool:
            def should_block(self, cmd: str) -> tuple[bool, str | None]:
                return True, "blocked"

            @hv.block_on(should_block)
            def execute(self, cmd: str) -> str:
                global _call_count
                _call_count += 1
                return "executed"

        tool = MockTool()
        with pytest.raises(PermissionError) as exc:
            tool.execute("ls")
        assert "blocked" in str(exc.value)
        assert _call_count == 0

    def test_block_on_allows_execution_when_not_blocked(self):
        """block_on allows the function to execute when decision returns False."""

        class MockTool:
            def should_block(self, cmd: str) -> tuple[bool, str | None]:
                return False, None

            @hv.block_on(should_block)
            def execute(self, cmd: str) -> str:
                global _call_count
                _call_count += 1
                return f"ran: {cmd}"

        tool = MockTool()
        result = tool.execute("ls")
        assert result == "ran: ls"
        assert _call_count == 1
