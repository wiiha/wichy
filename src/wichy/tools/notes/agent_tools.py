"""The agent's block tools.

Seven tools that read and edit block documents. `read_scratchpad` is a separate
module (`wichy.tools.read_scratchpad`) with its own rendering of the same
document.

**Every tool operates on the pinned scratchpad and takes no slug.** That is not
a limitation to work around: there is one shared document, and the agent and the
user edit the same one. A slug parameter would let the agent wander into notes
the user has not opened, and the pin is the single place that decision is made.

Two refusals are normal states rather than errors, and every tool returns the
same sentence for each:

- **Nothing pinned.** The pin is cleared on every CLI start and set only from the
  UI, so this is the ordinary startup state.
- **A markdown-format scratchpad.** A block write against a legacy ``.md`` would
  materialise a ``.json`` beside it, giving one slug two live documents. The
  tools report the same message the HTTP API uses, so the user sees one
  explanation rather than two.

Block ``data`` is validated here, inside ``execute()``, against the strict
per-type models. The tool framework validates only ``parameters_model``, so
``data`` has to be declared ``dict[str, Any]`` to produce a usable function
schema, and its contents are checked by the tool body.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.notes import get_scratchpad_slug
from wichy.tools.notes.blocks import (
    FORMAT_MARKDOWN,
    MARKDOWN_WRITE_REFUSED,
    BlockNotFoundError,
    DocumentNotFoundError,
    InvalidDocumentError,
    InvalidSlugError,
    MarkdownDocumentError,
    StaleVersionError,
    block_snapshot_of,
    delete_block,
    load_document,
    locked_document,
    insert_block,
    move_block,
    read_blocks,
    replace_block,
)
from wichy.tools.notes.models import (
    BLOCK_DATA_MODELS,
    BlockDataError,
    is_valid_slug,
    schema_summary,
)
from wichy.tools.notes.revisions import read_revisions

#: Returned by every tool when no document is pinned.
NO_SCRATCHPAD = "No scratchpad is pinned. Pin a note in the notes UI first."

#: The valid block types, for use in tool descriptions and error messages.
VALID_TYPES = ", ".join(sorted(BLOCK_DATA_MODELS))


class ScratchpadUnavailable(Exception):
    """No usable scratchpad is pinned.

    Carries the sentence the tool should return. Raised rather than returned as a
    sentinel so that a tool body which does not handle it cannot accidentally
    proceed with no document: the exception stops the call.
    """


class _NoChange(Exception):
    """A write tool decided the document already holds what it was asked for.

    Raised from inside a ``locked_document`` body, where returning early would
    NOT be enough: the context manager resumes its generator after the yield, so a
    plain ``return`` from the body still bumps the version and appends a revision
    entry describing a change that did not happen. The agent would then hold a
    version the browser never learns about, and its next save would be rejected
    as stale. Raising at the yield is what skips the bump.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _scratchpad_slug() -> str:
    """Resolve the pinned scratchpad, or explain why there is none.

    Returns:
        The slug to work on.

    Raises:
        ScratchpadUnavailable: Nothing is pinned, or the pin does not name a
            valid document. Its message is the tool's return value.
    """
    slug = get_scratchpad_slug()
    if not slug:
        raise ScratchpadUnavailable(NO_SCRATCHPAD)
    if not is_valid_slug(slug):
        raise ScratchpadUnavailable(
            f"The pinned scratchpad name '{slug}' is not a valid note name. "
            "Pin a note in the notes UI to fix it."
        )
    return slug


def _open(slug: str, *, for_write: bool = True) -> tuple[Any, str | None]:
    """Load the scratchpad.

    Args:
        slug: The scratchpad slug.
        for_write: When true, a markdown-format scratchpad is refused, because a
            block write against a legacy ``.md`` would materialise a ``.json``
            beside it and give one slug two live documents. When false, the
            markdown note is returned as its synthetic single-block document: a
            note the agent cannot edit is still a note it should be able to read,
            and ``read_scratchpad`` has always shown its content.

    Returns:
        ``(document, error)``.
    """
    try:
        document, fmt = load_document(slug, with_format=True)
    except DocumentNotFoundError:
        return None, (
            f"The pinned scratchpad '{slug}' no longer exists. "
            "Pin another note in the notes UI."
        )
    except (InvalidDocumentError, InvalidSlugError, UnicodeDecodeError, OSError) as e:
        return None, f"The pinned scratchpad '{slug}' could not be read: {e}"

    if fmt == FORMAT_MARKDOWN and for_write:
        return None, MARKDOWN_WRITE_REFUSED
    return document, None


