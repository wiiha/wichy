"""ReadScratchpadTool - read the agent's pinned scratchpad document.

Reimplemented over block documents. The previous version read ``<slug>.md``
directly and swallowed every failure into "scratchpad is empty", which became
wrong the moment a pin could name a ``.json``: it would report an empty
scratchpad for a document that plainly existed. It now resolves the pin through
the same file-resolution rules as everything else, so a converted document is
read as a block document and a legacy note is still read as markdown.

The tool is deliberately forgiving, because "nothing is pinned" is a normal state
rather than an error: the pin is cleared on every CLI start, and the UI is the
only thing that sets it.
"""

from __future__ import annotations

import json

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.notes import get_scratchpad_slug
from wichy.tools.notes.blocks import (
    FORMAT_MARKDOWN,
    DocumentNotFoundError,
    InvalidDocumentError,
    InvalidSlugError,
    load_document,
)
from wichy.tools.notes.models import is_valid_slug

#: Returned when nothing is pinned. Every block tool returns this same message,
#: so the agent learns one state from one sentence.
NO_SCRATCHPAD = "No scratchpad is pinned. Pin a note in the notes UI first."


class ScratchpadParams(ParametersModel):
    """No parameters needed for reading the scratchpad."""


class ReadScratchpadTool(BaseTool):
    """Read the content of the pinned scratchpad document."""

    name = "read_scratchpad"
    description = (
        "Read the pinned scratchpad document, including each block's id and "
        "metadata. Use read_blocks for a targeted read of one type or range; "
        "use this to see the whole document before deciding what to change."
    )
    parameters_model = ScratchpadParams
    needs_verification_in_api: bool = False

    def execute(self, **kwargs) -> str:
        """Render the pinned scratchpad.

        Returns:
            The document as text, or a message explaining why nothing was read.
            Never raises: a missing pin is a state the agent should be told about,
            not an exception it has to interpret.
        """
        slug = get_scratchpad_slug()
        if not slug:
            return NO_SCRATCHPAD

        if not is_valid_slug(slug):
            # The marker is a user-editable file, so a pin can name something no
            # document resolver would accept. Say so rather than reporting the
            # scratchpad as empty, which would send the user looking in the wrong
            # place.
            return (
                f"The pinned scratchpad name '{slug}' is not a valid note name. "
                "Pin a note in the notes UI to fix it."
            )

        try:
            document, fmt = load_document(slug, with_format=True)
        except DocumentNotFoundError:
            return (
                f"The pinned scratchpad '{slug}' no longer exists. "
                "Pin another note in the notes UI."
            )
        except (InvalidDocumentError, InvalidSlugError, OSError) as e:
            return f"The pinned scratchpad '{slug}' could not be read: {e}"

        if fmt == FORMAT_MARKDOWN:
            body = document.blocks[0].data.get("text", "") if document.blocks else ""
            return (
                f"# Scratchpad: {document.meta.title}\n\n"
                f"This note is still markdown; convert it to blocks in the notes UI "
                f"before block tools can edit it.\n\n---\n\n{body}"
            )

        lines = [
            f"# Scratchpad: {document.meta.title}",
            "",
            f"slug: {slug}  version: {document.meta.version}",
            "",
        ]
        for block in document.blocks:
            touched = ",".join(block.meta.touched_by) or "nobody"
            lines.append(f"[{block.type}] id: {block.id} (touched by: {touched})")
            lines.append(_render_data(block.data))
            lines.append("")
        return "\n".join(lines).rstrip()


def _render_data(data: dict) -> str:
    """Render one block's data compactly.

    Block data is a small JSON object, so it is shown as JSON rather than
    pretty-printed prose: the agent needs the exact field names to build a
    follow-up call, and a paraphrase would lose them.
    """
    return json.dumps(data, ensure_ascii=False)
