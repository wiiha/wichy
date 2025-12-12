from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Type, Dict, Any
from rich.markdown import Markdown
from rich.console import Console

console_tool_result = Console(quiet=True)


class BaseTool(ABC):
    """Base class for all tools in the agent system."""

    name: str
    description: str
    parameters_model: Type[BaseModel]

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
                if "description" in prop and prop["description"].startswith("HIDE_FROM_LLM"):
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

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    def validate_and_execute(self, **kwargs) -> str:
        """Validate parameters using Pydantic model and execute."""
        # This will raise ValidationError if params are invalid
        # and also catch errors that are not handled by the tool
        # itself.
        res = ""
        try:
            validated_params = self.parameters_model(**kwargs)
            res = self.execute(**validated_params.model_dump())
        except Exception as e:
            res = f"error: {e}"

        console_tool_result.log(
            Markdown(f"\n\n---\n\n### tool {self.name}\n\n{res}\n\n---\n\n")
        )
        return res
