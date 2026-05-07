from pathlib import Path
from typing import Dict, List, Optional

from wichy.config import settings
from wichy.console import user_console
from wichy.helpers.markdown import read_markdown_with_frontmatter
from wichy.tools.task.base import TaskAgentDefinitionBase


def _collect_md_files_from_dir(dir_path: Path) -> List[Path]:
    """Return a list of markdown files in dir_path (non-recursive)."""
    files: List[Path] = []
    if not dir_path.exists() or not dir_path.is_dir():
        return files
    for p in dir_path.iterdir():
        if p.is_file() and p.suffix.lower() in {".md", ".markdown"}:
            files.append(p)
    return files


def _parse_comma_separated(value: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated string into a list of stripped strings."""
    if not value:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return parts if parts else None


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse a string as a boolean, case-insensitive."""
    if not value:
        return default
    return value.strip().lower() == "true"


def _load_sub_agents_from_single_dir(
    dir_path: Path,
) -> Dict[str, TaskAgentDefinitionBase]:
    """Load sub-agent definitions from a single directory.

    Returns a dict keyed by agent name.  If multiple files declare the same
    name, the last one read wins and a warning is printed.
    """
    agents: Dict[str, TaskAgentDefinitionBase] = {}
    files = sorted(_collect_md_files_from_dir(dir_path), key=lambda p: p.name)

    for fp in files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            # skip unreadable files
            continue

        try:
            frontmatter, body = read_markdown_with_frontmatter(raw)
        except Exception:
            # skip unparsable files
            continue

        name = frontmatter.get("name")
        if not name:
            continue

        if name in agents:
            user_console.print(f"[yellow]name collision for sub agent {name}[/yellow]")

        agents[name] = TaskAgentDefinitionBase(
            name=name,
            description=frontmatter.get("description", ""),
            tools=_parse_comma_separated(frontmatter.get("tools")),
            not_tools=_parse_comma_separated(frontmatter.get("not_tools")),
            system_prompt=body,
            model=frontmatter.get("model"),
            include_env_info=_parse_bool(
                frontmatter.get("include_env_info"), default=False
            ),
        )

    return agents


def load_sub_agents_from_dirs() -> Dict[str, TaskAgentDefinitionBase]:
    """Load sub-agent definitions from home and local directories.

    Home directory definitions are loaded first, then local directory
    definitions override them. No warning is emitted when a local
    definition overrides a home definition.

    :return: Dictionary of sub-agent definitions keyed by agent name.
    """
    result: Dict[str, TaskAgentDefinitionBase] = {}

    # Home dir
    home_dir = settings.sub_agent_defs_home_dir
    if not home_dir.exists():
        try:
            home_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    result.update(_load_sub_agents_from_single_dir(home_dir))

    # Local (project) dir
    local_dir = settings.sub_agent_defs_local_dir
    if not local_dir.exists():
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    result.update(_load_sub_agents_from_single_dir(local_dir))

    return result


def load_all_sub_agents(
    defaults: Optional[Dict[str, TaskAgentDefinitionBase]] = None,
) -> Dict[str, TaskAgentDefinitionBase]:
    """Load all sub-agent definitions, starting with hardcoded defaults.

    The merge order is:
      1. ``defaults`` (if provided)
      2. Home directory ``~/.wichy/sub_agents/``
      3. Project-local ``.wichy/sub_agents/``

    Later sources override earlier ones without warnings.

    :param defaults: Optional dict of default agent definitions.
    :return: Dictionary of merged sub-agent definitions keyed by agent name.
    """
    result: Dict[str, TaskAgentDefinitionBase] = {}
    if defaults:
        result.update(defaults)
    result.update(load_sub_agents_from_dirs())
    return result
