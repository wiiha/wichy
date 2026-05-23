"""Generate skills information for system prompt injection."""

from typing import List, Optional

from wichy.skills.registry import SkillRegistry


def skills_information(skill_names: Optional[List[str]] = None) -> str:
    """
    Generate a formatted block of available skills for the system prompt.

    Args:
        skill_names: Optional list of specific skill names to include.
                    If None, includes all available skills.

    Returns:
        A formatted string with skill names and descriptions, wrapped in <skills> tags.
    """
    registry = SkillRegistry()
    all_skills = registry.list_all()

    if not all_skills:
        return ""

    # Filter skills if specific names provided
    if skill_names:
        skills = {name: all_skills[name] for name in skill_names if name in all_skills}
    else:
        skills = all_skills

    # Filter out inactive skills - agents cannot use them
    active_skills = {
        name: skill for name, skill in skills.items() if not skill.inactive
    }

    if not active_skills:
        return ""

    lines = ["<skills>"]
    for skill in active_skills.values():
        lines.append(f"- {skill.name}: {skill.description}")
    lines.append("</skills>")

    return "\n".join(lines)
