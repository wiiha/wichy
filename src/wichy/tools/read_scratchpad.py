"""ReadScratchpadTool - read the agent's pinned scratchpad document.

Reimplemented over block documents. The previous version read ``<slug>.md``
directly and swallowed every failure into "scratchpad is empty", which became
wrong the moment a pin could name a ``.json``: it would report an empty
scratchpad for a document that plainly existed. It now resolves the pin through
the same file-resolution rules as everything else, so a converted document is
read as a block document and a legacy note is still read as markdown.

The tool is deliberately forgiving, because "nothing is pinned" is a normal state
rather than an error: the pin is cleared on every CLI start, and the UI is the
only thing that sets it. A read may also name a note explicitly through the
``slug`` parameter -- the pin gates where the agent writes, not what it reads.
"""

from __future__ import annotations

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.notes.blocks import (
    FORMAT_MARKDOWN,
    MARKDOWN_WRITE_REFUSED,
    DocumentNotFoundError,
    InvalidDocumentError,
    InvalidSlugError,
    load_document,
)
from wichy.tools.notes.agent_tools import (
    _read_slug,
    normalize_style,
    render_document,
    render_markdown_document,
)


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
    slug: str | None = Field(
        default=None,
        description=(
            "Optional: the name of the note to read. Omit to read the pinned "
            "scratchpad."
        ),
    )


class ReadScratchpadTool(BaseTool):
    """Read a note: the pinned scratchpad by default, or the slug it names."""

    name = "read_scratchpad"
    description = (
        "Read the pinned scratchpad. Returns its content as markdown by default, "
        "with every block wrapped in a tag naming its block id so you can target "
        "it with a write tool. Pass style='block' when you need each block's "
        "author, last writer and raw data object instead. Pass slug='<note "
        "name>' to read another note; omit it to read the pinned scratchpad."
    )
    parameters_model = ScratchpadParams
    needs_verification_in_api: bool = False

    def execute(self, **kwargs) -> str:
        """Render the note this call names.

        Returns:
            The document as text, or a message explaining why nothing was read.
            Never raises: a missing pin is a state the agent should be told about,
            not an exception it has to interpret.
        """
        style, style_error = normalize_style(kwargs.get("style"))
        if style_error is not None:
            return style_error

        slug, slug_error = _read_slug(kwargs)
        if slug_error is not None:
            return slug_error

        try:
            document, fmt = load_document(slug, with_format=True)
        except DocumentNotFoundError:
            return (
                f"The note '{slug}' no longer exists. "
                "Pin another note in the notes UI, or call list_notes to see "
                "the notes that exist."
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
            return f"The note '{slug}' could not be read: {e}"

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
