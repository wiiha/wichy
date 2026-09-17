"""
Test cases for the BashTool.

Covers:
- basic command execution and output formatting (exit codes, no-output)
- timeout handling: the whole process group is killed on timeout
  (no orphaned children), timed-out output is still returned
- kill-registry integration: the Popen handle is registered with the
  call's kill record, and a force-kill SIGKILLs the process group so
  children do not survive and the blocked communicate() returns
  promptly with the kill result swapped in by validate_and_execute
"""

import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from wichy.config.settings import settings
from wichy.tools import kill_registry
from wichy.tools.bash import BashTool
from wichy.tools.kill_registry import _reset_for_tests, is_in_flight


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the process-global kill registry per test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def bash_tool(monkeypatch):
    """Fixture to create a fresh BashTool instance for each test."""
    # Monkey patch away the need for human verification
    monkeypatch.setattr(settings, "skip_human_verification", True)
    return BashTool()


def test_create_task(bash_tool):
    """Test known problematic command"""
    result = bash_tool.execute(command='find . -name "*test*" | grep bash', timeout=30)
    assert result.strip() != ""
    assert "find: |: unknown primary or operator" not in result
    assert "test_bash.py" in result


class TestOutputFormatting:
    """Output contract of BashTool._format_output."""

    def test_successful_command_with_output(self, bash_tool):
        result = bash_tool.execute(command="echo hello", timeout=10)
        assert result.strip() == "hello"

    def test_successful_command_without_output(self, bash_tool):
        result = bash_tool.execute(command="true", timeout=10)
        assert result.strip() == "[exit code: 0]"

    def test_failing_command_shows_exit_code(self, bash_tool):
        result = bash_tool.execute(command="exit 3", timeout=10)
        assert "[exit code: 3]" in result

    def test_failing_command_with_output_shows_both(self, bash_tool):
        result = bash_tool.execute(command="echo boom && exit 2", timeout=10)
        assert "boom" in result
        assert "[exit code: 2]" in result

    def test_stderr_is_merged_into_output(self, bash_tool):
        result = bash_tool.execute(command="echo oops 1>&2", timeout=10)
        assert "oops" in result


class TestTimeoutProcessGroupKill:
    """Timeout kills the whole process group; children never orphan."""

    def test_timeout_kills_process_group_with_children(self, bash_tool):
        # sleep chains: shell -> sleep 100 -> if the group kill misses,
        # the sleep would linger and the second communicate would hang.
        result = bash_tool.execute(command="sleep 100 && echo unreachable", timeout=1)
        assert "[timed out]" in result
        assert "unreachable" not in result

    def test_timeout_returns_partial_output(self, bash_tool):
        result = bash_tool.execute(command="echo before && sleep 60", timeout=1)
        assert "before" in result
        assert "[timed out]" in result


