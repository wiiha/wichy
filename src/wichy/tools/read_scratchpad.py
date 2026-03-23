"""ReadScratchpadTool - Read the user's pinned scratchpad note."""

from wichy.config import settings
from wichy.skills.skill import parse_markdown_frontmatter
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.notes import get_scratchpad_slug


class ScratchpadParams(ParametersModel):
    """No parameters needed for reading the scratchpad."""


class ReadScratchpadTool(BaseTool):
    """Read the content of the user's pinned scratchpad note."""

    name = "read_scratchpad"
    description = (
        "Read the content of the user's pinned scratchpad note. "
        "Returns the note's markdown content so you can reference it in your response."
    )
    parameters_model = ScratchpadParams

    def execute(self, **kwargs) -> str:
        """Execute the tool by reading the pinned scratchpad note."""
        slug = get_scratchpad_slug()
        if slug is None:
            return "scratchpad is empty"

        try:
            file_path = settings.notes_dir / f"{slug}.md"
            with open(file_path, "r") as f:
                raw = f.read()
            fm, content = parse_markdown_frontmatter(raw)
            title = fm.get("title", slug)
            return f"# Scratchpad: {title}\n\n---\n\n{content}"
        except Exception:
            return "scratchpad is empty"