def render_block(
    block: Any, *, include_metadata: bool = True, raw_data: bool = False
) -> str:
    """Render one block in the form the agent reads.

    Args:
        block: The block to render.
        include_metadata: Whether to include the metadata line. Without it the
            agent cannot name the block in a follow-up call, so this is only
            false for a deliberately content-only read.
        raw_data: Show the block's data as the stored JSON object rather than as
            rendered content. The JSON is what a caller needs to build a write --
            it carries the exact field names -- while rendered content is what a
            reader wants. The metadata line is identical either way, so the two
            forms differ only in how much detail the body carries.

    Returns:
        The rendered block.
    """
    # The metadata line has ONE spelling, shared by read_blocks, get_block,
    # find_block_id_for_string and the block style of read_scratchpad. Two
    # spellings of the same facts would make the agent translate between them.
    # The body differs by audience: rendered content to read, JSON to write.
    # Both authorship fields, consistently labeled. `author` alone was wrong twice
    # over: it is the CREATOR and `touch_block` never updates it, so an agent
    # re-reading a block it had just edited was told the user wrote it.
    # `touched_by[-1]` is the last writer, which is the one the agent wants.
    touched = ",".join(block.meta.touched_by) or "nobody"
    last_touched = block.meta.touched_by[-1] if block.meta.touched_by else "nobody"
    header = (
        f"[{block.type}] id: {block.id} author={block.meta.author} "
        f"last-touched-by={last_touched} (touched by: {touched})"
        if include_metadata
        else ""
    )
    body = _render_body(block, raw_data=raw_data)
    return f"{header}\n{body}".strip() if header else body


def _render_body(block: Any, *, raw_data: bool) -> str:
    """One block's body: rendered content, or the stored JSON object.

    Args:
        block: The block.
        raw_data: True for the JSON object, False for rendered content.

    Returns:
        The body text.
    """
    if raw_data:
        return json.dumps(dict(block.data), ensure_ascii=False)
    return render_data(block.type, block.data)


def render_data(block_type: str, data: dict[str, Any]) -> str:
    """Render one block's content as the text the agent reads.

    Deliberately close to the export form: a block the agent reads should look
    like what it would write, so a read-then-edit cycle does not have to
    translate between two notations.

    Stored data is NOT re-validated on read -- the document's own model treats a
    block's ``data`` as free-form -- so a hand-edited file can hold a block whose
    content does not fit its type. That must degrade, not raise: one malformed
    block would otherwise make the whole document unreadable through the agent's
    primary read tool. Any block that cannot be rendered as its type falls back
    to a JSON dump, which still shows the content.
    """
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False) if data is not None else ""

    try:
        return _render_typed(block_type, data)
    except (TypeError, ValueError, AttributeError, KeyError):
        # The shape does not match the declared type. Showing the raw data is
        # more useful than an error, because the content is what the agent needs
        # and the mismatch is what the user needs to see.
        return json.dumps(data, ensure_ascii=False)


def _render_typed(block_type: str, data: dict[str, Any]) -> str:
    """Render data that is expected to fit its type."""
    if block_type == "header":
        return f"{'#' * int(data.get('level', 1))} {data.get('text', '')}"
    if block_type == "paragraph":
        return str(data.get("text", ""))
    if block_type == "list":
        items = list(data.get("items") or [])
        if data.get("style") == "ordered":
            return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))
        return "\n".join(f"- {item}" for item in items)
    if block_type == "checklist":
        return "\n".join(
            f"- [{'x' if item.get('checked') else ' '}] {item.get('text', '')}"
            for item in data.get("items") or []
        )
    if block_type == "code":
        language = str(data.get("language") or "")
        return f"```{language}\n{data.get('code', '')}\n```"
    if block_type == "quote":
        lines = [f"> {line}" for line in str(data.get("text", "")).split("\n")]
        caption = str(data.get("caption") or "")
        if caption:
            lines.append(f"> -- {caption}")
        return "\n".join(lines)
    if block_type == "delimiter":
        return "---"
    if block_type == "question":
        answered = " answered" if data.get("answered") else ""
        return f"[QUESTION{answered}] {data.get('text', '')}"
    if block_type == "decision":
        return f"[DECISION] {data.get('text', '')}"
    if block_type == "todo":
        state = "checked" if data.get("checked") else "unchecked"
        return f"[TODO {state}] {data.get('text', '')}"
    return str(data)


