"""Tests for the Repl class."""

from unittest.mock import MagicMock, patch

import pytest

from wichy.repl import Repl


class TestRepl:
    """Test suite for Repl."""

    @pytest.fixture
    def mock_root_agent(self):
        """Create a mock RootAgent."""
        agent = MagicMock()
        agent.process.return_value = "Test response"
        agent.reset_context = MagicMock()
        agent.drop_last_context_entry = MagicMock()
        agent.agent_has_first_initiative = False
        return agent

    @pytest.fixture
    def mock_prompt_session(self):
        """Create a mock PromptSession."""
        session = MagicMock()
        session.prompt.return_value = "test input"
        return session

    @pytest.fixture
    def mock_cmd_checker(self):
        """Create a mock SlashCommandChecker."""
        checker = MagicMock()
        checker.check_command.return_value = None
        return checker

    def test_repl_initialization(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl initialization."""
        repl = Repl(
            root_agent=mock_root_agent,
            prompt_session=mock_prompt_session,
            cmd_checker=mock_cmd_checker,
        )
        assert repl.root_agent == mock_root_agent
        assert repl.prompt_session == mock_prompt_session
        assert repl.cmd_checker == mock_cmd_checker

    def test_repl_run_simple_interaction(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl run with a simple interaction."""
        # Simulate user entering one command then EOF
        mock_prompt_session.prompt.side_effect = ["test input", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        mock_root_agent.process.assert_called_once_with("test input")
        mock_cmd_checker.check_command.assert_called_once_with("test input")

    def test_repl_handles_slash_command(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl handles slash commands by skipping them and continuing."""
        # First input is a slash command, second is normal, third is EOF
        mock_cmd_checker.check_command.side_effect = ["/reset result", None]
        mock_prompt_session.prompt.side_effect = ["input1", "input2", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        # process should be called only once (for second input, not first which was a slash command)
        mock_root_agent.process.assert_called_once_with("input2")
        # check_command should be called twice (once per input before EOF)
        assert mock_cmd_checker.check_command.call_count == 2

    def test_repl_handles_context_reset_exception(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl handles ContextResetException and continues."""
        from wichy.slash_commands import ContextResetException

        mock_root_agent.process.side_effect = [
            ContextResetException(strategy="nuke"),
            "response after reset",
        ]
        mock_prompt_session.prompt.side_effect = ["input1", "input2", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        assert mock_root_agent.process.call_count == 2
        mock_root_agent.reset_context.assert_called_once_with(strategy="nuke")

    def test_repl_handles_context_drop_exception(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl handles ContextDropException and continues."""
        from wichy.slash_commands import ContextDropException

        mock_root_agent.process.side_effect = [
            ContextDropException(),
            "response after drop",
        ]
        mock_prompt_session.prompt.side_effect = ["input1", "input2", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        assert mock_root_agent.process.call_count == 2
        mock_root_agent.drop_last_context_entry.assert_called_once()

    def test_repl_handles_llm_context_limit_error(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl handles LLMBackendContextLimitReached and continues."""
        from wichy.llm_backend import LLMBackendContextLimitReached

        mock_root_agent.process.side_effect = [
            LLMBackendContextLimitReached("Context limit reached"),
            "response after error",
        ]
        mock_prompt_session.prompt.side_effect = ["input1", "input2", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        assert mock_root_agent.process.call_count == 2

    def test_repl_handles_keyboard_interrupt(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl handles KeyboardInterrupt and continues."""
        mock_root_agent.process.return_value = "response"
        mock_prompt_session.prompt.side_effect = [
            KeyboardInterrupt,
            "input after interrupt",
            EOFError,
        ]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        assert mock_root_agent.process.call_count == 1

    def test_repl_exits_on_eof(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl exits cleanly on EOFError."""
        mock_prompt_session.prompt.side_effect = EOFError

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        mock_root_agent.process.assert_not_called()

    def test_repl_strip_thinking_content(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test that Repl strips thinking content from responses."""
        mock_root_agent.process.return_value = (
            "Normal text <think>Thinking</think> more text"
        )
        mock_prompt_session.prompt.side_effect = ["input", EOFError]

        # We need to patch strip_thinking_content to verify it's called on the output path
        with patch("wichy.repl.strip_thinking_content") as mock_strip:
            mock_strip.return_value = "Normal text  more text"
            repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

            with pytest.raises(SystemExit):
                repl.run()

            mock_strip.assert_called_once_with(
                "Normal text <think>Thinking</think> more text"
            )

    def test_repl_uses_correct_exit_code_on_eof(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test that REPL exits with code 0 on EOF."""
        mock_prompt_session.prompt.side_effect = EOFError

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit) as exc_info:
            repl.run()

        assert exc_info.value.code == 0

    def test_repl_handles_empty_input(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl skips processing for empty string input."""
        # Empty input followed by EOF
        mock_prompt_session.prompt.side_effect = ["", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        # process should not be called for empty input
        mock_root_agent.process.assert_not_called()

    def test_repl_handles_whitespace_only_input(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl skips processing for whitespace-only input."""
        # Whitespace-only inputs followed by EOF
        mock_prompt_session.prompt.side_effect = ["   ", "\t", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        # process should not be called for whitespace-only inputs
        mock_root_agent.process.assert_not_called()

    def test_repl_processes_valid_input_after_empty_input(
        self, mock_root_agent, mock_prompt_session, mock_cmd_checker
    ):
        """Test Repl processes valid input correctly after empty input."""
        # Empty input, then valid input, then EOF
        mock_prompt_session.prompt.side_effect = ["", "valid input", EOFError]

        repl = Repl(mock_root_agent, mock_prompt_session, mock_cmd_checker)

        with pytest.raises(SystemExit):
            repl.run()

        # process should be called only once (for valid input, not empty)
        mock_root_agent.process.assert_called_once_with("valid input")