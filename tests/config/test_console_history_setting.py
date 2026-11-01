from __future__ import annotations

from pytest import MonkeyPatch

from wichy.config.settings import Settings


def test_console_history_max_default(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("WICHY_CONSOLE_HISTORY_MAX", raising=False)
    assert Settings().console_history_max == 2000


def test_console_history_max_direct_override() -> None:
    assert Settings(console_history_max=5).console_history_max == 5


def test_console_history_max_env_override(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("WICHY_CONSOLE_HISTORY_MAX", "7")
    assert Settings().console_history_max == 7


def test_console_history_max_singleton(monkeypatch: MonkeyPatch) -> None:
    from wichy.config.settings import settings

    monkeypatch.delenv("WICHY_CONSOLE_HISTORY_MAX", raising=False)
    assert settings.console_history_max == 2000
