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

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.notes import get_scratchpad_slug
from wichy.tools.notes.blocks import (
    FORMAT_MARKDOWN,
    MARKDOWN_WRITE_REFUSED,
    DocumentNotFoundError,
    InvalidDocumentError,
    InvalidSlugError,
    load_document,
)
from wichy.tools.notes.agent_tools import (
    normalize_style,
    render_document,
    render_markdown_document,
)
from wichy.tools.notes.models import is_valid_slug

#: Returned when nothing is pinned. Every block tool returns this same message,
#: so the agent learns one state from one sentence.
NO_SCRATCHPAD = "No scratchpad is pinned. Pin a note in the notes UI first."


class ScratchpadParams(ParametersModel):
    """Parameters for reading the scratchpad."""

    style: str | None = Field(
        default=None,
        description=(
            "How to render the scratchpad. 'markdown' (default) returns the "
            "content as clean markdown with each block wrapped in a tag naming "
            "its id, e.g. <blk-a12>. 'block' additionally returns each block's "
            "author, last writer and raw data object -- use it when you need the "
            "exact field names. 'md' is accepted as a synonym for 'markdown'."
        ),
    )


class ReadScratchpadTool(BaseTool):
    """Read the content of the pinned scratchpad document."""

    name = "read_scratchpad"
    description = (
        "Read the pinned scratchpad. Returns its content as markdown by default, "
        "with every block wrapped in a tag naming its block id so you can target "
        "it with a write tool. Pass style='block' when you need each block's "
        "author, last writer and raw data object instead."
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
        style, style_error = normalize_style(kwargs.get("style"))
        if style_error is not None:
            return style_error

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
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            # UnicodeDecodeError is a ValueError, not an OSError, so leaving it
            # out made this tool raise on a non-UTF-8 file despite its documented
            # promise never to. The message names the file, so the user can find
            # the one that needs fixing.
            return f"The pinned scratchpad '{slug}' could not be read: {e}"

        if fmt == FORMAT_MARKDOWN:
            # The same sentence every block tool returns, so the agent reads one
            # state from one message. The content still follows, because a note
            # the agent cannot edit is often still a note it should read.
            body = document.blocks[0].data.get("text", "") if document.blocks else ""
            return f"{MARKDOWN_WRITE_REFUSED}\n\n{body}"

        if style == "markdown":
            return render_markdown_document(document)

        # The shared block-style renderer, so this tool and read_blocks/get_block
        # never disagree about how a block is spelled.
        return render_document(document, include_metadata=True, raw_data=True)