def scratchpad_header(document: Any) -> str:
    """The one-line header every read of the scratchpad starts with.

    Args:
        document: The document being read.

    Returns:
        The header line, naming no slug: the agent has no word for a note's name.
    """
    return (
        f"[Scratchpad | version {document.meta.version} | "
        f"{len(document.blocks)} blocks]"
    )


def render_document(
    document: Any,
    blocks: list[Any] | None = None,
    *,
    include_metadata: bool = True,
    raw_data: bool = False,
) -> str:
    """Render a document header plus its blocks.

    Args:
        document: The document being read.
        blocks: Which of its blocks to render, or None for all.
        include_metadata: Whether each block gets its metadata line. The
            total-block count is still reported either way, so the agent can tell
            a filtered read from an empty document.
        raw_data: Show each block's stored data object instead of its rendered
            content; see :func:`render_block`.
    """
    chosen = document.blocks if blocks is None else blocks
    lines: list[str] = []
    if include_metadata:
        # No slug: the agent knows this document only as the scratchpad, and the
        # internal name is not something it can use. The version IS useful, for the
        # expected_version argument of a write.
        lines = [scratchpad_header(document), ""]
    for block in chosen:
        lines.append(
            render_block(block, include_metadata=include_metadata, raw_data=raw_data)
        )
        lines.append("")
    return "\n".join(lines).rstrip()


#: The styles ``read_scratchpad`` can render in.
#:
#: ``markdown`` is the default because it is what a read is usually FOR: seeing
#: what the scratchpad says. ``block`` is the verbose form -- a metadata line and
#: the raw data object -- which is needed when the agent must construct a write.
#: Both accept the alias ``md``, because an agent that has seen the word
#: "markdown" in many other contexts will reach for either spelling.
READ_STYLES = ("markdown", "md", "block")

#: Render markdown-style: the block's text wrapped in a tag naming its id.
#:
#: The id has to be present or the agent cannot target the block afterwards, and
#: XML-ish tags are how it is attached without a metadata line per block. The tag
#: is a real block id, not a placeholder, so it can be fed straight to a write
#: tool.
_MARKDOWN_BLOCK_OPEN = "<{block_id}>"
_MARKDOWN_BLOCK_CLOSE = "</{block_id}>"


def render_markdown_document(document: Any, blocks: list[Any] | None = None) -> str:
    """Render a document as clean markdown, each block tagged with its id.

    Args:
        document: The document being read.
        blocks: Which of its blocks to render, or None for all.

    Returns:
        The document's content, blocks separated by a blank line, each wrapped in
        ``<id>`` ... ``</id>``. A block with no text of its own (a delimiter) still
        gets its tags, so its id remains addressable.
    """
    chosen = document.blocks if blocks is None else blocks
    pieces = [
        f"{_MARKDOWN_BLOCK_OPEN.format(block_id=block.id)}\n"
        f"{render_data(block.type, block.data)}\n"
        f"{_MARKDOWN_BLOCK_CLOSE.format(block_id=block.id)}"
        for block in chosen
    ]
    body = "\n\n".join(pieces)
    return f"{scratchpad_header(document)}\n\n{body}".rstrip()


def normalize_style(raw: Any) -> tuple[str | None, str | None]:
    """Validate a requested read style.

    Returns:
        ``(style, error)``. ``markdown`` and its alias ``md`` both come back as
        ``"markdown"``, so callers branch on one spelling.
    """
    if raw is None or raw == "":
        return "markdown", None
    style = str(raw).strip().lower()
    if style == "md":
        style = "markdown"
    if style not in ("markdown", "block"):
        return None, (
            f"Unknown style '{raw}'. Use 'markdown' (default) for the content, "
            "or 'block' for the metadata and raw data."
        )
    return style, None


def render_style(style: str, document: Any, blocks: list[Any] | None = None) -> str:
    """Render a document in the requested style.

    Args:
        style: ``"markdown"`` or ``"block"``, already normalised.
        document: The document being read.
        blocks: Which of its blocks to render, or None for all.

    Returns:
        The rendered document.
    """
    if style == "markdown":
        return render_markdown_document(document, blocks)
    return render_document(document, blocks, include_metadata=True, raw_data=True)


