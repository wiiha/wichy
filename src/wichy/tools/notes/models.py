"""Block data models and the document envelope for block notes.

Two layers live here:

- The **per-type data models** (``ParagraphData`` and friends). Block ``data``
  is untrusted input: it arrives from the browser and from agent tool calls, so
  it is validated against the strict model for its declared type before anything
  is written. Strict means ``extra="forbid"`` -- an unexpected key is a rejection,
  not something to quietly store and hand back out later.
- The **document envelope** (``BlockDocument`` and its parts), which is the
  on-disk JSON shape.

Block ``meta`` is server-side only. The editor's ``save()`` returns
``{id, type, data, tunes}`` and has nowhere to put authorship, so the server
keeps meta itself, keyed by block id, and re-attaches it on every save.

Timestamps are ISO-8601 in UTC, matching the UTC timestamps revision-log
rotation uses for filenames.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: Author of a block, document or revision. ``system`` is reserved for
#: migrations and reverts, which are the work of neither actor.
Author = Literal["user", "agent", "system"]

#: Prefix on every generated block id, so ids are recognisable in logs and in
#: the revision log.
BLOCK_ID_PREFIX = "blk-"

#: The title used when a title contains nothing slug-able at all. A slug must be
#: a usable filename, so an empty result is never acceptable.
FALLBACK_SLUG = "untitled"


def now_iso() -> str:
    """Current time as an ISO-8601 string in UTC."""
    return datetime.now(timezone.utc).isoformat()


class BlockDataError(ValueError):
    """Block data did not match the schema for its declared type.

    Carries a message that names the expected schema, because the message is
    surfaced to the browser and to the agent, and "invalid input" tells neither
    of them what to send instead.
    """


class ParagraphData(BaseModel):
    """Data for a ``paragraph`` block."""

    model_config = ConfigDict(extra="forbid")

    text: str


class HeaderData(BaseModel):
    """Data for a ``header`` block."""

    model_config = ConfigDict(extra="forbid")

    text: str
    level: int = Field(ge=1, le=6)


class ListData(BaseModel):
    """Data for a ``list`` block.

    ``style`` is required rather than defaulted: the two styles render very
    differently, and silently defaulting a list the author meant as ordered to
    unordered is worse than refusing it.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[str]
    style: Literal["ordered", "unordered"]


class CodeData(BaseModel):
    """Data for a ``code`` block."""

    model_config = ConfigDict(extra="forbid")

    code: str
    language: str = ""


class QuoteData(BaseModel):
    """Data for a ``quote`` block."""

    model_config = ConfigDict(extra="forbid")

    text: str
    caption: str = ""


class ChecklistItem(BaseModel):
    """One row of a ``checklist`` block.

    A nested model, not a block type: it has no ``block_type`` of its own and
    never appears as a block.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    checked: bool


class ChecklistData(BaseModel):
    """Data for a ``checklist`` block."""

    model_config = ConfigDict(extra="forbid")

    items: list[ChecklistItem]


class DelimiterData(BaseModel):
    """Data for a ``delimiter`` block, which carries none."""

    model_config = ConfigDict(extra="forbid")


class QuestionData(BaseModel):
    """Data for a ``question`` block.

    A semantic marker, not a request for a reply: it reaches the agent through
    the ordinary change-notification path like any other block. ``answered``
    lets the agent record that it has dealt with one, so a later read does not
    prompt the same answer twice.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    answered: bool = False


class DecisionData(BaseModel):
    """Data for a ``decision`` block."""

    model_config = ConfigDict(extra="forbid")

    text: str


class TodoData(BaseModel):
    """Data for a ``todo`` block."""

    model_config = ConfigDict(extra="forbid")

    text: str
    checked: bool = False


#: Every block type that may be stored, mapped to the model validating its data.
BLOCK_DATA_MODELS: dict[str, Type[BaseModel]] = {
    "paragraph": ParagraphData,
    "header": HeaderData,
    "list": ListData,
    "code": CodeData,
    "quote": QuoteData,
    "checklist": ChecklistData,
    "delimiter": DelimiterData,
    "question": QuestionData,
    "decision": DecisionData,
    "todo": TodoData,
}

#: Block types that exist in the editor but are not offered by the agent tools.
#: Kept as an explicit set rather than inferred, so adding a type forces the
#: question "should the agent be able to create this?".
BLOCK_TYPES = frozenset(BLOCK_DATA_MODELS)


def _type_name(annotation: Any) -> str:
    """Render a model field's annotation for a human-readable error message."""
    text = str(annotation)
    for prefix in ("<class '", "'>"):
        text = text.replace(prefix, "")
    return text.replace("typing.", "")


