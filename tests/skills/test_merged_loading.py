"""Tests for merged project-local + user-home skill loading."""

from wichy.skills.loader import SkillLoader


class TestMergedSkillLoading:
    """Skills are loaded from both project-local and user-home directories."""

    def test_local_takes_precedence_over_home(self, tmp_path):
        """Project-local skill shadows a user-home skill with the same name."""
        local_dir = tmp_path / "local" / "skills"
        home_dir = tmp_path / "home" / "skills"
        local_dir.mkdir(parents=True)
        home_dir.mkdir(parents=True)

        # Create a skill in both directories with the same name.
        for directory, desc in ((home_dir, "home skill"), (local_dir, "local skill")):
            skill_dir = directory / "shared-skill"
            skill_dir.mkdir()
            (skill_dir / "skill.md").write_text(
                f"---\nname: shared-skill\ndescription: {desc}\n---\nBody\n"
            )

        loader = SkillLoader(project_skills_dir=local_dir, home_skills_dir=home_dir)
        skills = loader.load_all_skills()

        assert "shared-skill" in skills
        assert skills["shared-skill"].description == "local skill"

    def test_loads_from_home_when_local_missing(self, tmp_path):
        """When project-local skills dir does not exist, home skills are loaded."""
        home_dir = tmp_path / "home" / "skills"
        home_dir.mkdir(parents=True)
        local_dir = tmp_path / "local" / "skills"  # does not exist

        skill_dir = home_dir / "home-only"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text(
            "---\nname: home-only\ndescription: Home only skill\n---\nBody\n"
        )

        loader = SkillLoader(project_skills_dir=local_dir, home_skills_dir=home_dir)
        skills = loader.load_all_skills()

        assert "home-only" in skills

    def test_loads_from_both_directories(self, tmp_path):
        """Skills from both directories appear in the registry."""
        local_dir = tmp_path / "local" / "skills"
        home_dir = tmp_path / "home" / "skills"
        local_dir.mkdir(parents=True)
        home_dir.mkdir(parents=True)

        for directory, name in ((local_dir, "local-only"), (home_dir, "home-only")):
            skill_dir = directory / name
            skill_dir.mkdir()
            (skill_dir / "skill.md").write_text(
                f"---\nname: {name}\ndescription: {name} skill\n---\nBody\n"
            )

        loader = SkillLoader(project_skills_dir=local_dir, home_skills_dir=home_dir)
        skills = loader.load_all_skills()

        assert "local-only" in skills
        assert "home-only" in skills

    def test_empty_when_both_directories_missing(self, tmp_path):
        """When neither skills dir exists, load_all_skills returns an empty registry."""
        from wichy.skills.registry import SkillRegistry

        # Clear any stale state from other tests in this process.
        SkillRegistry().clear()
        local_dir = tmp_path / "local" / "skills"
        home_dir = tmp_path / "home" / "skills"

        loader = SkillLoader(project_skills_dir=local_dir, home_skills_dir=home_dir)
        skills = loader.load_all_skills()

        assert skills == {}

    def test_legacy_single_dir_mode_still_works(self, tmp_path):
        """Passing skills_dir directly disables merging."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        skill_dir = skills_dir / "legacy-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text(
            "---\nname: legacy-skill\ndescription: Legacy skill\n---\nBody\n"
        )

        loader = SkillLoader(skills_dir=skills_dir)
        skills = loader.load_all_skills()

        assert "legacy-skill" in skills
        assert loader.home_skills_dir is None
        assert loader.project_skills_dir == skills_dir

    def test_install_default_skills_dir_prefers_home(self, tmp_path):
        """install_default_skills_dir returns user-home when merging is enabled."""
        local_dir = tmp_path / "local" / "skills"
        home_dir = tmp_path / "home" / "skills"

        loader = SkillLoader(project_skills_dir=local_dir, home_skills_dir=home_dir)
        assert loader.install_default_skills_dir == home_dir

    def test_install_new_skills_dir_prefers_local(self, tmp_path):
        """install_new_skills_dir returns project-local when merging is enabled."""
        local_dir = tmp_path / "local" / "skills"
        home_dir = tmp_path / "home" / "skills"

        loader = SkillLoader(project_skills_dir=local_dir, home_skills_dir=home_dir)
        assert loader.install_new_skills_dir == local_dir

    def test_install_default_skills_dir_uses_legacy_single_dir(self, tmp_path):
        """install_default_skills_dir returns the single directory in legacy mode."""
        skills_dir = tmp_path / "skills"

        loader = SkillLoader(skills_dir=skills_dir)
        assert loader.install_default_skills_dir == skills_dir

    def test_install_new_skills_dir_uses_legacy_single_dir(self, tmp_path):
        """install_new_skills_dir returns the single directory in legacy mode."""
        skills_dir = tmp_path / "skills"

        loader = SkillLoader(skills_dir=skills_dir)
        assert loader.install_new_skills_dir == skills_dir
