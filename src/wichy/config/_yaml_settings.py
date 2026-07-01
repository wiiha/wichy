"""YAML-based configuration layer for wichy.

Loads optional user-home and project-local ``settings.yaml`` files and exposes
their contents as read-only namespaced attributes on the ``settings`` singleton.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class SettingsNamespace:
    """Read-only attribute access into a settings namespace."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_data":
            super().__setattr__(name, value)
            return
        raise AttributeError("settings namespaces are read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("settings namespaces are read-only")

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._data!r})"


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a single YAML settings file.

    Returns an empty dict if the file is missing or empty. Raises ``ValueError``
    if the YAML is invalid or if any top-level key is not a namespace mapping.
    """
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in settings file {path}: {exc}") from exc

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise ValueError(
            f"settings file {path} must contain a YAML mapping at the top level"
        )

    for key, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(
                f"settings file {path}: top-level key {key!r} must be a namespace (mapping)"
            )

    return raw


def load_yaml_settings(
    home_path: Path,
    project_path: Path,
) -> dict[str, SettingsNamespace]:
    """Load and merge YAML settings.

    ``project_path`` values override ``home_path`` values at the namespace key
    level.
    """
    home = _load_yaml_file(home_path)
    project = _load_yaml_file(project_path)

    merged: dict[str, Any] = {}
    for namespace in {*home, *project}:
        merged[namespace] = {**home.get(namespace, {}), **project.get(namespace, {})}

    return {name: SettingsNamespace(data) for name, data in merged.items()}
