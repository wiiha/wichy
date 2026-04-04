"""Tests for skill loader - inactive skills are loaded but tools filter them."""

import pytest
import tempfile
from pathlib import Path
from wichy.skills.loader import SkillLoader
from wichy.skills.registry import SkillRegistry


class TestLoaderInactiveHandling:
    """Tests for loading skills - inactive skills are still loaded into registry."""

    @pytest.fixture
    def temp_skills_dir(self, tmp_path):
        """Create a temporary skills directory with active and inactive skills."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # Create an active skill
        active_dir = skills_dir / "active-skill"
        active_dir.mkdir()
        (active_dir / "skill.md").write_text(
            "---\n"
            "name: active-skill\n"
            "description: An active skill\n"
            "metadata:\n"
            "  tags: [workflow]\n"
            "---\n"
            "This skill is active.\n"
        )

        # Create a skill inactive via tag
        inactive_tag_dir = skills_dir / "inactive-tag-skill"
        inactive_tag_dir.mkdir()
        (inactive_tag_dir / "skill.md").write_text(
            "---\n"
            "name: inactive-tag-skill\n"
            "description: A skill inactive via tag\n"
            "metadata:\n"
            "  tags: [inactive, workflow]\n"
            "---\n"
            "This skill is inactive due to tag.\n"
        )

        # Create a skill inactive via metadata property
        inactive_meta_dir = skills_dir / "inactive-meta-skill"
        inactive_meta_dir.mkdir()
        (inactive_meta_dir / "skill.md").write_text(
            "---\n"
            "name: inactive-meta-skill\n"
            "description: A skill inactive via metadata\n"
            "metadata:\n"
            "  tags: [workflow]\n"
            "  inactive: true\n"
            "---\n"
            "This skill is inactive due to metadata.\n"
        )

        # Create another active skill
        active_dir2 = skills_dir / "another-active"
        active_dir2.mkdir()
        (active_dir2 / "skill.md").write_text(
            "---\n"
            "name: another-active\n"
            "description: Another active skill\n"
            "---\n"
            "This skill is also active.\n"
        )

        return skills_dir

    def test_all_skills_loaded_including_inactive(self, temp_skills_dir):
        """All skills including inactive should be loaded into registry."""
        loader = SkillLoader(skills_dir=temp_skills_dir)
        skills = loader.load_all_skills()

        # All 4 skills should be loaded
        assert "active-skill" in skills
        assert "another-active" in skills
        assert "inactive-tag-skill" in skills
        assert "inactive-meta-skill" in skills

    def test_all_skills_count(self, temp_skills_dir):
        """Registry should contain all skills."""
        loader = SkillLoader(skills_dir=temp_skills_dir)
        skills = loader.load_all_skills()

        # Should have all 4 skills loaded
        assert len(skills) == 4

    def test_inactive_flag_on_inactive_skills(self, temp_skills_dir):
        """Inactive skills should have inactive=True property."""
        loader = SkillLoader(skills_dir=temp_skills_dir)
        skills = loader.load_all_skills()

        # Active skills should have inactive=False
        assert skills["active-skill"].inactive is False
        assert skills["another-active"].inactive is False

        # Inactive skills should have inactive=True
        assert skills["inactive-tag-skill"].inactive is True
        assert skills["inactive-meta-skill"].inactive is True

    def test_load_skill_from_dir_sets_inactive_flag(self, temp_skills_dir):
        """load_skill_from_dir should set inactive flag correctly."""
        loader = SkillLoader(skills_dir=temp_skills_dir)

        # Load individual skills
        active_skill = loader.load_skill_from_dir(temp_skills_dir / "active-skill")
        inactive_tag_skill = loader.load_skill_from_dir(temp_skills_dir / "inactive-tag-skill")
        inactive_meta_skill = loader.load_skill_from_dir(temp_skills_dir / "inactive-meta-skill")

        assert active_skill.inactive is False
        assert inactive_tag_skill.inactive is True
        assert inactive_meta_skill.inactive is True