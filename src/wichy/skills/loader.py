"""Skill loader - discovers and loads skills from disk."""

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Dict, List

from wichy.config import settings
from wichy.skills.registry import SkillRegistry
from wichy.skills.skill import ScriptInfo, Skill, parse_markdown_frontmatter

# Default skills bundled with the package
DEFAULT_SKILLS_DIR = Path(__file__).parent / "default"


class SkillLoader:
    """Discovers and loads skills from the skills directory."""

    def __init__(self, skills_dir: Path | None = None):
        self.skills_dir = Path(skills_dir) if skills_dir else settings.skills_dir
        self.registry = SkillRegistry()

    def discover_skill_dirs(self) -> List[Path]:
        """Find all potential skill directories (subdirectories of skills_dir)."""
        if not self.skills_dir.exists():
            return []
        return [d for d in self.skills_dir.iterdir() if d.is_dir()]

    def install_default_skills(self) -> int:
        """Install default skills bundled with the package. Returns count of installed skills."""
        if not DEFAULT_SKILLS_DIR.exists():
            return 0

        # Ensure skills directory exists
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        installed_count = 0
        for default_skill_dir in DEFAULT_SKILLS_DIR.iterdir():
            if not default_skill_dir.is_dir():
                continue
            target_dir = self.skills_dir / default_skill_dir.name

            # Only install if not already present
            if not target_dir.exists():
                shutil.copytree(default_skill_dir, target_dir)
                installed_count += 1

        return installed_count

    def load_skill_from_dir(self, skill_dir: Path) -> Skill | None:
        """Load a skill from its directory."""
        skill_name = skill_dir.name
        markdown_path = skill_dir / "skill.md"

        if not markdown_path.exists():
            # Skip directories without skill.md
            return None

        # Read markdown content
        with open(markdown_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()

        # Parse metadata from frontmatter
        frontmatter, _body = parse_markdown_frontmatter(markdown_content)

        # Extract top-level fields
        name = frontmatter.pop("name", skill_name)
        description = frontmatter.pop("description", "")
        safe_scripts_raw = frontmatter.pop("safe_scripts", [])

        # Parse safe_scripts (can be string or list)
        if isinstance(safe_scripts_raw, str):
            safe_scripts = [s.strip() for s in safe_scripts_raw.split(",")]
        elif isinstance(safe_scripts_raw, list):
            safe_scripts = safe_scripts_raw
        else:
            safe_scripts = []

        # Extract metadata dict if present, everything else goes into metadata too
        metadata = frontmatter.pop("metadata", {})
        # Merge any remaining top-level fields into metadata
        metadata = {**metadata, **frontmatter}

        # Optional JSON config (merge with frontmatter, frontmatter takes precedence)
        json_config_path = skill_dir / "skill.json"
        if json_config_path.exists():
            with open(json_config_path, "r", encoding="utf-8") as f:
                json_config = json.load(f)
                # Extract known fields from JSON
                json_name = json_config.pop("name", None)
                json_desc = json_config.pop("description", None)
                json_safe = json_config.pop("safe_scripts", [])
                json_meta = json_config.pop("metadata", {})
                # JSON metadata and remaining fields
                json_meta = {**json_meta, **json_config}
                # Merge: JSON is base, frontmatter overwrites
                if not name and json_name:
                    name = json_name
                if not description and json_desc:
                    description = json_desc
                if not safe_scripts and json_safe:
                    safe_scripts = (
                        json_safe
                        if isinstance(json_safe, list)
                        else [s.strip() for s in json_safe.split(",")]
                    )
                metadata = {**json_meta, **metadata}

        # Discover scripts
        scripts = self._discover_scripts(skill_dir, metadata)

        # Discover references and assets
        references = self._discover_files(skill_dir / "references")
        assets = self._discover_files(skill_dir / "assets")

        skill = Skill(
            name=name,
            path=skill_dir,
            markdown_content=markdown_content,
            description=description,
            safe_scripts=safe_scripts,
            metadata=metadata,
            scripts=scripts,
            references=references,
            assets=assets,
        )
        return skill

    def _discover_files(self, directory: Path) -> List[Path]:
        """Recursively discover all files in a directory."""
        files: List[Path] = []
        if not directory.exists() or not directory.is_dir():
            return files
        for item in directory.rglob("*"):
            if item.is_file():
                files.append(item)
        return files

    def _discover_scripts(self, skill_dir: Path, metadata: Dict) -> List["ScriptInfo"]:
        """Find executable scripts in scripts/ or options/scripts/."""
        scripts = []

        # Check if specific script dirs are configured
        script_dirs_str = metadata.get("script_dirs", ["scripts"])
        if isinstance(script_dirs_str, str):
            script_dirs_str = [script_dirs_str]
        script_dirs = [skill_dir / d for d in script_dirs_str]

        # Also check default "scripts" if not already included
        if "scripts" not in [d.name for d in script_dirs]:
            script_dirs.append(skill_dir / "scripts")

        for script_dir in script_dirs:
            if not script_dir.exists() or not script_dir.is_dir():
                continue
            for script_path in script_dir.iterdir():
                if not os.access(script_path, os.X_OK):
                    mode = os.stat(script_path).st_mode | stat.S_IXUSR
                    os.chmod(script_path, mode)
                if script_path.is_file() and os.access(script_path, os.X_OK):
                    script_name = script_path.name
                    # Try to extract description from shebang or first line comment
                    description = self._extract_script_description(script_path)
                    scripts.append(
                        ScriptInfo(
                            name=script_name,
                            path=script_path,
                            executable=True,
                            description=description,
                        )
                    )
        return scripts

    def _extract_script_description(self, script_path: Path) -> str:
        """Extract a short description from script file."""
        try:
            with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
                first_line = f.readline().strip()
                # If it starts with #! it's a shebang, skip to next line
                if first_line.startswith("#!"):
                    first_line = f.readline().strip()
                # If line starts with #, use as description
                if first_line.startswith("#"):
                    return first_line.lstrip("#").strip()
                return ""
        except Exception:
            return ""

    def load_all_skills(self) -> Dict[str, Skill]:
        """Discover and load all skills into the registry."""
        skill_dirs = self.discover_skill_dirs()
        for skill_dir in skill_dirs:
            skill = self.load_skill_from_dir(skill_dir)
            if skill:
                self.registry.register(skill)
        return self.registry.list_all()
