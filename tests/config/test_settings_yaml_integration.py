from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from wichy.config._yaml_settings import SettingsNamespace


def test_settings_exposes_yaml_namespace(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    (home / "settings.yaml").write_text("agent:\n  max_turns: 25\n", encoding="utf-8")
    tmp_path.joinpath(".wichy").mkdir()
    tmp_path.joinpath(".wichy/settings.yaml").write_text(
        "agent:\n  max_turns: 50\n", encoding="utf-8"
    )

    # Create a fresh settings instance to pick up monkeypatched paths.
    from wichy.config.settings import Settings

    s = Settings(wichy_home=home)
    assert isinstance(s.agent, SettingsNamespace)
    assert s.agent.max_turns == 50
    assert s.get_namespace("agent").max_turns == 50


def test_settings_unknown_namespace_raises_attribute_error(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    from wichy.config.settings import Settings

    s = Settings(wichy_home=home)
    with pytest.raises(AttributeError):
        _ = s.does_not_exist

    with pytest.raises(AttributeError):
        s.get_namespace("does_not_exist")


def test_settings_pydantic_fields_not_shadowed(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    # Even if YAML defines a namespace named after an existing pydantic field,
    # the pydantic field must win.
    (home / "settings.yaml").write_text("wichy_home:\n  foo: 1\n", encoding="utf-8")

    from wichy.config.settings import Settings

    s = Settings(wichy_home=home)
    assert isinstance(s.wichy_home, Path)


def test_settings_custom_namespace(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    (home / "settings.yaml").write_text(
        "my_custom:\n  value: hello\n", encoding="utf-8"
    )

    from wichy.config.settings import Settings

    s = Settings(wichy_home=home)
    assert s.my_custom.value == "hello"


def test_settings_invalid_yaml_raises(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    (home / "settings.yaml").write_text("agent: [unclosed", encoding="utf-8")

    from wichy.config.settings import Settings

    with pytest.raises(ValueError, match=str(home / "settings.yaml")):
        Settings(wichy_home=home)


def test_settings_root_level_scalar_rejected(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)

    (home / "settings.yaml").write_text("max_turns: 25\n", encoding="utf-8")

    from wichy.config.settings import Settings

    with pytest.raises(
        ValueError, match="top-level key 'max_turns' must be a namespace"
    ):
        Settings(wichy_home=home)