def schema_summary(block_type: str) -> str:
    """Describe the expected data shape for a block type.

    Used in error messages and in the agent tools' schemas, so both audiences
    learn the shape from one source rather than from two that can drift.

    Args:
        block_type: The block type to describe.

    Returns:
        A string such as ``HeaderData(text: str, level: int)``, or a note that
        the type is unknown.
    """
    model = BLOCK_DATA_MODELS.get(block_type)
    if model is None:
        return f"unknown block type '{block_type}'"
    fields = ", ".join(
        f"{name}: {_type_name(field.annotation)}"
        + ("" if field.is_required() else " = optional")
        for name, field in model.model_fields.items()
    )
    return f"{model.__name__}({fields})"


def validate_block_data(block_type: str, data: Mapping[str, Any] | None) -> BaseModel:
    """Validate ``data`` against the model for ``block_type``.

    Args:
        block_type: The block's declared type.
        data: The block's data. ``None`` is treated as an empty mapping, so a
            block type with no fields (``delimiter``) may be sent without data.

    Returns:
        The validated model instance.

    Raises:
        BlockDataError: The type is unknown, or the data does not match the
            schema for it. The message names the expected schema.
    """
    model = BLOCK_DATA_MODELS.get(block_type)
    if model is None:
        known = ", ".join(sorted(BLOCK_DATA_MODELS))
        raise BlockDataError(
            f"Unknown block type '{block_type}'. Valid types: {known}."
        )
    try:
        payload = dict(data or {})
    except (TypeError, ValueError) as e:
        # A non-mapping payload is a malformed request, not a crash: callers
        # turn BlockDataError into a 400, and anything else into a 500.
        raise BlockDataError(
            f"Invalid data for block type '{block_type}': expected an object, "
            f"got {type(data).__name__}. Expected {schema_summary(block_type)}."
        ) from e
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        raise BlockDataError(
            f"Invalid data for block type '{block_type}'. "
            f"Expected {schema_summary(block_type)}. {e}"
        ) from e


def new_block_id() -> str:
    """Generate a new block id.

    Uniqueness within a document is the caller's job; callers use
    :func:`wichy.tools.notes.blocks.new_unique_block_id`, which retries on
    collision.
    """
    return BLOCK_ID_PREFIX + secrets.token_hex(4)


class BlockMeta(BaseModel):
    """Server-side metadata for one block.

    Never round-tripped through the editor, which has nowhere to keep it.
    """

    model_config = ConfigDict(extra="forbid")

    created: str = Field(default_factory=now_iso)
    updated: str = Field(default_factory=now_iso)
    author: Author = "user"
    #: Every actor that has touched this block, in first-touch order. This is
    #: what lets the agent tell a block it has already edited from one the user
    #: wrote.
    touched_by: list[Author] = Field(default_factory=list)


class Block(BaseModel):
    """One block: an id, a type, its data, and server-side meta."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    meta: BlockMeta = Field(default_factory=BlockMeta)

    def validated_data(self) -> BaseModel:
        """Return this block's data as its validated model, raising if invalid."""
        return validate_block_data(self.type, self.data)


class DocumentMeta(BaseModel):
    """Document-level metadata, the ``meta`` object on disk."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    slug: str = ""
    version: int = 1
    created: str = Field(default_factory=now_iso)
    updated: str = Field(default_factory=now_iso)
    last_author: Author = "user"
    is_scratchpad: bool = False
    #: Source of the next revision id. Lives in meta rather than in the log so
    #: that monotonicity survives rotation.
    next_revision_id: int = 1


class BlockDocument(BaseModel):
    """A whole block document: metadata plus an ordered list of blocks."""

    model_config = ConfigDict(extra="forbid")

    meta: DocumentMeta = Field(default_factory=DocumentMeta)
    blocks: list[Block] = Field(default_factory=list)

    def block_index(self, block_id: str) -> int:
        """Position of ``block_id``, or -1 when it is not in this document."""
        for index, block in enumerate(self.blocks):
            if block.id == block_id:
                return index
        return -1

    def get_block(self, block_id: str) -> Block | None:
        """The block with ``block_id``, or None."""
        index = self.block_index(block_id)
        return None if index < 0 else self.blocks[index]

    def has_block(self, block_id: str) -> bool:
        """Whether this document contains ``block_id``."""
        return self.block_index(block_id) >= 0


#: A valid slug: starts and ends with a lowercase ASCII alphanumeric, with only
#: lowercase ASCII alphanumerics and hyphens between. Written as an explicit
#: ASCII class rather than ``str.isalnum``, which accepts non-ASCII letters such
#: as "e-acute" and would let a slug through that no other layer would produce.
#: Excluding the dot is what stops ``<slug>.md`` from aliasing
#: ``<slug>.revisions.jsonl``.
_SLUG_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\Z")


def is_valid_slug(slug: str) -> bool:
    """Whether ``slug`` is usable as a document identity and a filename.

    A valid slug is non-empty, contains only ``[a-z0-9-]``, and starts and ends
    with an alphanumeric character. Anything read off disk or taken from a URL
    must be checked with this before use: neither path rejects a dot, a slash or
    whitespace on its own, so validity has to be enforced rather than assumed.
    """
    return bool(_SLUG_RE.match(slug))
