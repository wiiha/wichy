from typing import Any

from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from wichy.skills.skill import parse_markdown_frontmatter
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.notes import get_notes_dir, set_scratchpad_slug


class WriteScratchpadParameters(ParametersModel):
    content: str = Field(
        ..., description="The full markdown content for the scratchpad"
    )

    def info(self) -> str:
        return f"content_length={len(self.content)}"


class WriteScratchpadTool(BaseTool):
    name = "write_scratchpad"
    description = "Write or overwrite the agent scratchpad note"
    description_long = (
        "Saves the provided markdown content to the agent scratchpad note "
        "which is different from the user's scratch pad."
    )
    parameters_model = WriteScratchpadParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Write the scratchpad note and pin it."""
        content: str = kwargs["content"]
        # Ensure notes directory exists
        notes_dir = Path(get_notes_dir())

        slug = "agent-scratchpad"
        file_path = notes_dir / Path(f"{slug}.md")

        # Determine created timestamp: preserve existing or use now
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    raw = f.read()
                metadata, _ = parse_markdown_frontmatter(raw)
                created = metadata.get(
                    "created",
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
                )
            except Exception:
                created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        else:
            created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

        # Write the file with frontmatter
        file_content = f"---\ntitle: Agent Scratchpad\ncreated: {created}\nupdated: {updated}\n---\n{content}"
        with open(file_path, "w") as f:
            f.write(file_content)

        # Pin as the active scratchpad
        set_scratchpad_slug(slug)

        return "Scratchpad saved and pinned."
