"""Skill registry - singleton storage for loaded skills."""

from typing import Dict, Optional

from .skill import Skill


class SkillRegistry:
    """Global registry for skills."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._skills = {}
        return cls._instance

    def register(self, skill: Skill) -> None:
        """Add a skill to the registry."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_all(self) -> Dict[str, Skill]:
        """Get all skills."""
        return dict(self._skills)

    def search(self, keyword: str) -> Dict[str, Skill]:
        """Search skills by keyword in name, description, tags, or content."""
        keyword = keyword.lower()
        results = {}
        for skill in self._skills.values():
            if keyword in skill.name.lower():
                results[skill.name] = skill
                continue
            if keyword in skill.description.lower():
                results[skill.name] = skill
                continue
            if any(keyword in tag.lower() for tag in skill.tags):
                results[skill.name] = skill
                continue
            if keyword in skill.markdown_content.lower():
                results[skill.name] = skill
        return results

    def clear(self) -> None:
        """Clear all skills (mainly for testing)."""
        self._skills.clear()
