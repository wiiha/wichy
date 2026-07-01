from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wichy.config._yaml_settings import (
    SettingsNamespace,
    _load_yaml_file,
    load_yaml_settings,
)


@pytest.fixture
def make_settings(tmp_path: Path) -> Any:
    """Create a pair of YAML settings files and return their paths."""

    def _make(home_text: str = "", project_text: str = "") -> tuple[Path, Path]:
        home = tmp_path / "home"
        home.mkdir()
        home_file = home / "settings.yaml"
        project_file = tmp_path / ".wichy" / "settings.yaml"
        project_file.parent.mkdir()
        home_file.write_text(home_text, encoding="utf-8")
        project_file.write_text(project_text, encoding="utf-8")
        return home_file, project_file

    return _make


def test_settings_namespace_attribute_access() -> None:
    ns = SettingsNamespace({"foo": 1, "bar": {"nested": True}})
    assert ns.foo == 1
    assert ns.bar == {"nested": True}


def test_settings_namespace_missing_attribute() -> None:
    ns = SettingsNamespace({"foo": 1})
    with pytest.raises(AttributeError, match="bar"):
        _ = ns.bar


def test_settings_namespace_read_only() -> None:
    ns = SettingsNamespace({"foo": 1})
    with pytest.raises(AttributeError, match="read-only"):
        ns.foo = 2
    with pytest.raises(AttributeError, match="read-only"):
        del ns.foo


def test_settings_namespace_contains() -> None:
    ns = SettingsNamespace({"foo": 1})
    assert "foo" in ns
    assert "bar" not in ns


def test_settings_namespace_truthiness() -> None:
    assert SettingsNamespace({"foo": 1})
    assert not SettingsNamespace({})


def test_load_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.yaml"
    assert _load_yaml_file(path) == {}


def test_load_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert _load_yaml_file(path) == {}


def test_load_whitespace_only_file(tmp_path: Path) -> None:
    path = tmp_path / "whitespace.yaml"
    path.write_text("   \n\n   ", encoding="utf-8")
    assert _load_yaml_file(path) == {}


def test_load_simple_settings(make_settings: Any) -> None:
    home, project = make_settings("agent:\n  max_turns: 25\n")
    namespaces = load_yaml_settings(home, project)
    assert namespaces["agent"].max_turns == 25


def test_project_overrides_home(make_settings: Any) -> None:
    home, project = make_settings(
        "agent:\n  max_turns: 10\n",
        "agent:\n  max_turns: 50\n",
    )
    namespaces = load_yaml_settings(home, project)
    assert namespaces["agent"].max_turns == 50


def test_merge_preserves_unconflicting_keys(make_settings: Any) -> None:
    home, project = make_settings(
        "agent:\n  max_turns: 10\nserver:\n  port: 7891\n",
        "agent:\n  max_turns: 50\n",
    )
    namespaces = load_yaml_settings(home, project)
    assert namespaces["agent"].max_turns == 50
    assert namespaces["server"].port == 7891


def test_project_only_namespace(make_settings: Any) -> None:
    home, project = make_settings(
        "",
        "custom:\n  foo: bar\n",
    )
    namespaces = load_yaml_settings(home, project)
    assert namespaces["custom"].foo == "bar"


def test_invalid_yaml_raises(make_settings: Any) -> None:
    home, project = make_settings("agent: [unclosed")
    with pytest.raises(ValueError, match=str(home)):
        load_yaml_settings(home, project)


def test_root_level_scalar_rejected(make_settings: Any) -> None:
    home, project = make_settings("max_turns: 25\n")
    with pytest.raises(
        ValueError, match="top-level key 'max_turns' must be a namespace"
    ):
        load_yaml_settings(home, project)


def test_root_level_list_rejected(make_settings: Any) -> None:
    home, project = make_settings("items:\n  - one\n  - two\n")
    # A list at the top level is rejected, but a list *under* a namespace is fine.
    # This fixture writes "items:" as a top-level key with a list value, which is invalid.
    with pytest.raises(ValueError, match="top-level key 'items' must be a namespace"):
        load_yaml_settings(home, project)


def test_list_value_under_namespace_allowed(make_settings: Any) -> None:
    home, project = make_settings("", "custom:\n  items:\n    - one\n    - two\n")
    namespaces = load_yaml_settings(home, project)
    assert namespaces["custom"].items == ["one", "two"]


def test_non_dict_top_level_raises(make_settings: Any) -> None:
    home, project = make_settings("[1, 2, 3]\n")
    with pytest.raises(
        ValueError, match="must contain a YAML mapping at the top level"
    ):
        load_yaml_settings(home, project)
