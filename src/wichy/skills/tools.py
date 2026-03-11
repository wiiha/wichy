"""Skill discovery and execution tools."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from wichy.skills.registry import SkillRegistry
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.human_verification import SKIP_HUMAN_VERIFICATION, prompt_session


class ListSkillsParameters(ParametersModel):
    """Parameters for listing skills."""

    pass

    def info(self) -> str:
        return "list all skills"


class SkillDiscoveryTool(BaseTool):
    """List all available skills with brief descriptions."""

    name = "list_skills"
    description = "List all available skills and their descriptions"
    description_long = "Lists all skills available with brief descriptions, tags, and number of scripts."
    parameters_model = ListSkillsParameters

    def execute(self, **kwargs) -> str:
        """List all skills."""
        registry = SkillRegistry()
        skills = registry.list_all()
        result = []
        for skill in skills.values():
            result.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "tags": skill.tags,
                    "script_count": len(skill.scripts),
                }
            )
        return json.dumps({"skills": result}, indent=2)


class SearchSkillsParameters(ParametersModel):
    """Parameters for searching skills."""

    keyword: str = Field(
        ...,
        description="Keyword to search for in skill names, descriptions, tags, or content",
    )

    def info(self) -> str:
        return f'keyword="{self.keyword}"'


class SkillSearchTool(BaseTool):
    """Search skills by keyword."""

    name = "search_skills"
    description = "Search skills by keyword in name, description, tags, or content"
    description_long = "Searches through all skills for a given keyword. Matches in skill names, descriptions, tags, or markdown content."
    parameters_model = SearchSkillsParameters

    def execute(self, **kwargs) -> str:
        """Search skills."""
        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        results = registry.search(params.keyword)
        result_list = []
        for skill in results.values():
            result_list.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "tags": skill.tags,
                    "script_count": len(skill.scripts),
                    "matches_in_content": params.keyword.lower()
                    in skill.markdown_content.lower(),
                }
            )
        return json.dumps({"skills": result_list}, indent=2)


class GetSkillInfoParameters(ParametersModel):
    """Parameters for getting skill details."""

    skill_name: str = Field(..., description="Name of the skill to get info for")

    def info(self) -> str:
        return f'skill_name="{self.skill_name}"'


class SkillInfoTool(BaseTool):
    """Get full details about a skill including its markdown content and scripts."""

    name = "get_skill_info"
    description = "Get detailed information about a specific skill including its markdown content and available scripts"
    description_long = "Returns the full markdown content, metadata, tags, and list of executable scripts for a given skill."
    parameters_model = GetSkillInfoParameters

    def execute(self, **kwargs) -> str:
        """Get skill info."""
        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        skill = registry.get(params.skill_name)
        if not skill:
            return f"error: Skill '{params.skill_name}' not found"
        result = {
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.path),
            "markdown_content": skill.markdown_content,
            "metadata": skill.metadata,
            "tags": skill.tags,
            "scripts": skill.list_scripts(),
        }
        return json.dumps({"skill": result}, indent=2)


class ExecuteSkillScriptParameters(ParametersModel):
    """Parameters for executing a skill script."""

    skill_name: str = Field(..., description="Name of the skill")
    script_name: str = Field(..., description="Name of the script to execute")
    args: List[str] = Field(
        default_factory=list, description="Arguments to pass to the script"
    )
    timeout: int = Field(30, description="Timeout in seconds (default: 30)")

    def info(self) -> str:
        args_str = " ".join(self.args) if self.args else ""
        return f'skill="{self.skill_name}" script="{self.script_name}" args="{args_str}" timeout={self.timeout}'


class SkillScriptTool(BaseTool):
    """Execute a script from a skill with optional arguments."""

    name = "execute_skill_script"
    description = "Execute a script from a skill. Requires human verification unless marked safe in skill.json metadata."
    description_long = "Executes a script from a specified skill. The script must be executable and located in the skill's scripts/ directory (or configured script_dirs). Human verification is required unless the script is listed in the skill's 'safe_scripts' metadata."
    parameters_model = ExecuteSkillScriptParameters

    def execute(self, **kwargs) -> str:
        """Execute a skill script."""
        from rich import print

        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        skill = registry.get(params.skill_name)
        if not skill:
            return f"error: Skill '{params.skill_name}' not found"

        script_path = skill.get_script_path(params.script_name)
        if not script_path:
            return f"error: Script '{params.script_name}' not found in skill '{params.skill_name}'"

        # Check if script is in safe_scripts (no human verification needed)
        is_safe = params.script_name in skill.safe_scripts

        # Human verification for non-safe scripts
        if not is_safe and not SKIP_HUMAN_VERIFICATION:
            print(f"\n[bold yellow]ACTION:[/bold yellow] Execute skill script")
            print(f"skill='{params.skill_name}' script='{params.script_name}'")
            if params.args:
                print(f"args: {' '.join(params.args)}")

            while True:
                line = prompt_session.prompt("Proceed? (y/n): ")
                response = str(line).strip().lower()
                if response.startswith("y"):
                    break
                if response.startswith("n"):
                    reason = (
                        response.removeprefix("no")
                        .removeprefix("n")
                        .removeprefix(",")
                        .strip()
                    )
                    msg = f"User denied execution of skill script: {params.skill_name}/{params.script_name}"
                    if reason:
                        msg += f"\nReason: {reason}"
                    return f"error: {msg}"
                print("Please enter 'y' or 'n <optional reason>'")

        # Build command
        command = [str(script_path)] + params.args

        try:
            result = subprocess.run(
                command,
                shell=False,  # Don't use shell for security
                text=True,
                capture_output=True,
                timeout=params.timeout,
                cwd=Path.home(),  # Run from home directory
                env=os.environ.copy(),  # Inherit current environment
            )
            output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                output += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                output += f"STDERR:\n{result.stderr}\n"
            return output
        except subprocess.TimeoutExpired:
            return f"error: Script execution timed out after {params.timeout} seconds"
        except Exception as e:
            return f"error: Failed to execute script: {str(e)}"
