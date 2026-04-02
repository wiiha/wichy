from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

from wichy.hooks.executor import HookExecutor
from wichy.tools.registry import ToolMeta

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

from wichy.console import user_console
from wichy.constants import HIDE_FROM_LLM_PREFIX
from wichy.tools.errors import format_error

console_tool_result = Console(quiet=True)


class ParametersModel(BaseModel):

    def info(self) -> str:
        """
        Generates a human readable string of the parameters
        that the model contains. Implementation is up to each
        tools parameters model.

        :return: Printable human readable string of the params.
        :rtype: str
        """
        return ""


class BaseTool(ABC, metaclass=ToolMeta):
    """Base class for all tools in the agent system."""

    name: str
    description: str
    description_long: Optional[str] = None
    """
    If a tool contains both a description and description_long. Then description_long
    will be passed to the LLM and description will be shown in the tool listing for
    the user.
    """
    parameters_model: Type[ParametersModel]

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Execute the tool with given parameters."""
        pass

    def to_function_definition(self) -> Dict[str, Any]:
        """Convert tool to OpenAI/Anthropic function definition format."""
        schema = self.parameters_model.model_json_schema()

        # Clean up the schema (remove title, etc.)
        if "title" in schema:
            del schema["title"]

        if "properties" in schema:
            props_to_del = []
            for prop_name in schema["properties"]:
                # This is to be able to define params on tools
                # and hide these params from being presented to
                # the LLM model. Not pretty but it works.
                prop = schema["properties"][prop_name]
                if "description" in prop and prop["description"].startswith(
                    HIDE_FROM_LLM_PREFIX
                ):
                    props_to_del.append(prop_name)
            for pn in props_to_del:
                del schema["properties"][pn]

            for prop_name in schema["properties"]:
                prop = schema["properties"][prop_name]
                if "anyOf" in prop:
                    x = ""
                    for k in prop["anyOf"]:
                        if k["type"] != "null":
                            x += " or " + k["type"]

                    if x.startswith(" or "):
                        x = x[len(" or ") :]

                    prop["type"] = x
                    del prop["anyOf"]
                    schema["properties"][prop_name] = prop

        # Ensure 'required' field is present
        # Pydantic generates 'required' automatically based on Field(...) vs Field(default)
        if "required" not in schema and "properties" in schema:
            # If Pydantic didn't add it, create empty list (all optional)
            schema["required"] = []

        description = self.description
        if self.description_long:
            description = self.description_long

        description = description.strip()

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": description,
                "parameters": schema,
            },
        }

    def validate_and_execute(self, **kwargs) -> str:
        """Validate parameters using Pydantic model and execute."""
        # This will raise ValidationError if params are invalid
        # and also catch errors that are not handled by the tool
        # itself.
        res = ""
        final_args: Dict[str, Any] = {}
        execution_error: Optional[Exception] = None

        try:
            validated_params = self.parameters_model(**kwargs)
            cmd_info = validated_params.info()
            if cmd_info != "":
                cmd_info = " [pre]" + cmd_info + "[/pre]"
            user_console.print(
                f"[dim][bold]→[/bold] Calling tool:[/dim] [bold]{self.name}[/bold][dim]{cmd_info}[/dim]"
            )

            # Run pre-tool hooks
            pre_result = HookExecutor.run_pre_hooks(
                self, self.name, validated_params.model_dump()
            )

            # If pre-hook denied execution, return error immediately
            if not pre_result.approved:
                res = format_error(
                    pre_result.error_message or f"{self.name}: Hook denied execution"
                )
                user_console.print(
                    f"[red bold]✗[/red bold] tool {self.name} denied by hook"
                )
            else:
                # Use modified input if hooks changed it, otherwise use original
                if pre_result.modified_input:
                    # Re-validate modified input
                    validated_params = self.parameters_model(
                        **pre_result.modified_input
                    )
                    final_args = validated_params.model_dump()
                else:
                    final_args = validated_params.model_dump()

                start_time = time.time()
                try:
                    res = self.execute(**final_args)
                except Exception as e:
                    execution_error = e
                    res = format_error(f"{self.name}: {type(e).__name__}: {e}")
                    user_console.print(
                        f"[red bold]✗[/red bold] tool {self.name} failed"
                    )

                # Calculate execution time
                end_time = time.time()
                execution_time = end_time - start_time

                # Calculate result size metrics
                char_count = len(res)
                token_estimate = char_count // 4  # rough estimate: 1 token ≈ 4 chars

                # Build success message with timing and size info
                if not execution_error:
                    msg = f"[green bold]✓[/green bold] tool {self.name} completed"
                    size_info = (
                        f" [dim]({char_count} chars, ~{token_estimate} tokens)[/dim]"
                    )
                    if execution_time > 3:
                        if execution_time > 60:
                            minutes = int(execution_time // 60)
                            seconds = int(execution_time % 60)
                            time_str = f"{minutes}m {seconds}s"
                        else:
                            time_str = f"{execution_time:.2f}s"
                        msg = f"{msg} in {time_str}"

                    msg = f"{msg}{size_info}"
                    user_console.print(msg)

                # Run post-tool hooks (even on exception for logging/monitoring)
                post_result = HookExecutor.run_post_hooks(
                    self, self.name, final_args, res, error=execution_error
                )

                # Use modified output if hooks changed it
                if post_result.modified_output:
                    res = post_result.modified_output

        except Exception as e:
            res = format_error(f"{self.name}: {type(e).__name__}: {e}")
            user_console.print(f"[red bold]✗[/red bold] tool {self.name} failed")

        # Log detailed error for debugging
        console_tool_result.log(
            Markdown(f"\n\n---\n\n### tool {self.name}\n\n{res}\n\n---\n\n"),
        )

        return res
