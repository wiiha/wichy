"""Tests for skill directory auto-reload."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wichy.skills.loader import SkillLoader
from wichy.skills.registry import SkillRegistry
from wichy.skills.reloader import SkillReloader


class TestSkillReloader:
    """Watchdog-based skill auto-reload."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Clear the registry before and after each test."""
        SkillRegistry().clear()
        yield
        SkillRegistry().clear()
        SkillReloader.stop()

    def _write_skill(self, directory: Path, name: str, description: str) -> Path:
        skill_dir = directory / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nBody\n"
        )
        return skill_dir

    def test_reloader_loads_skills_on_file_change(self, tmp_path):
        """Editing a skill.md triggers a reload of all skills."""
        local_dir = tmp_path / "skills"
        local_dir.mkdir(parents=True)
        home_dir = tmp_path / "home" / "skills"

        SkillReloader.set_source_dirs(local_dir, home_dir)

        loader = SkillLoader(skills_dir=local_dir)
        loader.load_all_skills()
        assert "reloader-skill" not in SkillRegistry().list_all()

        SkillReloader.start(debounce_seconds=0.2)

        # Create a new skill after the initial load.
        self._write_skill(local_dir, "reloader-skill", "Auto reload skill")

        # Wait for the debounced reload.
        for _ in range(50):
            if "reloader-skill" in SkillRegistry().list_all():
                break
            time.sleep(0.05)

        assert "reloader-skill" in SkillRegistry().list_all()
        skill = SkillRegistry().get("reloader-skill")
        assert skill is not None
        assert skill.description == "Auto reload skill"

    def test_reloader_picks_up_local_and_home_dirs(self, tmp_path):
        """Observer schedules watches for both project-local and user-home dirs."""
        local_dir = tmp_path / "local" / "skills"
        home_dir = tmp_path / "home" / "skills"
        local_dir.mkdir(parents=True)
        home_dir.mkdir(parents=True)

        SkillReloader.set_source_dirs(local_dir, home_dir)
        SkillReloader.start(debounce_seconds=0.2)
        assert SkillReloader.is_running()

    def test_reloader_is_no_op_when_already_running(self, tmp_path):
        """Calling start() twice does not start a second observer."""
        local_dir = tmp_path / "skills"
        local_dir.mkdir(parents=True)
        home_dir = tmp_path / "home" / "skills"

        SkillReloader.set_source_dirs(local_dir, home_dir)

        loader = SkillLoader(skills_dir=local_dir)
        loader.load_all_skills()

        SkillReloader.start(debounce_seconds=0.2)
        first_observer = SkillReloader._observer
        SkillReloader.start(debounce_seconds=0.2)
        second_observer = SkillReloader._observer

        assert first_observer is second_observer
        assert SkillReloader.is_running()

    def test_reloader_ignores_temporary_files(self, tmp_path):
        """Hidden files and directories do not trigger reloads."""
        local_dir = tmp_path / "skills"
        local_dir.mkdir(parents=True)
        home_dir = tmp_path / "home" / "skills"

        SkillReloader.set_source_dirs(local_dir, home_dir)

        # Pre-populate a real skill so we can detect if a reload runs.
        self._write_skill(local_dir, "existing-skill", "Existing skill")
        loader = SkillLoader(skills_dir=local_dir)
        loader.load_all_skills()
        assert "existing-skill" in SkillRegistry().list_all()

        SkillReloader.start(debounce_seconds=0.2)

        # Write a hidden file; it should not trigger a destructive reload.
        (local_dir / ".swp.tmp").write_text("swap")

        time.sleep(0.5)
        assert "existing-skill" in SkillRegistry().list_all()
