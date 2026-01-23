from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

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


class BaseTool(ABC):
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
                    "HIDE_FROM_LLM"
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
        console_cmd_info = Console()

        try:
            validated_params = self.parameters_model(**kwargs)
            cmd_info = validated_params.info()
            if cmd_info != "":
                cmd_info = " [pre]" + cmd_info + "[/pre]"
            console_cmd_info.print(
                f"[dim][bold]→[/bold] Calling tool:[/dim] [bold]{self.name}[/bold][dim]{cmd_info}[/dim]"
            )
            res = self.execute(**validated_params.model_dump())
            console_cmd_info.print(f"[green bold]✓[/green bold] {self.name} completed")

        except Exception as e:
            res = f"error: {e}"
            console_cmd_info.print(f"[red bold]✗[/red bold] {self.name} failed")

        # Log detailed error for debugging
        console_tool_result.log(
            Markdown(f"\n\n---\n\n### tool {self.name}\n\n{res}\n\n---\n\n"),
        )

        return res
