"""Tests for skill inactive functionality."""

from pathlib import Path
from wichy.skills.skill import Skill


class TestSkillInactiveProperty:
    """Tests for Skill.inactive property."""

    def test_inactive_false_by_default(self):
        """Skill with no inactive metadata should not be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={},
        )
        assert skill.inactive is False

    def test_inactive_false_with_empty_metadata(self):
        """Skill with empty metadata should not be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
        )
        assert skill.inactive is False

    def test_inactive_true_via_metadata_true(self):
        """Skill with inactive: true in metadata should be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"inactive": True},
        )
        assert skill.inactive is True

    def test_inactive_true_via_metadata_truthy_string(self):
        """Skill with inactive truthy string in metadata should be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"inactive": "yes"},
        )
        assert skill.inactive is True

    def test_inactive_true_via_metadata_int_one(self):
        """Skill with inactive: 1 in metadata should be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"inactive": 1},
        )
        assert skill.inactive is True

    def test_inactive_false_via_metadata_int_zero(self):
        """Skill with inactive: 0 in metadata should not be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"inactive": 0},
        )
        assert skill.inactive is False

    def test_inactive_false_via_metadata_false(self):
        """Skill with inactive: false in metadata should not be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"inactive": False},
        )
        assert skill.inactive is False

    def test_inactive_true_via_tags_list(self):
        """Skill with 'inactive' in tags list should be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"tags": ["workflow", "inactive", "process"]},
        )
        assert skill.inactive is True

    def test_inactive_true_via_tags_string(self):
        """Skill with 'inactive' in tags string should be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"tags": "workflow, inactive, process"},
        )
        assert skill.inactive is True

    def test_inactive_false_via_tags_without_inactive(self):
        """Skill without 'inactive' in tags should not be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={"tags": ["workflow", "process"]},
        )
        assert skill.inactive is False

    def test_inactive_true_both_tag_and_metadata(self):
        """Skill with both tag and metadata inactive should be inactive."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={
                "tags": ["inactive"],
                "inactive": True,
            },
        )
        assert skill.inactive is True

    def test_inactive_other_metadata_preserved(self):
        """Skill inactive check should not affect other metadata."""
        skill = Skill(
            name="test-skill",
            path=Path("/tmp/test-skill"),
            markdown_content="Test content",
            description="A test skill",
            metadata={
                "tags": ["workflow"],
                "inactive": True,
                "other_key": "other_value",
            },
        )
        assert skill.inactive is True
        assert skill.metadata.get("other_key") == "other_value"
        assert skill.tags == ["workflow"]
