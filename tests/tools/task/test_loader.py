"""Tests for the sub-agent markdown loader."""

import pytest

from wichy.tools.task.base import TaskAgentDefinitionBase
from wichy.tools.task.loader import (
    _load_sub_agents_from_single_dir,
    load_all_sub_agents,
    load_sub_agents_from_dirs,
)


class _FakeSettings:
    """Minimal stand-in for the config settings used by the loader."""

    def __init__(self, home_dir, local_dir):
        self.sub_agent_defs_home_dir = home_dir
        self.sub_agent_defs_local_dir = local_dir


@pytest.fixture
def isolated_dirs(monkeypatch, tmp_path):
    """Point sub-agent discovery to temp directories."""
    home_dir = tmp_path / "home_sub_agents"
    local_dir = tmp_path / "local_sub_agents"
    fake = _FakeSettings(home_dir=home_dir, local_dir=local_dir)
    monkeypatch.setattr("wichy.tools.task.loader.settings", fake)
    return home_dir, local_dir


def test_load_empty_dirs(isolated_dirs):
    """Loading from empty/nonexistent dirs returns empty dict."""
    result = load_sub_agents_from_dirs()
    assert result == {}

    result_with_defaults = load_all_sub_agents()
    assert result_with_defaults == {}


def test_load_single_agent_from_file(isolated_dirs):
    """A well-formed markdown file is parsed into a TaskAgentDefinitionBase."""
    _home_dir, local_dir = isolated_dirs
    local_dir.mkdir(parents=True)
    agent_file = local_dir / "my-agent.md"
    agent_file.write_text(
        "---\n"
        "name: my-agent\n"
        "description: A test agent\n"
        "tools: bash, read_file, glob\n"
        "not_tools: write_file\n"
        "model: ollama/test-model\n"
        "include_env_info: true\n"
        "---\n"
        "You are a test agent.\n"
    )

    result = load_sub_agents_from_dirs()
    assert "my-agent" in result
    agent = result["my-agent"]
    assert agent.description == "A test agent"
    assert agent.tools == ["bash", "read_file", "glob"]
    assert agent.not_tools == ["write_file"]
    assert agent.model == "ollama/test-model"
    assert agent.include_env_info is True
    assert agent.system_prompt == "You are a test agent."


def test_collision_warning(isolated_dirs):
    """Same-name files in a single dir warn and last one wins."""
    _home_dir, local_dir = isolated_dirs
    local_dir.mkdir(parents=True)
    (local_dir / "a.md").write_text("---\nname: dup\ndescription: first\n---\nfirst\n")
    (local_dir / "b.md").write_text(
        "---\nname: dup\ndescription: second\n---\nsecond\n"
    )

    result = _load_sub_agents_from_single_dir(local_dir)
    assert len(result) == 1
    assert result["dup"].description == "second"


def test_defaults_overridden_by_local(isolated_dirs):
    """Local definitions override hardcoded defaults without warnings."""
    _home_dir, local_dir = isolated_dirs
    local_dir.mkdir(parents=True)
    default = TaskAgentDefinitionBase(
        name="default-agent",
        description="default desc",
        system_prompt="default",
    )
    (local_dir / "override.md").write_text(
        "---\nname: default-agent\ndescription: overridden\n---\noverridden\n"
    )

    result = load_all_sub_agents(defaults={"default-agent": default})
    assert result["default-agent"].description == "overridden"
    assert len(result) == 1


def test_skips_invalid_files(isolated_dirs):
    """Files without a name key are skipped silently."""
    _home_dir, local_dir = isolated_dirs
    local_dir.mkdir(parents=True)
    (local_dir / "bad.md").write_text("---\ndescription: no name here\n---\nbody\n")
    (local_dir / "good.md").write_text("---\nname: good\ndescription: yes\n---\nbody\n")

    result = _load_sub_agents_from_single_dir(local_dir)
    assert "good" in result
    assert "bad" not in result


def test_tools_parsing(isolated_dirs):
    """Comma-separated tools string is split and stripped correctly."""
    _home_dir, local_dir = isolated_dirs
    local_dir.mkdir(parents=True)
    (local_dir / "agent.md").write_text(
        "---\nname: agent\ndescription: d\ntools:  bash , read_file , glob \n---\nbody\n"
    )
    result = _load_sub_agents_from_single_dir(local_dir)
    assert result["agent"].tools == ["bash", "read_file", "glob"]


def test_include_env_info_bool_parsing(isolated_dirs):
    """include_env_info is parsed case-insensitively, defaulting to False."""
    _home_dir, local_dir = isolated_dirs
    local_dir.mkdir(parents=True)

    (local_dir / "t1.md").write_text(
        "---\nname: t1\ndescription: d\ninclude_env_info: true\n---\nbody\n"
    )
    (local_dir / "t2.md").write_text(
        "---\nname: t2\ndescription: d\ninclude_env_info: True\n---\nbody\n"
    )
    (local_dir / "t3.md").write_text(
        "---\nname: t3\ndescription: d\ninclude_env_info: false\n---\nbody\n"
    )
    (local_dir / "t4.md").write_text("---\nname: t4\ndescription: d\n---\nbody\n")

    result = _load_sub_agents_from_single_dir(local_dir)
    assert result["t1"].include_env_info is True
    assert result["t2"].include_env_info is True
    assert result["t3"].include_env_info is False
    assert result["t4"].include_env_info is False
