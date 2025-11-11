from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Type, Dict, Any


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
        try:
            validated_params = self.parameters_model(**kwargs)
            return self.execute(**validated_params.model_dump())
        except Exception as e:
            return f"error: {e}"
