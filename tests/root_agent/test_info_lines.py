"""Root Agent Info box: only skills the agent can actually use are listed."""

import json

from wichy.context.handler import context_from_file
from wichy.root_agent.root_agent import RootAgent
from wichy.skills.skill import Skill


def _fresh_context(tmp_path):
    ctx_path = tmp_path / "ctx.json"
    ctx_path.write_text(json.dumps({"role": "system", "content": "test context"}))
    return context_from_file(str(ctx_path))


def _skill(tmp_path, name, inactive=False):
    metadata = {"inactive": True} if inactive else {}
    return Skill(
        name=name,
        path=tmp_path / name,
        markdown_content="# skill",
        description=f"{name} description",
        metadata=metadata,
    )


def _skills_info_line(agent):
    for entry in agent.context.logs:
        if entry.get("source") == "root_agent":
            for line in entry["data"]["info_lines"]:
                if line.startswith("- **skills:**"):
                    return line
    return None


def _build(tmp_path, skills):
    return RootAgent(
        model_str="test-model",
        tools=[],
        context=_fresh_context(tmp_path),
        skills=skills,
        print_info_lines=False,
    )


def test_inactive_skill_omitted_from_info(tmp_path):
    agent = _build(
        tmp_path,
        {
            "alpha": _skill(tmp_path, "alpha"),
            "beta": _skill(tmp_path, "beta", inactive=True),
            "gamma": _skill(tmp_path, "gamma"),
        },
    )
    assert _skills_info_line(agent) == "- **skills:** alpha, gamma"


def test_inactive_via_tag_omitted(tmp_path):
    tagged = Skill(
        name="tagged",
        path=tmp_path / "tagged",
        markdown_content="# skill",
        description="tagged",
        metadata={"tags": ["scratchpad", "inactive"]},
    )
    agent = _build(tmp_path, {"alpha": _skill(tmp_path, "alpha"), "tagged": tagged})
    assert _skills_info_line(agent) == "- **skills:** alpha"


def test_skill_inactive_flag_governs_the_line(tmp_path):
    """The display must follow ``Skill.inactive``, whichever way it became true."""
    for metadata in ({"inactive": True}, {"tags": ["inactive"]}, {"tags": "inactive"}):
        skill = Skill(
            name="hidden",
            path=tmp_path / "hidden",
            markdown_content="# skill",
            description="hidden",
            metadata=metadata,
        )
        assert skill.inactive, metadata
        agent = _build(tmp_path, {"hidden": skill})
        assert _skills_info_line(agent) is None, metadata


def test_line_absent_when_all_inactive(tmp_path):
    agent = _build(tmp_path, {"beta": _skill(tmp_path, "beta", inactive=True)})
    assert _skills_info_line(agent) is None


def test_registry_keeps_inactive_skill(tmp_path):
    skills = {
        "alpha": _skill(tmp_path, "alpha"),
        "beta": _skill(tmp_path, "beta", inactive=True),
    }
    agent = _build(tmp_path, skills)
    assert set(agent.skills) == {"alpha", "beta"}
