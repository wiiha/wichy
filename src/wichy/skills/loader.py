"""Skill loader - discovers and loads skills from disk."""

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List

from wichy.skills.registry import SkillRegistry
from wichy.skills.skill import ScriptInfo, Skill, parse_markdown_frontmatter

# Default skills bundled with the package
DEFAULT_SKILLS_DIR = Path(__file__).parent / "default"


class SkillLoader:
    """Discovers and loads skills from the skills directory."""

    def __init__(self, skills_dir: Path = None):
        self.skills_dir = (
            Path(skills_dir) if skills_dir else Path.home() / ".wichy" / "skills"
        )
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

    def load_skill_from_dir(self, skill_dir: Path) -> Skill:
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

        metadata, _body = parse_markdown_frontmatter(markdown_content)

        # Optional JSON config
        json_config_path = skill_dir / "skill.json"
        if json_config_path.exists():
            with open(json_config_path, "r", encoding="utf-8") as f:
                json_config = json.load(f)
                # Merge with markdown metadata (markdown takes precedence if keys overlap)
                metadata = {**json_config, **metadata}

        # Discover scripts
        scripts = self._discover_scripts(skill_dir, metadata)

        # Discover references and assets
        references = self._discover_files(skill_dir / "references")
        assets = self._discover_files(skill_dir / "assets")

        skill = Skill(
            name=skill_name,
            path=skill_dir,
            markdown_content=markdown_content,
            metadata=metadata,
            scripts=scripts,
            references=references,
            assets=assets,
        )
        return skill

    def _discover_files(self, directory: Path) -> List[Path]:
        """Recursively discover all files in a directory."""
        files = []
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