def _parse_int(raw: Any, field: str) -> tuple[int | None, str | None]:
    """Validate an optional non-negative integer argument.

    Returns:
        ``(value, error)``.
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, f"{field} must be an integer."
    if raw < 0:
        return None, f"{field} must not be negative."
    return raw, None


def _parse_expected_version(raw: Any) -> tuple[int | None, str | None]:
    """Validate the optional ``expected_version`` argument.

    Booleans are rejected explicitly, for the same reason the HTTP routes do:
    ``bool`` is a subclass of ``int``, so ``True`` would compare equal to version
    1 and silently satisfy the concurrency check.

    Returns:
        ``(version, error)``. Both None when the argument was omitted, which
        means "write over whatever is current".
    """
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, "expected_version must be an integer."
    return raw, None


#: The tail shared by every write tool's except chain. Split so the read phase and
#: the write phase report differently: an OSError arriving AFTER the document was
#: read is a WRITE failure, and saying "could not read" hid a half-applied write
#: behind a message that invites a blind retry.
def _write_error(slug: str, error: Exception) -> str:
    """The message for a failure that happened during the write phase."""
    return (
        f"The write to the scratchpad '{slug}' failed mid-way ({error}). Its "
        "result is uncertain: read the scratchpad to see the current state "
        "before retrying."
    )


def _read_error(slug: str, error: Exception) -> str:
    """The message for a failure that happened while reading the document."""
    return f"Could not read the scratchpad '{slug}': {error}"


def _stale_message(error: StaleVersionError) -> str:
    """The message for a write refused because the document moved on.

    Actionable rather than terse: the agent cannot fix a stale read without
    knowing both numbers, and it can always choose to write over the current
    state by omitting ``expected_version``.
    """
    return (
        f"The note changed since you read it (you expected v{error.expected}, it "
        f"is now v{error.actual}). Re-read the scratchpad, then retry -- or omit "
        "expected_version to write over the current state."
    )


class ReadBlocksParams(ParametersModel):
    """Parameters for read_blocks."""

    filter_type: str | None = None
    block_id: str | None = None
    start_index: int | None = Field(
        default=None, description="First block to return, inclusive, 0-based."
    )
    end_index: int | None = Field(
        default=None, description="Last block to return, inclusive."
    )
    include_metadata: bool = True


class ReadBlocksTool(BaseTool):
    """Read the scratchpad's blocks, optionally filtered."""

    name = "read_blocks"
    description = (
        "Read the pinned scratchpad's blocks. Filter by type, by a single block "
        "id, or by an index range. Returns each block's id so you can target it "
        "with replace_block, delete_block or move_block."
    )
    parameters_model = ReadBlocksParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Read blocks."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = kwargs.get("block_id")
        filter_type = kwargs.get("filter_type")
        start_raw = kwargs.get("start_index")
        end_raw = kwargs.get("end_index")
        include_metadata = kwargs.get("include_metadata", True)

        # A single-block read is a different question from a filtered range, so
        # combining them would leave which one wins undefined.
        if block_id and (filter_type or start_raw is not None or end_raw is not None):
            return (
                "block_id cannot be combined with filter_type or an index range. "
                "Use one or the other."
            )

        start, error = _parse_int(start_raw, "start_index")
        if error is not None:
            return error
        end, error = _parse_int(end_raw, "end_index")
        if error is not None:
            return error

        if filter_type and filter_type not in BLOCK_DATA_MODELS:
            return f"Unknown block type '{filter_type}'. Valid types: {VALID_TYPES}."

        # A read of a markdown scratchpad yields its content rather than the
        # write-refusal sentence: the synthetic block view is what
        # read_scratchpad shows, and two read tools disagreeing about whether the
        # document is readable is worse than either answer alone.
        document, error = _open(slug, for_write=False)
        if error is not None:
            return error

        if block_id:
            block = document.get_block(block_id)
            if block is None:
                return f"No block '{block_id}' in this document."
            return render_document(document, [block], include_metadata=include_metadata)

        try:
            selected = read_blocks(
                document, block_type=filter_type, start=start, end=end
            )
        except ValueError as e:
            return str(e)

        if not selected:
            return f"No blocks matched in '{slug}' ({len(document.blocks)} total)."
        return render_document(document, selected, include_metadata=include_metadata)


class ReplaceBlockParams(ParametersModel):
    """Parameters for replace_block."""

    block_id: str
    block_type: str
    data: dict[str, Any]
    expected_version: int | None = None


