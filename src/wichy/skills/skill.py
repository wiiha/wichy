"""Skill representation and data structures."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
import re


def parse_markdown_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from markdown, return (metadata, body)."""
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
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
    metadata: Dict[str, Any] = field(default_factory=dict)
    scripts: List[ScriptInfo] = field(default_factory=list)

    @property
    def description(self) -> str:
        """Get a brief description from metadata or first line of content."""
        if "description" in self.metadata:
            return self.metadata["description"]
        # Fallback: first non-empty line of markdown (strip frontmatter)
        _, body = parse_markdown_frontmatter(self.markdown_content)
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped:
                return stripped[:100]
        return f"Skill: {self.name}"

    @property
    def safe_scripts(self) -> List[str]:
        """List of script names marked as safe (no human verification)."""
        safe = self.metadata.get("safe_scripts", [])
        if isinstance(safe, str):
            safe = [s.strip() for s in safe.split(",")]
        return safe

    @property
    def tags(self) -> List[str]:
        """Tags for searching."""
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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize skill for tool responses."""
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "tags": self.tags,
            "scripts": self.list_scripts(),
            "metadata": self.metadata,
        }
