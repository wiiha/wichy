"""Tests for pipeline mode (--prompt flag) in main()."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from wichy.constants import ROLE_ASSISTANT, ROLE_USER


@pytest.fixture
def mock_settings():
    """Create a mock settings object with required attributes."""
    settings = MagicMock()
    settings.wake_up_message = (
        "You just woke up. "
        "Perform any tasks you deem necessary before interacting further with the user."
    )
    settings.history_file = MagicMock()
    settings.contexts_dir = MagicMock()
    return settings


@pytest.fixture
def mock_root_agent():
    """Create a mock root agent for pipeline mode tests."""
    agent = MagicMock()
    agent.process.return_value = "Done."
    agent.agent_has_first_initiative = True
    agent.context = MagicMock()
    agent.context.return_value = []  # the underlying message list
    agent.context.append = MagicMock()
    return agent


class TestPipelineMode:
    """Test suite for pipeline mode behavior in main()."""

    def _run_pipeline(self, argv, mock_settings, mock_root_agent, extra_patches=None):
        """Run main() in pipeline mode, suppressing SystemExit."""
        extra_patches = extra_patches or []
        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                for patch_target, patch_value in extra_patches:
                    patcher = patch(patch_target, patch_value)
                    patcher.start()
                    extra_patches.append((patch_target, patcher.stop))
                with patch.object(sys, "argv", argv):
                    with pytest.raises(SystemExit):
                        from wichy.__main__ import main

                        main()

    def test_pipeline_mode_skips_server(self, mock_settings, mock_root_agent):
        """When --prompt is given, start_server is NOT called."""
        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch(
                "wichy.__main__.start_server_in_background"
            ) as mock_start_server:
                with patch.object(
                    sys, "argv", ["wichy", "--prompt", "hi", "--no-server"]
                ):
                    with pytest.raises(SystemExit):
                        from wichy.__main__ import main

                        main()

                mock_start_server.assert_not_called()

    def test_pipeline_mode_agent_has_first_initiative_calls_wake_up_then_prompt(
        self, mock_settings, mock_root_agent
    ):
        """With agent_has_first_initiative=True, process is called twice."""
        mock_root_agent.agent_has_first_initiative = True

        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch.object(
                    sys, "argv", ["wichy", "--prompt", "fix the bug", "--no-server"]
                ):
                    with pytest.raises(SystemExit):
                        from wichy.__main__ import main

                        main()

        assert mock_root_agent.process.call_count == 2
        mock_root_agent.process.assert_any_call(mock_settings.wake_up_message)
        mock_root_agent.process.assert_any_call("fix the bug")

    def test_pipeline_mode_first_flag_skips_wake_up(
        self, mock_settings, mock_root_agent
    ):
        """With --first (agent_has_first_initiative=False), process is called once."""
        mock_root_agent.agent_has_first_initiative = False

        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch.object(
                    sys,
                    "argv",
                    ["wichy", "--prompt", "fix the bug", "--no-server", "--first"],
                ):
                    with pytest.raises(SystemExit):
                        from wichy.__main__ import main

                        main()

        mock_root_agent.process.assert_called_once_with("fix the bug")

    def test_pipeline_mode_preamble_appended_to_context(
        self, mock_settings, mock_root_agent
    ):
        """Pipeline preamble is appended to context as ROLE_USER, then prompt is processed."""
        mock_root_agent.agent_has_first_initiative = False

        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch.object(
                    sys, "argv", ["wichy", "--prompt", "hello", "--no-server"]
                ):
                    with pytest.raises(SystemExit):
                        from wichy.__main__ import main

                        main()

        mock_root_agent.context.append.assert_called_once()
        call_args = mock_root_agent.context.append.call_args[0][0]
        assert call_args["role"] == ROLE_USER
        assert "pipeline mode" in call_args["content"]
        mock_root_agent.process.assert_called_once_with("hello")

    def test_pipeline_mode_output_to_stdout(self, mock_settings, mock_root_agent):
        """The stripped agent response is written to sys.stdout."""
        raw_response = "<<thinking>>Hello!<<>>"
        clean_response = "Hello!"
        mock_root_agent.process.return_value = raw_response

        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch(
                    "wichy.__main__.strip_thinking_content", return_value=clean_response
                ):
                    with patch(
                        "wichy.__main__.sys.stdout", new_callable=MagicMock
                    ) as mock_stdout:
                        with patch.object(
                            sys,
                            "argv",
                            ["wichy", "--prompt", "say hello", "--no-server"],
                        ):
                            with pytest.raises(SystemExit):
                                from wichy.__main__ import main

                                main()

                        mock_stdout.write.assert_called_with(clean_response)

    def test_pipeline_mode_exits_zero(self, mock_settings, mock_root_agent):
        """Pipeline mode exits with code 0."""
        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch.object(
                    sys, "argv", ["wichy", "--prompt", "do it", "--no-server"]
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        from wichy.__main__ import main

                        main()

                    assert exc_info.value.code == 0

    def test_pipeline_mode_strip_thinking_content_called(
        self, mock_settings, mock_root_agent
    ):
        """strip_thinking_content is called on the agent response before writing to stdout."""
        raw_response = "<<thinking>>Result<<>>"
        clean_response = "Result without thinking"
        mock_root_agent.process.return_value = raw_response

        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch(
                    "wichy.__main__.strip_thinking_content", return_value=clean_response
                ) as mock_strip:
                    with patch.object(
                        sys, "argv", ["wichy", "--prompt", "go", "--no-server"]
                    ):
                        with pytest.raises(SystemExit):
                            from wichy.__main__ import main

                            main()

                        mock_strip.assert_called_once_with(raw_response)

    def test_pipeline_mode_sets_pipeline_mode_and_quiet(
        self, mock_settings, mock_root_agent
    ):
        """set_pipeline_mode(True) and set_user_output_quiet(True) are called."""
        with patch(
            "wichy.__main__.build_agent_from_config", return_value=mock_root_agent
        ):
            with patch("wichy.__main__.settings", mock_settings):
                with patch(
                    "wichy.__main__.human_verification.set_pipeline_mode"
                ) as mock_set_pipeline:
                    with patch("wichy.__main__.set_user_output_quiet") as mock_quiet:
                        with patch.object(
                            sys, "argv", ["wichy", "--prompt", "go", "--no-server"]
                        ):
                            with pytest.raises(SystemExit):
                                from wichy.__main__ import main

                                main()

                        mock_set_pipeline.assert_called_once_with(True)
                        mock_quiet.assert_called_once_with(True)

    def test_pipeline_mode_skips_preamble_when_context_already_has_it(
        self, mock_settings
    ):
        """When the loaded context already contains the pipeline system note, preamble is not appended."""
        agent = MagicMock()
        agent.process.return_value = "Done."
        agent.agent_has_first_initiative = False
        # Simulate context was loaded via --load-ctx / --last-ctx and already contains the note
        mock_ctx = MagicMock()
        mock_ctx.return_value = [
            {
                "role": ROLE_USER,
                "content": "[System note: Running in pipeline mode. ...]",
            },
            {"role": ROLE_ASSISTANT, "content": "Understood."},
        ]
        agent.context = mock_ctx
        agent.context.append = MagicMock()

        with patch("wichy.__main__.build_agent_from_config", return_value=agent):
            with patch("wichy.__main__.context_from_file", return_value=MagicMock()):
                with patch("wichy.__main__.settings", mock_settings):
                    with patch.object(
                        sys,
                        "argv",
                        [
                            "wichy",
                            "--prompt",
                            "hello",
                            "--no-server",
                            "--load-ctx",
                            "foo.json",
                        ],
                    ):
                        with pytest.raises(SystemExit):
                            from wichy.__main__ import main

                            main()

        agent.context.append.assert_not_called()
        agent.process.assert_called_once_with("hello")