class ReplaceBlockTool(BaseTool):
    """Replace one block's content, keeping its id."""

    name = "replace_block"
    description = (
        "Replace the content of one existing block, keeping its id. Use this to "
        "edit a block you have already read."
    )
    parameters_model = ReplaceBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Replace a block."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        block_type = str(kwargs.get("block_type") or "")
        data = kwargs.get("data")

        if not block_id:
            return "block_id is required."
        if not block_type:
            return f"block_type is required. Valid types: {VALID_TYPES}."
        if not isinstance(data, dict):
            return f"data must be an object. Expected {schema_summary(block_type)}."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                # Snapshot before the mutate, so an edit that changes nothing can
                # be recognised and refused WITHOUT the version bump: an empty-op
                # revision would move the version the browser never learns about,
                # and its next save would be rejected as stale.
                before = block_snapshot_of(document)
                replace_block(
                    document,
                    block_id,
                    data=data,
                    author="agent",
                    block_type=block_type,
                )
                if block_snapshot_of(document) == before:
                    raise _NoChange(
                        f"Block {block_id} already has that content (nothing changed)."
                    )
                committed = document
            new_version = committed.meta.version
        except _NoChange as e:
            return e.message
        except StaleVersionError as e:
            return _stale_message(e)
        except BlockNotFoundError:
            return f"No block '{block_id}' in this document."
        except BlockDataError as e:
            return str(e)
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned scratchpad '{slug}' no longer exists."
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
        ) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        return f"Replaced block {block_id} with {block_type} (version {new_version})."


class InsertBlockParams(ParametersModel):
    """Parameters for insert_block."""

    block_type: str
    data: dict[str, Any]
    after_block_id: str | None = None
    expected_version: int | None = None


class InsertBlockTool(BaseTool):
    """Insert a new block."""

    name = "insert_block"
    description = (
        "Insert a new block into the pinned scratchpad. Leave after_block_id "
        "empty to append at the end. Returns the new block's id."
    )
    parameters_model = InsertBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Insert a block."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_type = str(kwargs.get("block_type") or "")
        data = kwargs.get("data")
        after = kwargs.get("after_block_id")

        if not block_type:
            return f"block_type is required. Valid types: {VALID_TYPES}."
        if not isinstance(data, dict):
            return f"data must be an object. Expected {schema_summary(block_type)}."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        created_id = None
        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                block = insert_block(
                    document,
                    block_type=block_type,
                    data=data,
                    author="agent",
                    after_block_id=after,
                )
                created_id = block.id
                committed = document
            # Read after the block exits: the version bump happens on the way
            # out, so reading inside would report the version this call replaced.
            new_version = committed.meta.version
        except StaleVersionError as e:
            return _stale_message(e)
        except BlockDataError as e:
            return str(e)
        except BlockNotFoundError:
            return (
                f"Cannot insert after '{after}': no such block. "
                "Read the document to see the current block ids."
            )
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned scratchpad '{slug}' no longer exists."
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
        ) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        where = f"after {after}" if after else "at the end"
        return (
            f"Inserted block {created_id} ({block_type}) {where} "
            f"(version {new_version})."
        )


class DeleteBlockParams(ParametersModel):
    """Parameters for delete_block."""

    block_id: str
    expected_version: int | None = None


class DeleteBlockTool(BaseTool):
    """Delete one block."""

    name = "delete_block"
    description = (
        "Delete one block from the pinned scratchpad by its id. "
        "Read the document first to get the id."
    )
    parameters_model = DeleteBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Delete a block."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        if not block_id:
            return "block_id is required."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                delete_block(document, block_id)
                committed = document
            new_version = committed.meta.version
        except StaleVersionError as e:
            return _stale_message(e)
        except BlockNotFoundError:
            return f"No block '{block_id}' in this document."
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned scratchpad '{slug}' no longer exists."
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
        ) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        return f"Deleted block {block_id} (version {new_version})."


class MoveBlockParams(ParametersModel):
    """Parameters for move_block."""

    block_id: str
    after_block_id: str | None = None
    expected_version: int | None = None


