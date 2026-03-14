"""Skill representation and data structures."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_markdown_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from markdown, return (metadata, body)."""
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)
    if match:
        frontmatter = match.group(1)
        body = match.group(2)
        try:
            import yaml

            metadata = yaml.safe_load(frontmatter) or {}
        except ImportError:
            metadata = {}
        return metadata, body
    return {}, content


@dataclass
class ScriptInfo:
    """Information about a script within a skill."""

    name: str
    path: Path
    executable: bool
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "executable": self.executable,
            "description": self.description,
        }


@dataclass
class Skill:
    """Represents a skill with knowledge and optionally executable scripts."""

    name: str
    path: Path
    markdown_content: str
    description: str = ""
    safe_scripts: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    scripts: List[ScriptInfo] = field(default_factory=list)
    references: List[Path] = field(default_factory=list)  # Files in references/
    assets: List[Path] = field(default_factory=list)  # Files in assets/

    @property
    def tags(self) -> List[str]:
        """Tags for searching (from metadata.tags)."""
        tags = self.metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        return tags if isinstance(tags, list) else []

    def list_scripts(self) -> List[Dict[str, Any]]:
        """Return list of scripts as dictionaries."""
        return [s.to_dict() for s in self.scripts]

    def get_script_path(self, script_name: str) -> Optional[Path]:
        """Resolve a script name to its full path."""
        for script in self.scripts:
            if script.name == script_name:
                return script.path
        return None

    def is_script_executable(self, script_name: str) -> bool:
        """Check if a script is executable."""
        path = self.get_script_path(script_name)
        return path is not None and os.access(path, os.X_OK) if path else False

    def get_reference_path(self, filename: str) -> Optional[Path]:
        """Resolve a filename to its path in references/ directory."""
        for ref_path in self.references:
            if ref_path.name == filename:
                return ref_path
        return None

    def get_asset_path(self, filename: str) -> Optional[Path]:
        """Resolve a filename to its path in assets/ directory."""
        for asset_path in self.assets:
            if asset_path.name == filename:
                return asset_path
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize skill for tool responses."""
        result = {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "scripts": self.list_scripts(),
            "references": [str(p) for p in self.references],
            "assets": [str(p) for p in self.assets],
        }
        if self.safe_scripts:
            result["safe_scripts"] = self.safe_scripts
        if self.tags:
            result["tags"] = self.tags
        if self.metadata:
            result["metadata"] = self.metadata
        return result
