"""Tests for MCP tools filtering of inactive skills."""

import pytest
import json
from pathlib import Path
from wichy.skills.loader import SkillLoader
from wichy.skills.tools import SkillDiscoveryTool, SkillSearchTool, SkillInfoTool, SkillScriptTool, SkillFileTool


class TestToolsFilterInactive:
    """Tests for MCP tools filtering inactive skills."""

    @pytest.fixture
    def skills_dir(self, tmp_path):
        """Create a temporary skills directory with active and inactive skills."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # Create an active skill with a script
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
        scripts_dir = active_dir / "scripts"
        scripts_dir.mkdir()
        script_file = scripts_dir / "test.sh"
        script_file.write_text("#!/bin/bash\necho 'active'\n")
        script_file.chmod(0o755)

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

        # Create a skill inactive via metadata
        inactive_meta_dir = skills_dir / "inactive-meta-skill"
        inactive_meta_dir.mkdir()
        (inactive_meta_dir / "skill.md").write_text(
            "---\n"
            "name: inactive-meta-skill\n"
            "description: A skill inactive via metadata\n"
            "metadata:\n"
            "  inactive: true\n"
            "---\n"
            "This skill is inactive due to metadata.\n"
        )
        refs_dir = inactive_meta_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "guide.md").write_text("# Guide\nSome content\n")

        return skills_dir

    def test_list_skills_excludes_inactive(self, skills_dir):
        """list_skills tool should not include inactive skills."""
        loader = SkillLoader(skills_dir=skills_dir)
        loader.load_all_skills()

        tool = SkillDiscoveryTool()
        result = json.loads(tool.execute())

        skill_names = [s["name"] for s in result["skills"]]
        assert "active-skill" in skill_names
        assert "inactive-tag-skill" not in skill_names
        assert "inactive-meta-skill" not in skill_names

    def test_search_skills_excludes_inactive(self, skills_dir):
        """search_skills tool should not include inactive skills."""
        loader = SkillLoader(skills_dir=skills_dir)
        loader.load_all_skills()

        tool = SkillSearchTool()
        result = json.loads(tool.execute(keyword="skill"))

        skill_names = [s["name"] for s in result["skills"]]
        # Should only include active skill
        assert "active-skill" in skill_names
        assert "inactive-tag-skill" not in skill_names
        assert "inactive-meta-skill" not in skill_names

    def test_activate_skill_rejects_inactive(self, skills_dir):
        """activate_skill tool should reject inactive skills with error."""
        loader = SkillLoader(skills_dir=skills_dir)
        loader.load_all_skills()

        tool = SkillInfoTool()
        result = tool.execute(skill_name="inactive-tag-skill")

        assert "error" in result.lower()
        assert "inactive" in result.lower()

    def test_execute_script_rejects_inactive(self, skills_dir):
        """execute_skill_script tool should reject inactive skills with error."""
        loader = SkillLoader(skills_dir=skills_dir)
        loader.load_all_skills()

        tool = SkillScriptTool()
        result = tool.execute(skill_name="inactive-meta-skill", script_name="test.sh")

        assert "error" in result.lower()
        assert "inactive" in result.lower()

    def test_read_skill_file_rejects_inactive(self, skills_dir):
        """read_skill_file tool should reject inactive skills with error."""
        loader = SkillLoader(skills_dir=skills_dir)
        loader.load_all_skills()

        tool = SkillFileTool()
        result = tool.execute(skill_name="inactive-meta-skill", file_path="guide.md")

        assert "error" in result.lower()
        assert "inactive" in result.lower()

    def test_activate_skill_accepts_active(self, skills_dir):
        """activate_skill tool should accept active skills."""
        loader = SkillLoader(skills_dir=skills_dir)
        loader.load_all_skills()

        tool = SkillInfoTool()
        result = json.loads(tool.execute(skill_name="active-skill"))

        assert result["skill"]["name"] == "active-skill"
        assert "error" not in result