class MoveBlockTool(BaseTool):
    """Move a block to a new position."""

    name = "move_block"
    description = (
        "Move one block to a different position in the pinned scratchpad. "
        "Leave after_block_id empty to move it to the end."
    )
    parameters_model = MoveBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Move a block."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        after = kwargs.get("after_block_id")
        if not block_id:
            return "block_id is required."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                move_block(document, block_id, after_block_id=after)
                committed = document
            new_version = committed.meta.version
        except StaleVersionError as e:
            return _stale_message(e)
        except BlockNotFoundError as e:
            return str(e)
        except UnicodeDecodeError as e:
            # Before the ValueError clause: a decode failure IS a ValueError, and
            # the generic clause would pass the raw codec message through.
            return _read_error(slug, e)
        except ValueError as e:
            return str(e)
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned scratchpad '{slug}' no longer exists."
        except (InvalidDocumentError, InvalidSlugError) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        where = f"after {after}" if after else "to the end"
        return f"Moved block {block_id} {where} (version {new_version})."


class GetBlockParams(ParametersModel):
    """Parameters for get_block."""

    block_id: str = Field(description="The id of the block to read.")


class GetBlockTool(BaseTool):
    """Read one block in full detail."""

    name = "get_block"
    description = (
        "Read one block by id, showing its type, author, last writer and raw "
        "data object. Use this when you need the exact field names to build a "
        "write, or the block's metadata. For seeing what the scratchpad says, "
        "prefer read_scratchpad."
    )
    parameters_model = GetBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Read one block."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        if not block_id:
            return "block_id is required."

        document, error = _open(slug, for_write=False)
        if error is not None:
            return error

        block = document.get_block(block_id)
        if block is None:
            return f"No block '{block_id}' in this document."
        return render_document(document, [block], include_metadata=True, raw_data=True)


class FindBlocksParams(ParametersModel):
    """Parameters for find_block_id_for_string."""

    search_str: str = Field(
        description="Text to look for. Case-insensitive, matches substrings."
    )


class FindBlockIdsTool(BaseTool):
    """Find blocks whose content contains a string."""

    name = "find_block_id_for_string"
    description = (
        "Find every block whose content contains the given text (case-insensitive "
        "substring match) and return those blocks in full detail, exactly as "
        "get_block renders one. Use this to locate a block by something it says "
        "when you do not know its id."
    )
    parameters_model = FindBlocksParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Find matching blocks."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        search = str(kwargs.get("search_str") or "")
        if not search:
            return "search_str is required."

        document, error = _open(slug, for_write=False)
        if error is not None:
            return error

        needle = search.lower()
        matched = [
            block
            for block in document.blocks
            # Searched against the RENDERED text, which is what the agent has
            # seen. Matching the raw data instead would find "checked" in every
            # checklist block's JSON -- a field name, not content the agent was
            # looking for -- while missing nothing it could actually read.
            if needle in render_data(block.type, block.data).lower()
        ]
        if not matched:
            return (
                f"No block contains '{search}' "
                f"({len(document.blocks)} blocks searched)."
            )
        # Every match is returned IN FULL, not as a list of ids: the caller wants
        # to know what the blocks say, and an id list would make it issue one
        # get_block per match to find out.
        return render_document(document, matched, include_metadata=True, raw_data=True)


class ReadRevisionsParams(ParametersModel):
    """Parameters for read_revisions."""

    limit: int | None = 20
    since_id: int | None = None
    author: str | None = None


