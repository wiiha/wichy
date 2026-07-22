"""Resolve ``config/<alias-or-path>`` model strings to OpenAI client parameters.

A config backend lets users define arbitrary OpenAI-compatible endpoints in
``settings.yaml`` under a ``backends`` namespace (or as standalone JSON/YAML
files) and reference them via ``wichy -m config/my-alias``.

Resolution order for the part after ``config/``:

1. **Filepath** — if ``Path(part)`` resolves to an existing file, load it
   as a single-backend config (JSON or YAML, by extension).
2. **Alias** — look up the name in the merged ``backends`` namespace from
   ``~/.wichy/settings.yaml`` and ``./.wichy/settings.yaml`` (project
   overrides home).
3. **Error** — neither file nor alias found.

Each config entry supports ``${ENV_VAR}`` interpolation in ``api_key``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from wichy.config import settings
from wichy.console import user_console

# Regex for ${ENV_VAR_NAME} interpolation in api_key values.
# Allows uppercase, lowercase, digits, and underscores (standard env var names).
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Required fields in every config backend entry.
_REQUIRED_FIELDS = ("base_url", "model")

# Optional fields.
_OPTIONAL_FIELDS = ("api_key", "extra_body")

# Valid scope values for saving.
_VALID_SCOPES = ("home", "project")


def _interpolate_env_vars(value: str) -> str:
    """Replace ``${ENV_VAR}`` placeholders with environment variable values.

    If the env var is not set, the literal placeholder is left in place
    (which will likely fail authentication — preferred over a silent empty key).
    """

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        return match.group(0)

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _load_single_config_file(path: Path) -> Dict[str, Any]:
    """Load a standalone config file (JSON or YAML by extension).

    Returns the parsed dict. Raises ``ValueError`` on malformed content or
    missing required fields.
    """
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in config file {path}: {exc.msg} (line {exc.lineno})"
            ) from exc
    elif suffix in (".yaml", ".yml"):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in config file {path}: {exc}") from exc
    else:
        # Try JSON first, then YAML as fallback.
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"Could not parse config file {path} as JSON or YAML: {exc}"
                ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Config file {path} must contain a mapping (dict) at the top level"
        )

    return data


def _validate_entry(data: Dict[str, Any], source_label: str) -> None:
    """Validate that a config entry has all required fields with correct types.

    Raises ``ValueError`` with a clear message if validation fails.
    """
    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise ValueError(
                f"Config backend '{source_label}' is missing required field '{field}'. "
                f"Required fields: {', '.join(_REQUIRED_FIELDS)}."
            )

    base_url = data.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError(
            f"Config backend '{source_label}': 'base_url' must be a non-empty string."
        )

    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(
            f"Config backend '{source_label}': 'model' must be a non-empty string."
        )

    api_key = data.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError(
            f"Config backend '{source_label}': 'api_key' must be a string or null."
        )

    extra_body = data.get("extra_body")
    if extra_body is not None and not isinstance(extra_body, dict):
        raise ValueError(
            f"Config backend '{source_label}': 'extra_body' must be a mapping (dict) or null."
        )


def _resolve_api_key(
    raw_api_key: Optional[str], alias: str, quiet: bool = False
) -> str:
    """Resolve the API key with env-var interpolation and fallback chain.

    1. If ``raw_api_key`` is set, interpolate ``${ENV_VAR}`` and return.
       If interpolation results in an empty string, fall through to fallback.
    2. If absent, warn and fall back to ``settings.openai_api_key``.
    3. If that is also None, warn and fall back to ``"sk-generic"``.

    Args:
        quiet: If True, suppress warning messages (used during validation-only
            checks such as the /model slash command).
    """
    if raw_api_key is not None and raw_api_key.strip():
        interpolated = _interpolate_env_vars(raw_api_key)
        if "${" in interpolated:
            # Env var was not found — leave as-is (will likely fail auth).
            unresolved = _ENV_VAR_PATTERN.findall(interpolated)
            if not quiet:
                user_console.print(
                    f"[yellow]warn:[/yellow] Config backend '{alias}': "
                    f"environment variable '{unresolved[0]}' not set. "
                    f"API key contains unresolved placeholder."
                )
            return interpolated
        if interpolated.strip():
            return interpolated
        # Interpolation produced empty string — fall through to fallback chain.

    # Fallback 1: openai_api_key from settings
    fallback = settings.openai_api_key
    if fallback is not None and fallback.strip():
        if not quiet:
            user_console.print(
                f"[yellow]warn:[/yellow] Config backend '{alias}': "
                f"no 'api_key' configured, falling back to openai_api_key."
            )
        return fallback

    # Fallback 2: sk-generic (same as generic backend)
    if not quiet:
        user_console.print(
            f"[yellow]warn:[/yellow] Config backend '{alias}': "
            f"no 'api_key' configured and openai_api_key is not set, "
            f"falling back to 'sk-generic'."
        )
    return "sk-generic"


def _load_alias_from_settings(alias: str) -> Optional[Dict[str, Any]]:
    """Look up an alias in the merged ``backends`` namespace from settings.yaml.

    Returns the entry dict, or ``None`` if the ``backends`` namespace or the
    alias doesn't exist.
    """
    try:
        namespace = settings.get_namespace("backends")
    except (KeyError, AttributeError):
        return None

    if alias not in namespace:
        return None

    raw = namespace._data[alias]
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config backend alias '{alias}' in settings.yaml is not a mapping (dict)."
        )

    return dict(raw)


def resolve_config_backend(
    model_str: str,
    validate_only: bool = False,
) -> Tuple[str, str, str, Dict[str, Any]]:
    """Resolve a ``config/<alias-or-path>`` model string.

    Args:
        model_str: Full model string starting with ``config/``, e.g.
            ``config/my-llm`` or ``config//path/to/file.json``.
        validate_only: If True, suppress API-key warnings (used by /model
            slash command validation where no API call is being made).

    Returns:
        Tuple of ``(base_url, api_key, model, extra_body)``.

    Raises:
        ValueError: If the reference cannot be resolved or the config is
            missing required fields.
    """
    part = model_str.strip().split("/", 1)
    if len(part) < 2 or not part[1].strip():
        raise ValueError(
            f"Invalid config backend format. Expected 'config/<alias-or-path>', got: {model_str}"
        )

    reference = part[1].strip()

    # Strategy 1: try as a filepath.
    candidate = Path(reference)
    try:
        resolved = candidate.resolve()
        is_file = resolved.is_file()
    except (OSError, RuntimeError):
        is_file = False

    data: Optional[Dict[str, Any]]
    if is_file:
        data = _load_single_config_file(resolved)
        source_label = str(resolved)
    else:
        # Strategy 2: try as an alias in settings.yaml backends namespace.
        data = _load_alias_from_settings(reference)
        if data is None:
            raise ValueError(
                f"Config backend '{reference}' not found. "
                f"No file at path '{candidate}' and no alias '{reference}' "
                f"in backends section of settings.yaml."
            )
        source_label = reference

    _validate_entry(data, source_label)

    base_url = data["base_url"].strip()
    model = data["model"].strip()
    api_key = _resolve_api_key(data.get("api_key"), source_label, quiet=validate_only)
    extra_body = data.get("extra_body") or {}

    return base_url, api_key, model, extra_body


def save_backend_to_yaml(
    alias: str,
    base_url: str,
    model: str,
    api_key: Optional[str],
    extra_body: Optional[Dict[str, Any]],
    scope: str,
) -> Path:
    """Save a new backend entry to the appropriate settings.yaml file.

    Creates the file if it doesn't exist. Merges into an existing ``backends``
    namespace without clobbering other namespaces or entries.

    Args:
        alias: Backend alias name (kebab-case validated by caller).
        base_url: OpenAI-compatible endpoint URL.
        model: Model name for the API.
        api_key: API key string (may contain ``${ENV_VAR}``), or None.
        extra_body: Optional dict forwarded to the API, or None.
        scope: ``"home"`` for ``~/.wichy/settings.yaml`` or ``"project"``
            for ``./.wichy/settings.yaml``.

    Returns:
        The path to the file that was written.

    Raises:
        ValueError: If ``scope`` is invalid or the alias already exists.
    """
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"Invalid scope '{scope}'. Must be one of: {', '.join(_VALID_SCOPES)}."
        )

    if scope == "home":
        target = settings.wichy_home / "settings.yaml"
    else:
        target = Path(".wichy/settings.yaml")

    # Build the entry (ordered: base_url, model, api_key, extra_body).
    entry: Dict[str, Any] = {"base_url": base_url, "model": model}
    if api_key is not None and api_key.strip():
        entry["api_key"] = api_key
    if extra_body is not None and len(extra_body) > 0:
        entry["extra_body"] = extra_body

    # Load existing content or start fresh.
    if target.exists():
        text = target.read_text(encoding="utf-8")
        if text.strip():
            try:
                existing = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ValueError(
                    f"Could not parse existing settings file {target}: {exc}"
                ) from exc
            if existing is None:
                existing = {}
        else:
            existing = {}
    else:
        existing = {}

    if not isinstance(existing, dict):
        raise ValueError(
            f"Existing settings file {target} must contain a YAML mapping at top level."
        )

    # Ensure 'backends' namespace exists.
    backends = existing.get("backends", {})
    if not isinstance(backends, dict):
        raise ValueError(
            f"Existing 'backends' key in {target} is not a mapping (dict)."
        )

    if alias in backends:
        raise ValueError(
            f"Backend alias '{alias}' already exists in {target}. "
            f"Use --force to overwrite."
        )

    backends[alias] = entry
    existing["backends"] = backends

    # Ensure parent directory exists.
    target.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: dump to temp file then os.replace() for all-or-nothing update.
    is_new = not target.exists()
    tmp_file = target.with_suffix(".yaml.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        if is_new:
            f.write("# wichy settings.yaml\n")
            f.write("# See documentation for available namespaces.\n\n")
        yaml.dump(existing, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp_file, target)

    return target