class TestKillRegistryIntegration:
    """The Popen handle is registered; force-kill SIGKILLs the group."""

    def test_process_registered_while_running(self, bash_tool):
        result_box: dict = {}

        def run():
            result_box["res"] = bash_tool.validate_and_execute(
                command="sleep 30 && echo done",
                timeout=60,
                _tool_call_id="call-bash-1",
                _agent_id="root",
            )

        th = threading.Thread(target=run, daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = kill_registry.get_record("call-bash-1")
            if record is not None and record._process is not None:
                break
            time.sleep(0.02)
        record = kill_registry.get_record("call-bash-1")
        assert record is not None, "bash call never registered"
        assert record._process is not None, "Popen handle never attached"
        assert is_in_flight("call-bash-1")
        # cleanup via the registry kill (terminate() alone would leave
        # the sleep child holding the stdout pipe open)
        kill_registry.request_kill("call-bash-1")
        th.join(timeout=15)
        assert not th.is_alive()
        assert result_box["res"].startswith("[TOOL_KILLED]")

    def test_kill_mid_run_returns_kill_string_and_kills_group(self, bash_tool):
        result_box: dict = {}

        def run():
            result_box["res"] = bash_tool.validate_and_execute(
                command="sleep 300",
                timeout=600,
                _tool_call_id="call-bash-2",
                _agent_id="root",
            )

        th = threading.Thread(target=run, daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = kill_registry.get_record("call-bash-2")
            if record is not None and record._process is not None:
                break
            time.sleep(0.02)
        record = kill_registry.get_record("call-bash-2")
        assert record is not None and record._process is not None

        pid = record._process.pid
        assert kill_registry.request_kill("call-bash-2", reason="too slow")

        th.join(timeout=15)
        assert not th.is_alive(), "blocked communicate() did not return after killpg"
        assert result_box["res"].startswith(
            "[TOOL_KILLED]"
        ), "kill did not swap in the kill string"
        assert "too slow" in result_box["res"]
        # the process group is really dead
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid)], capture_output=True, text=True, timeout=5
            )
            assert str(pid) not in proc.stdout, "bash process survived the kill"
        except subprocess.TimeoutExpired:
            pass
        assert not is_in_flight("call-bash-2")

    def test_kill_kills_children_of_shell_too(self, bash_tool):
        # The motivating case: a find/glob-like long-running child.
        result_box: dict = {}

        def run():
            result_box["res"] = bash_tool.validate_and_execute(
                command="sleep 500",
                timeout=500,
                _tool_call_id="call-bash-3",
                _agent_id="root",
            )

        th = threading.Thread(target=run, daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = kill_registry.get_record("call-bash-3")
            if record is not None and record._process is not None:
                break
            time.sleep(0.02)

        kill_registry.request_kill("call-bash-3")
        th.join(timeout=15)
        assert not th.is_alive()

        # count any lingering sleep-500 processes (the group kill must
        # have taken the shell AND the sleep). The bracket trick keeps
        # this check's own shell command line from matching.
        check = bash_tool.execute(command="pgrep -f 'sleep 50[0]' | wc -l", timeout=10)
        assert (
            check.strip() == "0"
        ), f"child processes survived the process-group kill: {check}"

    def test_direct_execute_without_record_still_times_out(self, bash_tool):
        # Direct execute (no agent loop): timeout path has no kill
        # record and must still kill the process group.
        result = bash_tool.execute(command="sleep 60", timeout=1)
        assert "[timed out]" in result
        # The bracket trick keeps this check's own shell command line
        # from matching. Retry briefly: the PREVIOUS test also runs a
        # 1s-timeout `sleep 60`, and under load its SIGKILLed child
        # can be reaped a moment after this test starts -- a single
        # instantaneous check would count that corpse as a leak.
        deadline = time.monotonic() + 5
        check = "1"
        while time.monotonic() < deadline:
            check = bash_tool.execute(
                command="pgrep -f 'sleep 6[0]' | wc -l", timeout=10
            )
            if check.strip() == "0":
                break
            time.sleep(0.2)
        assert check.strip() == "0", f"lingering sleeps after timeout: {check}"


class TestProcessHygiene:
    """Failed communicate paths must not leak an unreaped child."""

    def test_commute_failure_kills_group_and_reaps(self, bash_tool):
        class FakeProc:
            def __init__(self):
                self.pid = 4242
                self.returncode = None
                self.killed_group = False
                self.reaped = False

            def communicate(self, timeout=None):
                raise RuntimeError("pipe exploded")

            def wait(self, timeout=None):
                # Simulate SIGKILL taking effect: the reap observes it.
                self.returncode = -9
                self.reaped = True
                return self.returncode

        proc = FakeProc()

        def fake_killpg(pgid, sig):
            proc.killed_group = True

        def fake_popen(*a, **kw):
            return proc

        with patch("wichy.tools.bash.subprocess.Popen", side_effect=fake_popen):
            with patch("wichy.tools.bash.os.killpg", side_effect=fake_killpg):
                with patch("wichy.tools.bash.os.getpgid", return_value=12345):
                    out = bash_tool.execute(command="echo hi", timeout=5)

        assert out.startswith("error:"), out
        assert proc.killed_group, "group not killed on communicate failure"
        assert proc.reaped, "child not reaped"
        assert proc.returncode == -9
