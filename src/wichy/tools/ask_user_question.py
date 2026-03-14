import json
from typing import Dict, List, Optional, Union

from prompt_toolkit.shortcuts import checkboxlist_dialog, input_dialog, radiolist_dialog
from prompt_toolkit.styles import Style
from pydantic import BaseModel, Field, field_validator

from wichy.tools.base import BaseTool, ParametersModel


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

    def execute(
        self, questions: List[Question], metadata: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Execute the tool by prompting the user with questions.

        Args:
            questions: List of questions to ask (can be dicts or Question objects), DO NOT give an "other option"
            metadata: Optional metadata for tracking

        Returns:
            JSON string containing answers mapping question headers to selected option labels
        """

        try:
            # Parse questions into Question objects if they are dicts
            parsed_questions: List[Question] = []
            for q in questions:
                if isinstance(q, dict):
                    parsed_questions.append(Question(**q))
                else:
                    parsed_questions.append(q)

            answers = {}

            # Style for the dialogs
            style = Style.from_dict(
                {
                    "dialog": "bg:#000000 #ffffff",
                    "dialog.frame": "bg:#000000 #ffffff",
                    "dialog.body": "bg:#000000 #ffffff",
                    "dialog.title": "bg:#000000 #ffffff bold",
                }
            )

            for question in parsed_questions:
                # Prepare options for the dialog
                values = [
                    (opt.label, f"{opt.label}: {opt.description}")
                    for opt in question.options
                ]

                # Add "Other" option
                other_label = "Other (please specify)"
                other_was_passed = False
                for opt in question.options:
                    if opt.label.lower().strip() == "other":
                        other_label = opt.label
                        other_was_passed = True
                        break

                if not other_was_passed:
                    values.append((other_label, "Provide a custom answer"))

                title = "Question"
                if metadata and "source" in metadata:
                    title = f"Question from {metadata['source']}"

                if question.multiSelect:
                    # Use checkbox list for multi-select
                    dialog = checkboxlist_dialog(
                        title=title, text=question.question, values=values, style=style
                    )
                    result = dialog.run()

                    if result:
                        # Remove "Other" if present in multi-select (shouldn't be selected)
                        filtered = [r for r in result if r != other_label]
                        if filtered:
                            answers[question.header] = ", ".join(filtered)
                        else:
                            answers[question.header] = "No selection"
                    else:
                        answers[question.header] = "No selection"
                else:
                    # Use radio dialog for single-select
                    dialog = radiolist_dialog(
                        title=title, text=question.question, values=values, style=style
                    )
                    result = dialog.run()

                    if result and result != other_label:
                        answers[question.header] = result
                    elif result == other_label:
                        # For "Other", prompt for custom text
                        custom_input = input_dialog(
                            title="Custom Answer",
                            text=f"You selected 'Other' for '{question.question}'. Please specify:",
                            style=style,
                        ).run()
                        if custom_input:
                            answers[question.header] = custom_input
                        else:
                            answers[question.header] = "No selection"
                    else:
                        answers[question.header] = "No selection"

            result = {"answers": answers}
            if metadata:
                result["metadata"] = metadata

            return json.dumps(result, indent=2)

        except Exception as e:
            return f"error: {str(e)}"
