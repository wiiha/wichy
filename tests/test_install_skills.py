"""Tests for 'wichy install skills' command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wichy.cli_parser import CliParser


class TestInstallSkillsParser:
    """Tests for CLI parsing of 'install skills' command."""

    def test_parse_install_skills(self):
        """Test parsing 'wichy install skills'."""
        parser = CliParser()
        args = parser.parse(["install", "skills"])

        assert args.command == "install"
        assert args.install_command == "skills"
        assert args.install_force is False

    def test_parse_install_skills_force(self):
        """Test parsing 'wichy install skills --force'."""
        parser = CliParser()
        args = parser.parse(["install", "skills", "--force"])

        assert args.command == "install"
        assert args.install_command == "skills"
        assert args.install_force is True

    def test_parse_install_skills_force_short(self):
        """Test parsing 'wichy install skills -f'."""
        parser = CliParser()
        args = parser.parse(["install", "skills", "-f"])

        assert args.command == "install"
        assert args.install_command == "skills"
        assert args.install_force is True


class TestHandleInstallSkills:
    """Tests for handle_install_skills function."""

    @patch("wichy.skills.loader.SkillLoader")
    def test_install_skills_calls_install_default_skills(self, mock_loader_class):
        """Test that install skills calls SkillLoader.install_default_skills()."""
        from wichy.cli.handlers import handle_install_skills

        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.install_default_skills.return_value = 1

        args = MagicMock()
        args.install_force = False

        with pytest.raises(SystemExit):
            handle_install_skills(args)

        mock_loader.install_default_skills.assert_called_once()

    @patch("wichy.skills.loader.SkillLoader")
    def test_install_skills_zero_prints_already_installed(
        self, mock_loader_class, capsys
    ):
        """Test that when 0 skills installed, prints 'already installed' message."""
        from wichy.cli.handlers import handle_install_skills

        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.install_default_skills.return_value = 0

        args = MagicMock()
        args.install_force = False

        with pytest.raises(SystemExit):
            handle_install_skills(args)

        captured = capsys.readouterr()
        assert (
            "already installed" in captured.out.lower()
            or "already installed" in captured.err.lower()
        )
        assert "--force" in captured.out or "--force" in captured.err

    @patch("wichy.skills.loader.SkillLoader")
    def test_install_skills_nonzero_prints_installed_count(
        self, mock_loader_class, capsys
    ):
        """Test that when >0 skills installed, prints count of installed skills."""
        from wichy.cli.handlers import handle_install_skills

        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.install_default_skills.return_value = 3

        args = MagicMock()
        args.install_force = False

        with pytest.raises(SystemExit):
            handle_install_skills(args)

        captured = capsys.readouterr()
        assert "3" in captured.out or "3" in captured.err

    @patch("wichy.skills.loader.SkillLoader")
    @patch("wichy.skills.loader.DEFAULT_SKILLS_DIR")
    def test_install_skills_force_removes_default_skills(
        self, mock_default_dir, mock_loader_class, tmp_path
    ):
        """Test that --force removes skills matching DEFAULT_SKILLS_DIR names."""
        from wichy.cli.handlers import handle_install_skills

        # Setup mock default skills directory with skill names
        mock_skill1 = MagicMock(spec=Path)
        mock_skill1.name = "default-skill-1"
        mock_skill1.is_dir.return_value = True
        mock_skill2 = MagicMock(spec=Path)
        mock_skill2.name = "default-skill-2"
        mock_skill2.is_dir.return_value = True
        mock_default_dir.iterdir.return_value = [mock_skill1, mock_skill2]

        # Setup mock user skills directory with the default skills installed
        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.skills_dir = tmp_path / "skills"

        # Create the skills directories
        skill_dir_1 = mock_loader.skills_dir / "default-skill-1"
        skill_dir_2 = mock_loader.skills_dir / "default-skill-2"
        skill_dir_1.mkdir(parents=True)
        skill_dir_2.mkdir(parents=True)

        mock_loader.install_default_skills.return_value = 2

        args = MagicMock()
        args.install_force = True

        with pytest.raises(SystemExit):
            handle_install_skills(args)

        # Verify that existing default skills were removed
        assert not skill_dir_1.exists(), "default-skill-1 should have been removed"
        assert not skill_dir_2.exists(), "default-skill-2 should have been removed"

    @patch("wichy.skills.loader.SkillLoader")
    @patch("wichy.skills.loader.DEFAULT_SKILLS_DIR")
    def test_install_skills_force_preserves_user_skills(
        self, mock_default_dir, mock_loader_class, tmp_path
    ):
        """Test that --force does NOT remove user skills (non-default)."""
        from wichy.cli.handlers import handle_install_skills

        # Setup mock default skills directory with limited skills
        mock_default_skill = MagicMock(spec=Path)
        mock_default_skill.name = "default-skill"
        mock_default_skill.is_dir.return_value = True
        mock_default_dir.iterdir.return_value = [mock_default_skill]

        # Setup mock user skills directory
        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.skills_dir = tmp_path / "skills"

        # Create a default skill directory (should be removed)
        default_skill_dir = mock_loader.skills_dir / "default-skill"
        default_skill_dir.mkdir(parents=True)

        # Create a user skill directory (should be preserved)
        user_skill_dir = mock_loader.skills_dir / "my-custom-skill"
        user_skill_dir.mkdir(parents=True)

        mock_loader.install_default_skills.return_value = 1

        args = MagicMock()
        args.install_force = True

        with pytest.raises(SystemExit):
            handle_install_skills(args)

        # Verify user skill was preserved
        assert user_skill_dir.exists(), "User skill should not have been removed"
        # Verify default skill was removed (so reinstall would work)
        assert not default_skill_dir.exists(), "Default skill should have been removed"

    @patch("wichy.skills.loader.SkillLoader")
    def test_install_skills_exits_zero(self, mock_loader_class):
        """Test that install skills exits with code 0."""
        from wichy.cli.handlers import handle_install_skills

        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.install_default_skills.return_value = 1

        args = MagicMock()
        args.install_force = False

        with pytest.raises(SystemExit) as exc_info:
            handle_install_skills(args)

        assert exc_info.value.code == 0

    @patch("wichy.skills.loader.SkillLoader")
    @patch("wichy.skills.loader.DEFAULT_SKILLS_DIR")
    def test_install_skills_force_reinstalls_all(
        self, mock_default_dir, mock_loader_class, tmp_path
    ):
        """Test that --force reinstalls all default skills after removal."""
        from wichy.cli.handlers import handle_install_skills

        # Setup mock default skills
        mock_skill = MagicMock(spec=Path)
        mock_skill.name = "reinstall-skill"
        mock_skill.is_dir.return_value = True
        mock_default_dir.iterdir.return_value = [mock_skill]

        mock_loader = MagicMock()
        mock_loader_class.return_value = mock_loader
        mock_loader.skills_dir = tmp_path / "skills"

        # Return 1 to indicate skill was reinstalled
        mock_loader.install_default_skills.return_value = 1

        args = MagicMock()
        args.install_force = True

        with pytest.raises(SystemExit):
            handle_install_skills(args)

        # Verify install_default_skills was still called after removal
        mock_loader.install_default_skills.assert_called_once()
