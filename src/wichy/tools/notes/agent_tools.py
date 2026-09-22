"""The agent's block tools.

Six tools that read and edit block documents, plus `read_scratchpad`, which is
retained but reimplemented over blocks.

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


def _open(slug: str) -> tuple[Any, str | None]:
    """Load the scratchpad, refusing a markdown one.

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

    if fmt == FORMAT_MARKDOWN:
        return None, MARKDOWN_WRITE_REFUSED
    return document, None


def render_block(block: Any, *, include_metadata: bool = True) -> str:
    """Render one block in the form the agent reads.

    Args:
        block: The block to render.
        include_metadata: Whether to include the ``[block id=... type=...]``
            line. Without it the agent cannot name the block in a follow-up
            call, so this is only false for a deliberately content-only read.

    Returns:
        The rendered block.
    """
    header = (
        f"[block id={block.id} type={block.type} author={block.meta.author}]"
        if include_metadata
        else ""
    )
    body = render_data(block.type, block.data)
    return f"{header}\n{body}".strip() if header else body


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


def render_document(
    document: Any, blocks: list[Any] | None = None, *, include_metadata: bool = True
) -> str:
    """Render a document header plus its blocks.

    Args:
        document: The document being read.
        blocks: Which of its blocks to render, or None for all.
        include_metadata: Whether each block gets its ``[block id=...]`` line.
            The total-block count is still reported either way, so the agent can
            tell a filtered read from an empty document.
    """
    chosen = document.blocks if blocks is None else blocks
    lines = [
        f"[Document: {document.meta.slug} | Version: {document.meta.version} | "
        f"Total blocks: {len(document.blocks)}]",
        "",
    ]
    for block in chosen:
        lines.append(render_block(block, include_metadata=include_metadata))
        lines.append("")
    return "\n".join(lines).rstrip()


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


class ReadBlocksParams(ParametersModel):
    """Parameters for read_blocks."""

    filter_type: str | None = None
    block_id: str | None = None
    start_index: int | None = None
    end_index: int | None = None
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

        document, error = _open(slug)
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

        committed = None
        try:
            with locked_document(slug, None, author="agent") as document:
                replace_block(
                    document,
                    block_id,
                    data=data,
                    author="agent",
                    block_type=block_type,
                )
                committed = document
            new_version = committed.meta.version
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
            OSError,
        ) as e:
            return f"Could not read the scratchpad: {e}"

        return f"Replaced block {block_id} with {block_type} (version {new_version})."


class InsertBlockParams(ParametersModel):
    """Parameters for insert_block."""

    block_type: str
    data: dict[str, Any]
    after_block_id: str | None = None


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

        created_id = None
        committed = None
        try:
            with locked_document(slug, None, author="agent") as document:
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
            OSError,
        ) as e:
            return f"Could not read the scratchpad: {e}"

        where = f"after {after}" if after else "at the end"
        return (
            f"Inserted block {created_id} ({block_type}) {where} "
            f"(version {new_version})."
        )


class DeleteBlockParams(ParametersModel):
    """Parameters for delete_block."""

    block_id: str


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

        committed = None
        try:
            with locked_document(slug, None, author="agent") as document:
                delete_block(document, block_id)
                committed = document
            new_version = committed.meta.version
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
            OSError,
        ) as e:
            return f"Could not read the scratchpad: {e}"

        return f"Deleted block {block_id} (version {new_version})."


class MoveBlockParams(ParametersModel):
    """Parameters for move_block."""

    block_id: str
    after_block_id: str | None = None


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

        committed = None
        try:
            with locked_document(slug, None, author="agent") as document:
                move_block(document, block_id, after_block_id=after)
                committed = document
            new_version = committed.meta.version
        except BlockNotFoundError as e:
            return str(e)
        except UnicodeDecodeError as e:
            # Before the ValueError clause: a decode failure IS a ValueError, and
            # the generic clause would pass the raw codec message through.
            return f"Could not read the scratchpad: {e}"
        except ValueError as e:
            return str(e)
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned scratchpad '{slug}' no longer exists."
        except (InvalidDocumentError, InvalidSlugError, OSError) as e:
            return f"Could not read the scratchpad: {e}"

        where = f"after {after}" if after else "to the end"
        return f"Moved block {block_id} {where} (version {new_version})."


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

        # The document is opened first, so a markdown-format scratchpad gets the
        # same refusal every other block tool gives. A markdown note keeps no
        # revision log, so without this the honest answer would be "no revisions
        # recorded" -- true, but it would hide the real reason.
        _document, error = _open(slug)
        if error is not None:
            return error

        try:
            entries = read_revisions(
                slug, limit=limit, since_id=kwargs.get("since_id"), author=author
            )
        except (InvalidSlugError, OSError) as e:
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


def all_tools() -> list[type[BaseTool]]:
    """Every block tool, in the order they are worth showing the agent."""
    return [
        ReadBlocksTool,
        ReplaceBlockTool,
        InsertBlockTool,
        DeleteBlockTool,
        MoveBlockTool,
        ReadRevisionsTool,
    ]


__all__ = [
    "ScratchpadUnavailable",
    "DeleteBlockTool",
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
