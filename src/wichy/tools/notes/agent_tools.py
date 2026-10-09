"""The agent's block tools.

Writes stay on the pinned note and take no slug; reads may name any note.
Two refusals are normal states, not errors: nothing pinned, and a markdown
note, where a block write would materialise a ``.json`` beside its ``.md``.
Block ``data`` is validated in ``execute()``, so it is declared ``dict[str, Any]``.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Mapping

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
    document_lock,
    insert_block,
    list_documents,
    load_document,
    locked_document,
    move_block,
    read_blocks,
    replace_block,
    resolve_format,
)
from wichy.tools.notes.models import (
    BLOCK_DATA_MODELS,
    BlockDataError,
    Proposal,
    is_valid_slug,
)
from wichy.tools.notes.revisions import CorruptRevisionLogError, read_revisions

#: Marker patterns shared by the markdown reader and writer.
HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
ORDERED_MARKER_RE = re.compile(r"^\s*\d+[.)]\s+")
CHECKLIST_MARKER_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s*(.*)$")
QUOTE_MARKER_RE = re.compile(r"^\s*>\s?")
CODE_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*([\w+-]*)\s*$")
CODE_CLOSE_RE = re.compile(r"\n?(`{3,}|~{3,})\s*$")

#: Returned by every tool when no document is pinned.
NO_SCRATCHPAD = (
    "No note is pinned for the agent to edit. Pin one in the notes UI first."
)

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


def _new_block_id() -> str:
    """A fresh block id, for a proposal that has no document to draw one from."""
    from wichy.tools.notes.models import new_block_id as _make

    return _make()


#: Human words for a proposal kind, so the agent can relay it plainly.
_KIND_WORDS = {
    "insert": "adding a block",
    "write": "rewriting a block",
    "delete": "deleting a block",
    "move": "moving a block",
    "change_type": "changing a block's type",
}


def _propose_or_none(slug: str, proposal: Proposal) -> str | None:
    """Record ``proposal`` when the note is in proposal mode; otherwise None.

    None means "write directly": every caller then runs its ordinary locked write.
    The read is the cheapest gate, and it happens before any document mutation.
    """
    try:
        with document_lock(slug):
            fmt = resolve_format(slug)
            if fmt is None:
                raise DocumentNotFoundError(f"No note found for slug '{slug}'.")
            if fmt == FORMAT_MARKDOWN:
                raise MarkdownDocumentError(MARKDOWN_WRITE_REFUSED)
            document = load_document(slug)
            if not document.meta.proposals_enabled:
                return None

            from wichy.tools.notes.proposals import (
                create_or_supersede,
                fingerprint_block,
            )

            proposal.base_version = document.meta.version
            if proposal.kind != "insert" and proposal.block_id is not None:
                target = document.get_block(proposal.block_id)
                if target is None:
                    # Refused here too: a proposal that could only fail would mislead.
                    return f"No block '{proposal.block_id}' in this document."
                if "answer" in proposal.payload and target.type != "question":
                    return (
                        f"Block {proposal.block_id} is a {target.type}, not a "
                        "question. Only a question block has an answered flag."
                    )
                proposal.fingerprint = fingerprint_block(target)

            create_or_supersede(slug, proposal)
            return (
                f"I proposed an edit to the pinned note ({_KIND_WORDS.get(proposal.kind, proposal.kind)}). "
                "It is waiting for the user's review and nothing has changed yet. "
                "The user decides whether to accept it."
            )
    except DocumentNotFoundError:
        return f"The pinned note '{slug}' no longer exists."
    except MarkdownDocumentError:
        return MARKDOWN_WRITE_REFUSED
    except (InvalidDocumentError, InvalidSlugError, UnicodeDecodeError) as e:
        return _read_error(slug, e)
    except OSError as e:
        return _write_error(slug, e)


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
            f"The pinned note name '{slug}' is not a valid note name. "
            "Pin a note in the notes UI to fix it."
        )
    return slug


def _read_slug(kwargs: Mapping[str, Any]) -> tuple[str, str | None]:
    """Resolve the target of a READ, which may name a note explicitly.

    The pin gates where the agent ACTS, not where it reads: a write stays on
    the pinned scratchpad, while a read may name any note -- the notification
    channel names slugs the agent has never had pinned, and a read tool that
    cannot follow one makes those notifications unactionable.

    Args:
        kwargs: The tool call's arguments, which may carry a ``slug``.

    Returns:
        ``(slug, error)``. With no ``slug`` argument the pinned scratchpad is
        the target, exactly as before: the pin is cleared on every CLI start,
        so an unpinned session reads the same sentence every read tool has
        always returned.
    """
    requested = kwargs.get("slug")
    if requested is None or (isinstance(requested, str) and not requested.strip()):
        try:
            return _scratchpad_slug(), None
        except ScratchpadUnavailable as e:
            return "", str(e)
    slug = str(requested).strip()
    if not is_valid_slug(slug):
        return "", (
            f"'{slug}' is not a valid note name. "
            "Call list_notes to see the notes that exist."
        )
    return slug, None


def _open(slug: str, *, for_write: bool = True) -> tuple[Any, str | None]:
    """Load the scratchpad.

    Args:
        slug: The scratchpad slug.
        for_write: When true, a markdown-format scratchpad is refused, because a
            block write against a legacy ``.md`` would materialise a ``.json``
            beside it and give one slug two live documents. When false, the
            markdown note is returned as its synthetic single-block document: a
            note the agent cannot edit is still a note it should be able to read,
            and ``read_note`` has always shown its content.

    Returns:
        ``(document, error)``.
    """
    try:
        document, fmt = load_document(slug, with_format=True)
    except DocumentNotFoundError:
        # Writes arrive via the pin, reads via an explicit slug.
        return None, (
            f"The note '{slug}' no longer exists. "
            "Pin another note in the notes UI, or call list_notes to see "
            "the notes that exist."
        )
    except (InvalidDocumentError, InvalidSlugError, UnicodeDecodeError, OSError) as e:
        return None, f"The note '{slug}' could not be read: {e}"

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
    # author is the creator; touched_by[-1] is the last writer.
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
        # Shape mismatched the declared type: show the raw data instead of raising.
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


def _lines_of(text: str) -> list[str]:
    """The non-empty, stripped lines of a body of text.

    A trailing blank line is an artefact of pasting, not an item the user wants,
    so it is dropped rather than becoming an empty list entry.
    """
    return [line.strip() for line in text.splitlines() if line.strip()]


def text_to_data(block_type: str, text: str) -> dict[str, Any]:
    """Build a block's data from plain text.

    Plain text is what a caller writes without thinking about the block's schema.
    The text is interpreted by the target type, and every type has a defined
    reading of it -- there is no input that maps to nothing:

    - ``paragraph``, ``header``, ``quote``, ``question``, ``decision``, ``todo``:
      the text is the block's ``text``. A leading markdown heading marker sets a
      header's level, and a ``|`` gives a quote its caption.
    - ``list``, ``checklist``: one item per non-empty line, with a leading
      ``-``, ``*``, ``1.`` or ``- [ ]`` marker tolerated and stripped so text
      copied out of a rendered read works as input.
    - ``code``: the body verbatim, with a leading ```` ```lang ```` fence read
      for its language.
    - ``delimiter``: no content, so the text is ignored.

    Args:
        block_type: The type to build data for.
        text: The plain text.

    Returns:
        A data mapping that validates against the type's model.
    """
    if block_type == "delimiter":
        return {}

    if block_type == "list":
        list_items = [LIST_MARKER_RE.sub("", line) for line in _lines_of(text)]
        ordered = bool(ORDERED_MARKER_RE.match(text.strip()))
        return {
            "items": list_items,
            "style": "ordered" if ordered else "unordered",
        }

    if block_type == "checklist":
        checklist_items: list[dict[str, Any]] = []
        for line in _lines_of(text):
            marker = CHECKLIST_MARKER_RE.match(line)
            if marker:
                checklist_items.append(
                    {
                        "text": marker.group(2),
                        "checked": marker.group(1).lower() == "x",
                    }
                )
            else:
                checklist_items.append(
                    {"text": LIST_MARKER_RE.sub("", line), "checked": False}
                )
        return {"items": checklist_items}

    if block_type == "code":
        code_body = text
        language = ""
        trimmed = text.strip("\n")
        first_line, _, remainder = trimmed.partition("\n")
        # Matched against the first line only, since the pattern is anchored.
        fence = CODE_FENCE_RE.match(first_line.strip())
        if fence:
            language = fence.group(2)
            # Drop the opening and closing fences, keeping the body as written.
            code_body = remainder
            code_body = CODE_CLOSE_RE.sub("", code_body)
        return {"code": code_body.rstrip("\n"), "language": language}

    if block_type == "header":
        match = HEADER_RE.match(text.strip())
        if match:
            return {"text": match.group(2).strip(), "level": len(match.group(1))}
        return {"text": text.strip(), "level": 1}

    if block_type == "quote":
        lines = []
        for line in text.splitlines():
            lines.append(QUOTE_MARKER_RE.sub("", line))
        body = "\n".join(lines).strip()
        caption = ""
        if "|" in body:
            body, _, tail = body.rpartition("|")
            caption = tail.strip()
        return {"text": body.strip(), "caption": caption}

    # paragraph, question, decision, todo, and anything unknown: the text itself.
    return {"text": text.strip()}


def text_to_checklist_items(text: str) -> list[dict[str, Any]]:
    """Turn text into checklist items, one per non-empty line.

    Used when converting a non-checklist block into a checklist: the block's text
    is its content, and each line of it becomes an item.

    Args:
        text: The source text.

    Returns:
        One ``{text, checked}`` mapping per line.
    """
    items: list[dict[str, Any]] = []
    for line in _lines_of(text):
        match = CHECKLIST_MARKER_RE.match(line)
        if match:
            items.append(
                {"text": match.group(2), "checked": match.group(1).lower() == "x"}
            )
        else:
            items.append({"text": LIST_MARKER_RE.sub("", line), "checked": False})
    return items or [{"text": "", "checked": False}]


def plain_text_of(block_type: str, data: Mapping[str, Any]) -> str:
    """A block's content as plain text, without markdown decoration.

    Distinct from ``render_data``, which renders FOR READING: a header renders as
    ``## Title`` so it looks like the markdown a reader expects, but its text IS
    "Title". Carrying the rendering across a type change would leak the ``##``
    into the new block's content, so conversion reads the text from here.

    Args:
        block_type: The block's type.
        data: Its data.

    Returns:
        The block's text, with no syntax markers.
    """
    if block_type in ("paragraph", "header", "quote", "question", "decision", "todo"):
        return str(data.get("text", ""))
    if block_type in ("list", "checklist"):
        parts = []
        for item in data.get("items") or []:
            parts.append(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
            )
        return "\n".join(parts)
    if block_type == "code":
        return str(data.get("code", ""))
    if block_type == "delimiter":
        return ""
    # Unknown type: the rendering is the only content there is.
    return render_data(block_type, dict(data))


def convert_data(
    old_type: str, old_data: Mapping[str, Any], new_type: str
) -> dict[str, Any]:
    """Carry a block's content across a type change.

    Every conversion is defined, so changing a block's type never loses its
    content: the block's own text is taken and read as the new type. That is why
    a paragraph becoming a list yields a one-item list rather than an error -- the
    text is the content, and the new type says how to hold it.

    Args:
        old_type: The block's current type.
        old_data: Its current data.
        new_type: The type to convert to.

    Returns:
        Data for *new_type* carrying the block's text.
    """
    if old_type == new_type:
        return dict(old_data)
    if new_type == "delimiter":
        return {}
    text = plain_text_of(old_type, dict(old_data))
    if new_type == "checklist":
        return {"items": text_to_checklist_items(text)}
    return text_to_data(new_type, text)


def scratchpad_header(document: Any) -> str:
    """The one-line header every read of the scratchpad starts with.

    Args:
        document: The document being read.

    Returns:
        The header line: the note's title, but never its slug. The title is
        context the agent can quote to the user; the internal slug is a
        filename it has no word for. An untitled note keeps the unnamed
        shape rather than an empty one.
    """
    title = str(getattr(document.meta, "title", "") or "")
    mode = (
        "proposals: on"
        if getattr(document.meta, "proposals_enabled", True)
        else "proposals: off"
    )
    if not title:
        # A missing title is a state to show as unnamed, not as `Note: `.
        return (
            f"[Note | version {document.meta.version} | "
            f"{len(document.blocks)} blocks | {mode}]"
        )
    return (
        f"[Note: {title} | version {document.meta.version} | "
        f"{len(document.blocks)} blocks | {mode}]"
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
        # The version is what a write needs as expected_version.
        lines = [scratchpad_header(document), ""]
    for block in chosen:
        lines.append(
            render_block(block, include_metadata=include_metadata, raw_data=raw_data)
        )
        lines.append("")
    return "\n".join(lines).rstrip()


#: Render styles read_note accepts; ``md`` aliases ``markdown``.
READ_STYLES = ("markdown", "md", "block")

#: Markdown reads wrap each block's text in a tag carrying its real id.
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


#: Write-phase tail of every tool's except chain; a late OSError is a write failure.
def _write_error(slug: str, error: Exception) -> str:
    """The message for a failure that happened during the write phase."""
    return (
        f"The write to the note '{slug}' failed mid-way ({error}). Its "
        "result is uncertain: read the note to see the current state "
        "before retrying."
    )


def _read_error(slug: str, error: Exception) -> str:
    """The message for a failure that happened while reading the document."""
    return f"Could not read the note '{slug}': {error}"


def _stale_message(error: StaleVersionError) -> str:
    """The message for a write refused because the document moved on.

    Actionable rather than terse: the agent cannot fix a stale read without
    knowing both numbers, and it can always choose to write over the current
    state by omitting ``expected_version``.
    """
    return (
        f"The note changed since you read it (you expected v{error.expected}, it "
        f"is now v{error.actual}). Re-read the note, then retry -- or omit "
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
    slug: str | None = Field(
        default=None,
        description=(
            "Optional: the name of the note to read. Omit to read the pinned " "note."
        ),
    )


class ReadBlocksTool(BaseTool):
    """Read the scratchpad's blocks, optionally filtered."""

    name = "read_blocks"
    description = (
        "Read the pinned note's blocks. Filter by type, by a single block "
        "id, or by an index range. Returns each block's id so you can target it "
        "with write_block, change_block_type, delete_block or move_block. Pass "
        "slug='<note name>' to read another note; omit it to read the pinned "
        "note."
    )
    parameters_model = ReadBlocksParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Read blocks."""
        slug, slug_error = _read_slug(kwargs)
        if slug_error is not None:
            return slug_error

        block_id = kwargs.get("block_id")
        filter_type = kwargs.get("filter_type")
        start_raw = kwargs.get("start_index")
        end_raw = kwargs.get("end_index")
        include_metadata = kwargs.get("include_metadata", True)

        # A single-block read and a filtered range are mutually exclusive.
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

        # Read markdown as content, not the write-refusal.
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


class WriteBlockParams(ParametersModel):
    """Parameters for write_block."""

    block_id: str = Field(description="The id of the block to change.")
    new_content: str = Field(
        description=(
            "The block's new content as plain text. Line breaks are kept. For a "
            "list or checklist, write one item per line (a leading '- ', '* ' or "
            "'1. ' is stripped). For a header, a leading '##' sets its level."
        )
    )
    expected_version: int | None = None


class WriteBlockTool(BaseTool):
    """Replace one block's content."""

    name = "write_block"
    description = (
        "Replace the content of one block, keeping its id and its type. Pass the "
        "new content as plain text -- not JSON. Use this to edit a block you have "
        "already read."
    )
    parameters_model = WriteBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Write a block's content."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        new_content = kwargs.get("new_content")
        if not block_id:
            return "block_id is required."
        if new_content is None:
            return "new_content is required."
        new_content = str(new_content)
        if not new_content.strip():
            # Checked before opening: empty content means delete, not an empty write.
            return "new_content must not be empty. Use delete_block to remove a block."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        proposed = _propose_or_none(
            slug,
            Proposal(
                id=str(uuid.uuid4()),
                kind="write",
                block_id=block_id,
                payload={"text": new_content},
                base_version=expected or 0,
            ),
        )
        if proposed is not None:
            return proposed

        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                block = document.get_block(block_id)
                if block is None:
                    raise BlockNotFoundError(f"No block '{block_id}' in this document.")
                # The type is kept; the text is read using the block's own type.
                block_type = block.type
                data = text_to_data(block_type, new_content)
                before = block_snapshot_of(document)
                replace_block(
                    document, block_id, data=data, author="agent", block_type=block_type
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
            return f"The pinned note '{slug}' no longer exists."
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
        ) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        return f"Updated block {block_id} ({block_type}) (version {new_version})."


class ChangeBlockTypeParams(ParametersModel):
    """Parameters for change_block_type."""

    block_id: str = Field(description="The id of the block to convert.")
    new_type: str = Field(description=f"One of: {VALID_TYPES}.")
    expected_version: int | None = None


class ChangeBlockTypeTool(BaseTool):
    """Change one block's type, keeping its content."""

    name = "change_block_type"
    description = (
        "Change an existing block's type while keeping its content. Use this "
        "instead of deleting and re-inserting. The content carries over: a "
        "paragraph becoming a list becomes a one-item list, and a "
        "multi-line block becoming a checklist becomes one item per line."
    )
    parameters_model = ChangeBlockTypeParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Convert a block's type."""
        try:
            slug = _scratchpad_slug()
        except ScratchpadUnavailable as e:
            return str(e)

        block_id = str(kwargs.get("block_id") or "")
        new_type = str(kwargs.get("new_type") or "").strip()
        if not block_id:
            return "block_id is required."
        if not new_type:
            return f"new_type is required. Valid types: {VALID_TYPES}."
        if new_type not in BLOCK_DATA_MODELS:
            return f"Unknown block type '{new_type}'. Valid types: {VALID_TYPES}."

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        proposed = _propose_or_none(
            slug,
            Proposal(
                id=str(uuid.uuid4()),
                kind="change_type",
                block_id=block_id,
                payload={"type": new_type},
                base_version=expected or 0,
            ),
        )
        if proposed is not None:
            return proposed

        committed = None
        old_type = ""
        try:
            with locked_document(slug, expected, author="agent") as document:
                block = document.get_block(block_id)
                if block is None:
                    raise BlockNotFoundError(f"No block '{block_id}' in this document.")
                old_type = block.type
                if old_type == new_type:
                    raise _NoChange(
                        f"Block {block_id} is already a {new_type} (nothing changed)."
                    )
                data = convert_data(old_type, block.data, new_type)
                before = block_snapshot_of(document)
                replace_block(
                    document, block_id, data=data, author="agent", block_type=new_type
                )
                if block_snapshot_of(document) == before:
                    raise _NoChange(
                        f"Block {block_id} is already a {new_type} (nothing changed)."
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
            return f"The pinned note '{slug}' no longer exists."
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
        ) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        return (
            f"Changed block {block_id} from {old_type} to {new_type} "
            f"(version {new_version})."
        )


class InsertBlockParams(ParametersModel):
    """Parameters for insert_block."""

    block_type: str = Field(description=f"One of: {VALID_TYPES}.")
    new_content: str = Field(
        description=(
            "The new block's content as plain text. For a list or checklist, one "
            "item per line. For a header, a leading '##' sets its level. Ignored "
            "for a delimiter."
        )
    )
    after_block_id: str | None = Field(
        default=None,
        description="Insert after this block id. Empty appends at the end.",
    )
    expected_version: int | None = None


class InsertBlockTool(BaseTool):
    """Insert a new block."""

    name = "insert_block"
    description = (
        "Insert a new block into the pinned note, with its content as plain "
        "text. Give after_block_id to place it after a specific block, or omit "
        "it to add the block at the end. Returns the new block's id."
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
        new_content = kwargs.get("new_content")
        after = kwargs.get("after_block_id")

        if not block_type:
            return f"block_type is required. Valid types: {VALID_TYPES}."
        if block_type not in BLOCK_DATA_MODELS:
            return f"Unknown block type '{block_type}'. Valid types: {VALID_TYPES}."
        if new_content is None:
            return "new_content is required."
        data = text_to_data(block_type, str(new_content))

        expected, version_error = _parse_expected_version(
            kwargs.get("expected_version")
        )
        if version_error is not None:
            return version_error

        proposed = _propose_or_none(
            slug,
            Proposal(
                id=str(uuid.uuid4()),
                kind="insert",
                anchor_id=after,
                payload={"type": block_type, "text": str(new_content)},
                block_id_hint=_new_block_id(),
                base_version=expected or 0,
            ),
        )
        if proposed is not None:
            return proposed

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
            # Read after the locked body exits, once the version bump has run.
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
            return f"The pinned note '{slug}' no longer exists."
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
        "Delete one block from the pinned note by its id. "
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

        proposed = _propose_or_none(
            slug,
            Proposal(
                id=str(uuid.uuid4()),
                kind="delete",
                block_id=block_id,
                base_version=expected or 0,
            ),
        )
        if proposed is not None:
            return proposed

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
            return f"The pinned note '{slug}' no longer exists."
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
        "Move one block to a different position in the pinned note. "
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

        proposed = _propose_or_none(
            slug,
            Proposal(
                id=str(uuid.uuid4()),
                kind="move",
                block_id=block_id,
                payload={"after_block_id": after},
                base_version=expected or 0,
            ),
        )
        if proposed is not None:
            return proposed

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
            # Before the ValueError clause: a decode failure is itself a ValueError.
            return _read_error(slug, e)
        except ValueError as e:
            return str(e)
        except MarkdownDocumentError:
            return MARKDOWN_WRITE_REFUSED
        except DocumentNotFoundError:
            return f"The pinned note '{slug}' no longer exists."
        except (InvalidDocumentError, InvalidSlugError) as e:
            return _read_error(slug, e)
        except OSError as e:
            return _write_error(slug, e)

        where = f"after {after}" if after else "to the end"
        return f"Moved block {block_id} {where} (version {new_version})."


class GetBlockParams(ParametersModel):
    """Parameters for get_block."""

    block_id: str = Field(description="The id of the block to read.")
    slug: str | None = Field(
        default=None,
        description=(
            "Optional: the name of the note to read. Omit to read the pinned " "note."
        ),
    )


class GetBlockTool(BaseTool):
    """Read one block in full detail."""

    name = "get_block"
    description = (
        "Read one block by id, showing its type, author, last writer and raw "
        "data object. Use this when you need the exact field names to build a "
        "write, or the block's metadata. For seeing what the note says, "
        "prefer read_note. Pass slug='<note name>' to read another note; "
        "omit it to read the pinned note."
    )
    parameters_model = GetBlockParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Read one block."""
        slug, slug_error = _read_slug(kwargs)
        if slug_error is not None:
            return slug_error

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
    slug: str | None = Field(
        default=None,
        description=(
            "Optional: the name of the note to read. Omit to read the pinned " "note."
        ),
    )


class FindBlockIdsTool(BaseTool):
    """Find blocks whose content contains a string."""

    name = "find_block_id_for_string"
    description = (
        "Find every block whose content contains the given text (case-insensitive "
        "substring match) and return those blocks in full detail, exactly as "
        "get_block renders one. Use this to locate a block by something it says "
        "when you do not know its id. Pass slug='<note name>' to read another "
        "note; omit it to read the pinned note."
    )
    parameters_model = FindBlocksParams
    needs_verification_in_api = False

    def execute(self, **kwargs: Any) -> str:
        """Find matching blocks."""
        slug, slug_error = _read_slug(kwargs)
        if slug_error is not None:
            return slug_error

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
            # Searched against the rendered text, not the raw JSON.
            if needle in render_data(block.type, block.data).lower()
        ]
        if not matched:
            return (
                f"No block contains '{search}' "
                f"({len(document.blocks)} blocks searched)."
            )
        # Every match is returned in full, not as an id list.
        return render_document(document, matched, include_metadata=True, raw_data=True)


class ReadRevisionsParams(ParametersModel):
    """Parameters for read_revisions."""

    limit: int | None = 20
    since_id: int | None = None
    author: str | None = None
    slug: str | None = Field(
        default=None,
        description=(
            "Optional: the name of the note to read. Omit to read the pinned " "note."
        ),
    )


class ReadRevisionsTool(BaseTool):
    """Read the note's revision history."""

    name = "read_revisions"
    description = (
        "Read the pinned note's revision history, newest first. Shows who "
        "changed what and when, so you can see recent edits by the user. Pass "
        "slug='<note name>' to read another note; omit it to read the pinned "
        "note."
    )
    parameters_model = ReadRevisionsParams
    needs_verification_in_api = False

    #: Cap on entries returned by a history read.
    MAX_LIMIT = 100

    def execute(self, **kwargs: Any) -> str:
        """Read revisions."""
        slug, slug_error = _read_slug(kwargs)
        if slug_error is not None:
            return slug_error

        limit = kwargs.get("limit", 20)
        if limit is None:
            limit = 20
        if isinstance(limit, bool) or not isinstance(limit, int):
            return "limit must be an integer."
        if limit < 1:
            return "limit must be at least 1."
        if limit > self.MAX_LIMIT:
            # Capped, not refused: too much history is not an error worth failing.
            limit = self.MAX_LIMIT

        author = kwargs.get("author")
        if author is not None and author not in ("user", "agent", "system"):
            return "author must be one of: user, agent, system."

        since_id = kwargs.get("since_id")
        if since_id is not None and (
            isinstance(since_id, bool) or not isinstance(since_id, int)
        ):
            # bool subclasses int, so it is rejected explicitly.
            return "since_id must be an integer."

        # A legacy note keeps no revision log, so a read reports none.
        _document, error = _open(slug, for_write=False)
        if error is not None:
            return error

        try:
            entries = read_revisions(
                slug, limit=limit, since_id=since_id, author=author
            )
        except (
            CorruptRevisionLogError,
            InvalidSlugError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            # A non-UTF-8 or torn log must not raise from a tool returning a string.
            return f"Could not read revisions: {e}"

        if not entries:
            return f"No revisions recorded for '{slug}'."

        # Every read reports the mode, so a write knows whether it will apply.
        try:
            mode_doc = load_document(slug)
            mode = (
                "proposals: on" if mode_doc.meta.proposals_enabled else "proposals: off"
            )
        except (DocumentNotFoundError, InvalidDocumentError, OSError):
            mode = "proposals: on"

        lines = [
            f"[Document: {slug} | {len(entries)} revision(s), newest first | {mode}]",
            "",
        ]
        for entry in entries:
            block_ids = [
                str(op.get("block_id"))
                for op in entry.get("ops", [])
                if op.get("block_id")
            ]
            suffix = f" (blocks: {', '.join(block_ids)})" if block_ids else ""
            # An anchor marks where an older build's history begins.
            if entry.get("baseline"):
                suffix += (
                    " [history anchor -- the start of recorded history; "
                    "not a browsable state]"
                )
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
        "Mark a question block in the pinned note as answered, so later "
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

        proposed = _propose_or_none(
            slug,
            Proposal(
                id=str(uuid.uuid4()),
                kind="write",
                block_id=block_id,
                payload={"answer": True},
                base_version=expected or 0,
            ),
        )
        if proposed is not None:
            return proposed

        committed = None
        try:
            with locked_document(slug, expected, author="agent") as document:
                block = document.get_block(block_id)
                if block is None:
                    raise BlockNotFoundError(f"No block '{block_id}' in this document.")
                if block.type != "question":
                    # Raised, not returned: a return still bumps the version.
                    raise _NoChange(
                        f"Block {block_id} is a {block.type}, not a question. "
                        "Only a question block has an answered flag."
                    )
                if block.data.get("answered"):
                    raise _NoChange(f"Block {block_id} is already marked answered.")
                # Copy, not the stored dict: QuestionData forbids extra fields.
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
            # The locked body raised before its bump, so nothing changed.
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
            return f"The pinned note '{slug}' no longer exists."
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


class ListNotesParams(ParametersModel):
    """Parameters for list_notes."""


class ListNotesTool(BaseTool):
    """List every note the read tools can name."""

    name = "list_notes"
    description = (
        "List every note: its name, title, version, when it was last updated "
        "and its format. Use this to find the name to pass as the slug of a "
        "read tool, or to see what notes exist at all."
    )
    parameters_model = ListNotesParams
    needs_verification_in_api: bool = False

    def execute(self, **kwargs: Any) -> str:
        """List the notes."""
        try:
            rows = list_documents()
        except (
            InvalidSlugError,
            InvalidDocumentError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            return f"Could not list notes: {e}"
        if not rows:
            return "There are no notes yet."
        lines = ["[Notes]", ""]
        for row in rows:
            title = row.get("title") or "(untitled)"
            lines.append(
                f"- {row.get('slug')} -- {title} "
                f"(version {row.get('version')}, updated {row.get('updated')}, "
                f"{row.get('format')})"
            )
        return "\n".join(lines)


def all_tools() -> list[type[BaseTool]]:
    """Every block tool, in the order they are worth showing the agent."""
    return [
        ReadBlocksTool,
        GetBlockTool,
        FindBlockIdsTool,
        WriteBlockTool,
        ChangeBlockTypeTool,
        InsertBlockTool,
        DeleteBlockTool,
        MoveBlockTool,
        AnswerQuestionTool,
        ReadRevisionsTool,
        ListNotesTool,
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
    "WriteBlockTool",
    "ChangeBlockTypeTool",
    "ListNotesTool",
    "all_tools",
    "render_block",
    "render_data",
    "render_document",
]
