from pydantic import Field

from wichy.helpers.string import truncate_to_len
from wichy.tools.base import BaseTool, ParametersModel


class AnalyzedContentAnswerParameters(ParametersModel):
    context: str = Field(
        ...,
        description=(
            "One sentence summarizing:"
            "- Main topic/domain"
            "- Key arguments/points"
            "- Intended audience/purpose"
        ),
    )
    keywords: list = Field(
        ...,
        description=(
            "Several specific, distinct keywords that capture key concepts and terminology."
            "Order from most to least important."
            "Don't include keywords that are the name of the speaker or time."
            "At least three keywords, but don't be too redundant."
        ),
    )
    tags: list = Field(
        ...,
        description=(
            "Several broad categories/themes for classification."
            "Include domain, format, and type tags."
            "At least three tags, but don't be too redundant."
        ),
    )

    def info(self):

        return f'context="{truncate_to_len(self.context)},  keywords="{self.keywords}", tags="{self.tags}'


class AnalyzedContentAnswerTool(BaseTool):
    name = "analysis_answer"
    description = "Answer the analysis, returning context, keywords and tags"
    description_long = """
You are expected to use this tool in order to present your answer to the analysis.
Your analysis should have identified context, keywords and tags.
"""

    parameters_model = AnalyzedContentAnswerParameters

    def execute(self, context: str, keywords: list, tags: list) -> str:
        return ""
