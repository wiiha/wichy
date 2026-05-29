"""Skill discovery and execution tools."""

import json
import subprocess
from typing import List, Literal

from pydantic import Field

from wichy.config.settings import settings
from wichy.console import user_console
from wichy.skills.registry import SkillRegistry
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error
from wichy.tools.human_verification import in_pipeline_mode, prompt_session


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
        """List all skills (excluding inactive)."""
        registry = SkillRegistry()
        skills = registry.list_all()
        result = []
        for skill in skills.values():
            # Skip inactive skills - they're not available to agents
            if skill.inactive:
                continue
            result.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "tags": skill.tags,
                    "script_count": len(skill.scripts),
                    "reference_count": len(skill.references),
                    "asset_count": len(skill.assets),
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
        """Search skills (excluding inactive)."""
        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        results = registry.search(params.keyword)
        result_list = []
        for skill in results.values():
            # Skip inactive skills - they're not available to agents
            if skill.inactive:
                continue
            result_list.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "tags": skill.tags,
                    "script_count": len(skill.scripts),
                    "reference_count": len(skill.references),
                    "asset_count": len(skill.assets),
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

    name = "activate_skill"
    description = "Get detailed information about a specific skill including its markdown content and available scripts"
    description_long = "Returns the full markdown content, metadata, tags, and list of executable scripts for a given skill."
    parameters_model = GetSkillInfoParameters

    def execute(self, **kwargs) -> str:
        """Get skill info (rejects inactive skills)."""
        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        skill = registry.get(params.skill_name)
        if not skill:
            return format_error(f"Skill '{params.skill_name}' not found")
        if skill.inactive:
            return format_error(
                f"Skill '{params.skill_name}' is inactive and cannot be used"
            )
        result = {
            "name": skill.name,
            "description": skill.description,
            # "path": str(skill.path),
            "markdown_content": skill.markdown_content,
            "tags": skill.tags,
            "scripts": skill.list_scripts(),
            "references": [
                str(p.relative_to(skill.path / "references")) for p in skill.references
            ],
            "assets": [str(p.relative_to(skill.path / "assets")) for p in skill.assets],
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
        """Execute a skill script (rejects inactive skills)."""
        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        skill = registry.get(params.skill_name)
        if not skill:
            return format_error(f"Skill '{params.skill_name}' not found")
        if skill.inactive:
            return format_error(
                f"Skill '{params.skill_name}' is inactive and cannot be used"
            )

        script_path = skill.get_script_path(params.script_name)
        if not script_path:
            return format_error(
                f"Script '{params.script_name}' not found in skill '{params.skill_name}'"
            )

        # Check if script is in safe_scripts (no human verification needed)
        is_safe = params.script_name in skill.safe_scripts

        # Human verification for non-safe scripts
        if not is_safe and not settings.skip_human_verification:
            if in_pipeline_mode():
                raise PermissionError(
                    f"This skill script requires human verification and cannot run in pipeline mode. "
                    f"Tool: execute_skill_script, skill={params.skill_name}, script={params.script_name}"
                )
            user_console.print(
                "\n[bold yellow]ACTION:[/bold yellow] Execute skill script"
            )
            user_console.print(
                f"skill='{params.skill_name}' script='{params.script_name}'"
            )
            if params.args:
                user_console.print(f"args: {' '.join(params.args)}")

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
                    return format_error(msg)
                user_console.print("Please enter 'y' or 'n <optional reason>'")

        # Build command
        command = [str(script_path)] + params.args

        try:
            result = subprocess.run(
                command,
                shell=False,  # Don't use shell for security
                text=True,
                capture_output=True,
                timeout=params.timeout,
                # cwd=Path.home(),  # Run from home directory
            )
            output = f"Exit code: {result.returncode}\n"
            if result.stdout:
                output += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                output += f"STDERR:\n{result.stderr}\n"
            return output
        except subprocess.TimeoutExpired:
            return format_error(
                f"Script execution timed out after {params.timeout} seconds"
            )
        except Exception as e:
            return format_error(f"Failed to execute script: {str(e)}")


class ReadSkillFileParameters(ParametersModel):
    """Parameters for reading a file from a skill's references/ or assets/ directory."""

    skill_name: str = Field(..., description="Name of the skill")
    file_path: str = Field(
        ...,
        description="Path to the file relative to the skill's references/ or assets/ directory (e.g., 'api-guide.md' or 'examples/demo.md')",
    )
    file_type: Literal["references", "assets"] = Field(
        default="references",
        description="Type of file to read: 'references' or 'assets'. Defaults to 'references'.",
    )

    def info(self) -> str:
        return (
            f'skill="{self.skill_name}" file="{self.file_path}" type="{self.file_type}"'
        )


class SkillFileTool(BaseTool):
    """Read a file from a skill's references/ or assets/ directory."""

    name = "read_skill_file"
    description = "Read a file from a skill's references/ or assets/ directory. Use this to access detailed documentation, examples, or templates bundled with a skill."
    description_long = "Reads and returns the content of a file from a skill's references/ or assets/ directory. Use this to access reference documentation, examples, templates, or other resources that supplement the skill's main knowledge."
    enable_result_offload = True
    parameters_model = ReadSkillFileParameters

    def execute(self, **kwargs) -> str:
        """Read a skill file (rejects inactive skills)."""
        params = self.parameters_model(**kwargs)
        registry = SkillRegistry()
        skill = registry.get(params.skill_name)
        if not skill:
            return format_error(f"Skill '{params.skill_name}' not found")
        if skill.inactive:
            return format_error(
                f"Skill '{params.skill_name}' is inactive and cannot be used"
            )

        # Determine which directory to search
        if params.file_type == "assets":
            files = skill.assets
        else:  # default to references
            files = skill.references

        # Find the file
        target_path = None
        for file_path in files:
            # Match by relative path from the skill's directory
            relative_path = file_path.relative_to(skill.path / params.file_type)
            if str(relative_path) == params.file_path:
                target_path = file_path
                break
            # Also try matching just the filename
            if file_path.name == params.file_path:
                target_path = file_path
                break

        if not target_path:
            available_files = [
                str(f.relative_to(skill.path / params.file_type)) for f in files
            ]
            return format_error(
                f"File '{params.file_path}' not found in skill '{params.skill_name}' {params.file_type}/ directory. Available files: {available_files}"
            )

        # Read the file content
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
            return content
        except Exception as e:
            return format_error(f"Failed to read file '{params.file_path}': {str(e)}")
