import json
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from wichy.helpers.interaction_provider import get_interaction_provider
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class QuestionOption(BaseModel):
    """Option for a question."""

    label: str = Field(
        ...,
        description="The display text for this option that the user will see and select",
    )
    description: Optional[str] = Field(
        default=None,
        description="Explanation of what this option means or what will happen if chosen. If not provided, label is used as description.",
    )

    def model_post_init(self, __context):
        """Set description to label if not provided."""
        if self.description is None:
            self.description = self.label


class Question(BaseModel):
    """A question to ask the user."""

    question: str = Field(..., description="The complete question to ask the user")
    header: Optional[str] = Field(
        default=None,
        description="Very short label displayed as a chip/tag. If None, auto-generated from question.",
    )
    options: List[Union[QuestionOption, str]] = Field(
        ...,
        description="The available choices for this question (can be labels or QuestionOption objects)",
    )
    multiSelect: bool = Field(
        default=False,
        description="Set to true to allow the user to select multiple options",
    )

    @field_validator("options", mode="before")
    @classmethod
    def convert_strings_to_options(cls, v):
        """Convert string options to QuestionOption objects."""
        if not isinstance(v, list):
            return v
        result = []
        for item in v:
            if isinstance(item, str):
                # Create dict without description key, letting it default to None then replaced by label
                result.append({"label": item})
            else:
                result.append(item)
        return result

    @field_validator("header", mode="after")
    @classmethod
    def auto_generate_header(cls, v, info):
        """Auto-generate header if not provided."""
        if v is None and "question" in info.data:
            question = info.data["question"]
            return question[:30] + ("..." if len(question) > 30 else "")
        return v


class AskUserQuestionParameters(ParametersModel):
    """Parameters for AskUserQuestionTool."""

    questions: List[Question] = Field(
        ...,
        description="Questions to ask the user (1-4 questions)",
        min_length=1,
        max_length=4,
    )
    metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional metadata for tracking and analytics purposes. Not displayed to user.",
    )

    def info(self) -> str:
        num_questions = len(self.questions)
        headers = [q.header for q in self.questions]
        return f"questions={num_questions} headers={headers}"


class AskUserQuestionTool(BaseTool):
    """
    A tool for asking the user questions during execution.
    Allows agents to gather user preferences, clarify ambiguous instructions,
    or get decisions on implementation choices.
    """

    name = "ask_user_question"
    description = "Ask the user questions to gather preferences, clarify requirements, or make decisions"
    parameters_model = AskUserQuestionParameters
    description_long = """
Use this tool when you need to ask the user questions during execution. This allows you to:

1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Important notes:
- Users will always be able to select "Other" to provide custom text input, this option will be added by the tool ans should not be added by the caller.
- Use multiSelect: true to allow multiple answers to be selected for a question
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label
"""

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """
        Execute the tool by prompting the user with questions.

        Args:
            questions: List of questions to ask (can be dicts or Question objects), DO NOT give an "other option"
            metadata: Optional metadata for tracking

        Returns:
            JSON string containing answers mapping question headers to selected option labels
        """
        questions: List[Question] = kwargs["questions"]
        metadata: Optional[Dict[str, str]] = kwargs.get("metadata")
        try:
            # Ensure Question objects
            parsed: List[Question] = []
            for q in questions:
                if isinstance(q, dict):
                    parsed.append(Question(**q))
                else:
                    parsed.append(q)

            provider = get_interaction_provider()
            if provider is None:
                raise RuntimeError("No interaction provider configured")

            result = provider.ask_questions(parsed, metadata)
            return json.dumps(result, indent=2)

        except Exception as e:
            return format_error(str(e))
