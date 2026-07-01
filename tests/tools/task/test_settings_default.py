"""Tests for YAML settings defaulting into the TaskAgentTool."""

from __future__ import annotations

from pathlib import Path

from wichy.config.settings import Settings
from wichy.tools.task_tool import _read_agent_max_turns_setting


def _make_settings(
    tmp_path: Path, monkeypatch, home_text: str = "", project_text: str = ""
) -> Settings:
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.yaml").write_text(home_text, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    tmp_path.joinpath(".wichy").mkdir(exist_ok=True)
    tmp_path.joinpath(".wichy/settings.yaml").write_text(project_text, encoding="utf-8")
    return Settings(wichy_home=home)


def test_read_agent_max_turns_returns_none_without_settings(
    tmp_path: Path, monkeypatch
):
    """When no settings files exist, the default is None."""
    s = _make_settings(tmp_path, monkeypatch)
    assert _read_agent_max_turns_setting(s) is None


def test_read_agent_max_turns_from_project_settings(tmp_path: Path, monkeypatch):
    """A project-local agent.max_turns value is picked up."""
    s = _make_settings(tmp_path, monkeypatch, project_text="agent:\n  max_turns: 17\n")
    assert _read_agent_max_turns_setting(s) == 17


def test_read_agent_max_turns_project_overrides_home(tmp_path: Path, monkeypatch):
    """Project-local agent.max_turns overrides the user-home value."""
    s = _make_settings(
        tmp_path,
        monkeypatch,
        home_text="agent:\n  max_turns: 5\n",
        project_text="agent:\n  max_turns: 42\n",
    )
    assert _read_agent_max_turns_setting(s) == 42


def test_read_agent_max_turns_ignores_zero(tmp_path: Path, monkeypatch):
    """A max_turns of 0 is ignored with a warning instead of failing."""
    s = _make_settings(tmp_path, monkeypatch, home_text="agent:\n  max_turns: 0\n")
    assert _read_agent_max_turns_setting(s) is None


def test_read_agent_max_turns_ignores_negative(tmp_path: Path, monkeypatch):
    """A negative max_turns is ignored with a warning instead of failing."""
    s = _make_settings(tmp_path, monkeypatch, home_text="agent:\n  max_turns: -3\n")
    assert _read_agent_max_turns_setting(s) is None


def test_read_agent_max_turns_ignores_string(tmp_path: Path, monkeypatch):
    """A string max_turns is ignored with a warning instead of failing."""
    s = _make_settings(tmp_path, monkeypatch, home_text="agent:\n  max_turns: ten\n")
    assert _read_agent_max_turns_setting(s) is None


def test_read_agent_max_turns_ignores_bool(tmp_path: Path, monkeypatch):
    """A boolean max_turns is ignored with a warning instead of failing."""
    s = _make_settings(tmp_path, monkeypatch, home_text="agent:\n  max_turns: true\n")
    assert _read_agent_max_turns_setting(s) is None


def test_read_agent_max_turns_prints_warning_for_invalid_value(
    tmp_path: Path, monkeypatch, capsys
):
    """An invalid max_turns prints a user-facing warning."""
    s = _make_settings(tmp_path, monkeypatch, home_text="agent:\n  max_turns: ten\n")

    printed: list[str] = []
    monkeypatch.setattr(
        "wichy.tools.task_tool.user_console.print",
        lambda msg, **_: printed.append(str(msg)),
    )

    assert _read_agent_max_turns_setting(s) is None
    assert len(printed) == 1
    assert "settings.agent.max_turns" in printed[0]
    assert "ten" in printed[0]
    assert "positive integer" in printed[0]


def test_read_agent_max_turns_uses_module_singleton_by_default():
    """When called without arguments, the function reads the global settings singleton."""
    # This just ensures the default argument path doesn't crash. The actual value
    # depends on whether a real settings file happens to exist in the test environment.
    result = _read_agent_max_turns_setting()
    assert result is None or (isinstance(result, int) and result > 0)