class ReadRevisionsTool(BaseTool):
    """Read the scratchpad's revision history."""

    name = "read_revisions"
    description = (
        "Read the pinned scratchpad's revision history, newest first. Shows who "
        "changed what and when, so you can see recent edits by the user."
    )
    parameters_model = ReadRevisionsParams
    needs_verification_in_api = False

    #: The spec caps a history read at 100 entries.
    MAX_LIMIT = 100

    def execute(self, **kwargs: Any) -> str:
        """Read revisions."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        limit = kwargs.get("limit", 20)
        if limit is None:
            limit = 20
        if isinstance(limit, bool) or not isinstance(limit, int):
            return "limit must be an integer."
        if limit < 1:
            return "limit must be at least 1."
        if limit > self.MAX_LIMIT:
            # Capped rather than refused: asking for too much history is not a
            # mistake worth failing, and the cap keeps the output readable.
            limit = self.MAX_LIMIT

        author = kwargs.get("author")
        if author is not None and author not in ("user", "agent", "system"):
            return "author must be one of: user, agent, system."

        since_id = kwargs.get("since_id")
        if since_id is not None and (
            isinstance(since_id, bool) or not isinstance(since_id, int)
        ):
            # `limit` already rejected a bool; `since_id` accepted `true` and read
            # it as id 1, which silently returned the wrong slice of history.
            return "since_id must be an integer."

        # A read, so a markdown scratchpad yields "no revisions" from a document
        # that was actually read, not the write-refusal sentence. A legacy note
        # keeps no revision log, so the honest answer is that there is none.
        _document, error = _open(slug, for_write=False)
        if error is not None:
            return error

        try:
            entries = read_revisions(
                slug, limit=limit, since_id=since_id, author=author
            )
        except (InvalidSlugError, UnicodeDecodeError, OSError) as e:
            # The revision log is read off disk as UTF-8 text: a non-UTF-8 file
            # raises UnicodeDecodeError, and leaving it out would let a read tool
            # raise despite promising a string result.
            return f"Could not read revisions: {e}"

        if not entries:
            return f"No revisions recorded for '{slug}'."

        lines = [
            f"[Document: {slug} | {len(entries)} revision(s), newest first]",
            "",
        ]
        for entry in entries:
            block_ids = [
                str(op.get("block_id"))
                for op in entry.get("ops", [])
                if op.get("block_id")
            ]
            suffix = f" (blocks: {', '.join(block_ids)})" if block_ids else ""
            lines.append(
                f"[rev {entry.get('id')}] {entry.get('timestamp')} "
                f"{entry.get('author')} v{entry.get('version_from')}->"
                f"v{entry.get('version_to')} -- {entry.get('summary')}{suffix}"
            )
        return "\n".join(lines)


class AnswerQuestionParams(ParametersModel):
    """Parameters for answer_question."""

    block_id: str
    expected_version: int | None = None


class AnswerQuestionTool(BaseTool):
    """Mark a question block as answered."""

    name = "notes_answer_question"
    description = (
        "Mark a question block in the pinned scratchpad as answered, so later "
        "reads stop showing it as open. Flips only the answered flag: the "
        "question text the user typed is left exactly as it is. Read the "
        "document first to get the block id."
    )
    parameters_model = AnswerQuestionParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Mark a question answered."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        if not block_id:
            return "block_id is required."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                block = document.get_block(block_id)
                if block is None:
                    raise BlockNotFoundError(f"No block '{block_id}' in this document.")
                if block.type != "question":
                    # Raised rather than returned: a `return` inside the locked
                    # body still runs the version bump on the way out, recording
                    # a change that did not happen.
                    raise _NoChange(
                        f"Block {block_id} is a {block.type}, not a question. "
                        "Only a question block has an answered flag."
                    )
                if block.data.get("answered"):
                    raise _NoChange(f"Block {block_id} is already marked answered.")
                # A copy, not the stored dict: replace_block validates the whole
                # data object against the question schema, and QuestionData
                # forbids extra fields, so the agent must not resend (and thereby
                # risk clobbering) the text the user typed.
                data = dict(block.data)
                data["answered"] = True
                replace_block(
                    document,
                    block_id,
                    data=data,
                    author="agent",
                    block_type="question",
                )
                committed = document
            new_version = committed.meta.version
        except _NoChange as e:
            # Nothing was written: the locked body raised before its bump ran,
            # so the document is exactly as this call found it.
            return e.message
        except StaleVersionError as e:
            return _stale_message(e)
        except BlockNotFoundError:
            return f"No block '{block_id}' in this document."
        except BlockDataError as e:
            return str(e)
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned scratchpad '{slug}' no longer exists."
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
        ) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        return (
            f"Marked question block {block_id} answered (version {new_version}). "
            "Answer the user in chat as well; this only records that the answer "
            "happened."
        )


def all_tools() -> list[type[BaseTool]]:
    """Every block tool, in the order they are worth showing the agent."""
    return [
        ReadBlocksTool,
        GetBlockTool,
        FindBlockIdsTool,
        ReplaceBlockTool,
        InsertBlockTool,
        DeleteBlockTool,
        MoveBlockTool,
        AnswerQuestionTool,
        ReadRevisionsTool,
    ]


__all__ = [
    "ScratchpadUnavailable",
    "AnswerQuestionTool",
    "DeleteBlockTool",
    "FindBlockIdsTool",
    "GetBlockTool",
    "InsertBlockTool",
    "MoveBlockTool",
    "NO_SCRATCHPAD",
    "ReadBlocksTool",
    "ReadRevisionsTool",
    "ReplaceBlockTool",
    "all_tools",
    "render_block",
    "render_data",
    "render_document",
]
