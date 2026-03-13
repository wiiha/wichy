from pathlib import Path
from typing import Dict, List

from pydantic import BaseModel

from wichy.config import settings
from wichy.helpers.markdown import read_markdown_with_frontmatter


class ParsedRootAgentDesc(BaseModel):
    system_prompt: str
    props: Dict[str, str]


def parse_root_agent_markdown_desc(desc: str) -> ParsedRootAgentDesc:
    root_agent_props, system_prompt = read_markdown_with_frontmatter(desc)
    return ParsedRootAgentDesc(system_prompt=system_prompt, props=root_agent_props)


def _collect_md_files_from_dir(dir_path: Path) -> List[Path]:
    """Return a list of .md files in dir_path (non-recursive)."""
    files: List[Path] = []
    if not dir_path.exists() or not dir_path.is_dir():
        return files
    for p in dir_path.iterdir():
        if p.is_file() and p.suffix.lower() in {".md", ".markdown"}:
            files.append(p)
    return files


def load_user_root_agents() -> List[str]:
    """
    Loads user made root agent descriptions.
    The function will validate that the descriptions has expected
    key-value pairs. If there are two root agents with the same name
    the last one read will take precedence. Local .wichy defs also takes
    precedence over .wichy in home dir.

    :return: List of strings that has been validated as Root Agent Desc
    :rtype: List[str]
    """

    all_files: List[Path] = []

    # check home dir for .wichy
    home_defs_dir = settings.root_agent_defs_home_dir
    if not home_defs_dir.exists():
        # create dir so users can drop files later
        try:
            home_defs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # if creation fails, ignore and continue without home dir
            pass
    # add markdown files from home dir (if any)
    all_files.extend(_collect_md_files_from_dir(home_defs_dir))

    # check closest .wichy (project/local)
    local_defs_dir = settings.root_agent_defs_local_dir
    if not local_defs_dir.exists():
        # create dir locally as well
        try:
            local_defs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    # add markdown files from local dir (local takes precedence so add later)
    all_files.extend(_collect_md_files_from_dir(local_defs_dir))

    # Use an ordered dict-like behavior by iterating in file order:
    # home files first, then local files override when same name appears.
    root_agents: Dict[str, str] = {}

    for fp in all_files:
        try:
            with fp.open("r", encoding="utf-8") as f:
                rad = f.read()
        except Exception:
            # skip files we can't read
            continue

        try:
            ra = parse_root_agent_markdown_desc(rad)
        except Exception:
            # skip files that fail to parse
            continue

        props = ra.props
        if not ("name" in props):
            continue

        # later files override earlier ones (local files were added last)
        root_agents[props["name"]] = rad

    # return the validated root agent descriptions as a list of strings
    return list(root_agents.values())
