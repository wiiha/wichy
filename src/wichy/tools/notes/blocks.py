"""Document storage: slug identity, file resolution, and block edits.

This module owns everything that touches block documents on disk.

**One slug is one document.** A slug may have a ``.json`` (canonical) and a
``.md`` (legacy note, or the backup conversion leaves behind). The ``.json``
wins whenever both exist, and once it exists the ``.md`` is inert: never
listed, never returned as a document, never written. A slug therefore never
holds two live documents.

**Every mutation runs under one lock.** The lock is held across resolve, read,
version compare and write, not just around the write. An atomic write on its
own prevents a torn file but not a lost update: two actors can both read
version V, both pass the check and both write V+1. The lock is what makes the
409 meaningful and what makes "one request is one version bump" true.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Literal, Mapping, overload

from wichy.tools.file_safety import atomic_write
from wichy.tools.notes.models import (
    FALLBACK_SLUG,
    Author,
    Block,
    BlockDocument,
    BlockMeta,
    DocumentMeta,
    is_valid_slug,
    new_block_id,
    now_iso,
    validate_block_data,
)
from wichy.tools.notes.state import (
    clear_doc_version,
    get_doc_lock,
    get_doc_version,
    rename_document as rename_state,
    set_doc_version,
)

#: Document formats. ``markdown`` is the read-only legacy bridge: a ``.md`` note
#: exposed as a document with one synthetic block, with no block-level writes.
FORMAT_EDITORJS = "editorjs"
FORMAT_MARKDOWN = "markdown"

#: Message every write path returns for a markdown document. Shared so the API
#: and the agent tools cannot drift apart on it.
MARKDOWN_WRITE_REFUSED = "Document is in markdown format. Convert to blocks first."

#: Slugs with an open ``locked_document`` body on the CURRENT thread. The
#: document lock is re-entrant, so without this a nested body would not block
#: and its write would be silently overwritten by the outer, staler copy.
#: Thread-local: two threads may each hold their own body for one slug, which
#: the lock then serialises -- that is the intended concurrency.
_NESTED = threading.local()

#: Serialises slug allocation. Uniqueness is a property of the directory as a
#: whole, so it cannot be guarded by a per-document lock: two creators would
#: hold locks for different slugs and never serialise. Always acquired BEFORE
#: a document lock, never after, so the two cannot deadlock.
_SLUG_ALLOCATION_LOCK = threading.Lock()


class DocumentNotFoundError(LookupError):
    """No document exists for the requested slug."""


class InvalidSlugError(ValueError):
    """A slug was not valid, so it cannot name a document."""


class InvalidDocumentError(ValueError):
    """A stored document could not be parsed."""


class StaleVersionError(RuntimeError):
    """The caller's expected version does not match the stored version.

    Raised while the document lock is held, which is what makes a 409 reliable
    under concurrency.
    """

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"Version mismatch: expected {expected}, found {actual}")
        self.expected = expected
        self.actual = actual


class MarkdownDocumentError(RuntimeError):
    """A block-level write was attempted on a markdown-format document."""


class BlockNotFoundError(LookupError):
    """No block with the given id exists in the document."""


class DocumentDeletionError(OSError):
    """Some file belonging to a slug could not be removed.

    Raised rather than swallowed: a partial delete that reports success is
    how a note reappears on the next list.
    """


def notes_dir() -> Path:
    """Directory holding notes, created if missing.

    Resolved through settings at call time rather than cached, so tests that
    point ``notes_dir`` at a temporary directory take effect.
    """
    from wichy.tools.notes import get_notes_dir

    return Path(get_notes_dir())


def document_path(slug: str) -> Path:
    """Path to the canonical block document for ``slug``.

    Raises:
        InvalidSlugError: The slug is not valid. Checked here as well as at the
            document entry points, because these take a caller-supplied slug
            and turn it straight into a path.
    """
    return notes_dir() / f"{require_valid_slug(slug)}.json"


def legacy_path(slug: str) -> Path:
    """Path to the legacy markdown note for ``slug``.

    Raises:
        InvalidSlugError: The slug is not valid.
    """
    return notes_dir() / f"{require_valid_slug(slug)}.md"


def revisions_path(slug: str) -> Path:
    """Path to the revision log for ``slug``.

    Raises:
        InvalidSlugError: The slug is not valid.
    """
    return notes_dir() / f"{require_valid_slug(slug)}.revisions.jsonl"


def resolve_format(slug: str) -> str | None:
    """Which format ``slug`` resolves to, or None when nothing exists.

    The ``.json`` wins whenever it exists. The ``.md`` is only consulted when
    there is no ``.json``, which is what makes a converted note's backup inert.

    Args:
        slug: A valid slug.

    Returns:
        ``FORMAT_EDITORJS``, ``FORMAT_MARKDOWN``, or None.
    """
    require_valid_slug(slug)
    if document_path(slug).exists():
        return FORMAT_EDITORJS
    if legacy_path(slug).exists():
        return FORMAT_MARKDOWN
    return None


def slug_exists(slug: str) -> bool:
    """Whether any file exists for ``slug``.

    Checks both extensions. Checking only the markdown one would let a new
    document adopt a slug whose block document already exists, giving one slug
    two live documents.

    Raises:
        InvalidSlugError: The slug is not valid.
    """
    require_valid_slug(slug)
    return document_path(slug).exists() or legacy_path(slug).exists()


def require_valid_slug(slug: str) -> str:
    """Return ``slug`` if valid, else raise.

    Every entry point that takes a slug from outside -- a URL, a stored
    filename, a caller -- goes through this, so validity is enforced rather
    than inferred from whatever happens to be on disk.

    Raises:
        InvalidSlugError: The slug is not usable as a document identity.
    """
    if not is_valid_slug(slug):
        raise InvalidSlugError(
            f"Invalid slug '{slug}'. Slugs may contain only lowercase letters, "
            "digits and hyphens, and must start and end with a letter or digit."
        )
    return slug


def generate_slug(title: str) -> str:
    """Derive a slug from a title, never returning an empty one.

    Lowercases, turns spaces into hyphens, then drops everything that is not a
    lowercase letter, digit or hyphen. The fallback lives here rather than at
    the call site so that every caller inherits it: a rename with a
    symbol-only title would otherwise compute an empty slug and write to
    ``<notes_dir>/.json``, losing the note.
    """
    slug = title.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = slug.strip("-")
    return slug or FALLBACK_SLUG


def make_unique_slug(base_slug: str) -> str:
    """Return a slug based on ``base_slug`` that no document uses yet."""
    slug = base_slug
    counter = 1
    while slug_exists(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def unique_slug_for_title(title: str) -> str:
    """Slug for a new document titled ``title``, avoiding existing slugs."""
    return make_unique_slug(generate_slug(title))


def _markdown_document(slug: str, raw: str) -> BlockDocument:
    """Wrap a legacy markdown file as a read-only document with one synthetic block.

    The file's frontmatter is parsed and stripped, and only the BODY becomes the
    synthetic block's text. Keeping the fence would be wrong twice over: the
    conversion step would see ``---`` and produce delimiter blocks for the
    frontmatter, and export would then emit a second frontmatter block on top of
    the one it regenerates.

    The title and timestamps come from that metadata, so a legacy note keeps the
    name the user gave it rather than being labelled with its slug.

    ``meta.version`` is 1 because a markdown document has no versions to count:
    nothing can write to it.
    """
    from wichy.skills.skill import parse_markdown_frontmatter

    metadata, body = parse_markdown_frontmatter(raw)
    return BlockDocument(
        meta=DocumentMeta(
            title=str(metadata.get("title") or slug),
            slug=slug,
            version=1,
            created=str(metadata.get("created") or "") or now_iso(),
            updated=str(metadata.get("updated") or "") or now_iso(),
            last_author="user",
        ),
        blocks=[
            Block(
                id=new_block_id(),
                type="paragraph",
                data={"text": body},
                meta=BlockMeta(author="user", touched_by=["user"]),
            )
        ],
    )


@overload
def load_document(slug: str, *, with_format: Literal[False] = ...) -> BlockDocument: ...


@overload
def load_document(
    slug: str, *, with_format: Literal[True]
) -> tuple[BlockDocument, str]: ...


def load_document(
    slug: str, *, with_format: bool = False
) -> BlockDocument | tuple[BlockDocument, str]:
    """Load the document for ``slug``.

    A markdown note is returned as a synthetic single-block document rather
    than an error, because the legacy bridge is a supported read path.

    Args:
        slug: A valid slug.
        with_format: When true, return ``(document, format)`` instead of the
            document alone. Callers that must refuse block writes need the
            format to know whether to refuse.

    Returns:
        The document, or a ``(document, format)`` pair.

    Raises:
        InvalidSlugError: The slug is not valid.
        DocumentNotFoundError: No file exists for the slug.
        InvalidDocumentError: The stored JSON is not a usable document.
    """
    require_valid_slug(slug)
    resolved = resolve_format(slug)
    if resolved is None:
        raise DocumentNotFoundError(f"No note found for slug '{slug}'.")

    if resolved == FORMAT_MARKDOWN:
        raw = legacy_path(slug).read_text(encoding="utf-8")
        document = _markdown_document(slug, raw)
    else:
        raw_json = document_path(slug).read_text(encoding="utf-8")
        try:
            document = BlockDocument.model_validate(json.loads(raw_json))
        except (json.JSONDecodeError, ValueError) as e:
            raise InvalidDocumentError(
                f"Stored document for '{slug}' is unreadable: {e}"
            ) from e
        # The meta slug is the authority for a document that has one, but a
        # file that has been renamed on disk should still be served under the
        # name it was asked for, so the requested slug wins when they differ.
        document.meta.slug = slug

    if with_format:
        return document, resolved
    return document


def save_document(document: BlockDocument) -> None:
    """Write ``document`` atomically.

    The lock is deliberately NOT taken here. Callers hold the document lock
    across the whole read-check-write sequence; taking it again inside would
    be harmless to a re-entrant lock but would invite a caller to believe this
    function is safe to call on its own, which it is not -- an unlocked
    read-modify-write is exactly the lost update the lock exists to prevent.
    """
    require_valid_slug(document.meta.slug)
    for block in document.blocks:
        # Validated here as well as at each helper, because a document can also
        # arrive by being read back off disk: re-persisting it while changing
        # some unrelated block must not launder invalid data into the file.
        validate_block_data(block.type, block.data)
    document.meta.updated = now_iso()
    payload = document.model_dump(mode="json")
    atomic_write(
        str(document_path(document.meta.slug)),
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def block_snapshot_of(document: BlockDocument) -> list[dict[str, Any]]:
    """Ordered ``{id, type, data}`` view of a document, for diffing.

    Kept here rather than imported from ``revisions`` because ``revisions``
    imports this module; a module-scope import in this direction would be
    circular.
    """
    return [
        {"id": block.id, "type": block.type, "data": dict(block.data)}
        for block in document.blocks
    ]


@contextmanager
def locked_document(
    slug: str,
    expected_version: int | None = None,
    *,
    author: Author,
    summary: str | None = None,
    extra_ops: Iterable[Mapping[str, Any]] = (),
    on_commit: Callable[[BlockDocument, dict], None] | None = None,
) -> Generator[BlockDocument, None, None]:
    """Hold the document lock across a read, an optional version check and a write.

    The body is handed an editable document. Whatever it leaves behind is the
    document that gets persisted, with one version bump, so a body that changes
    many blocks still produces exactly one new version.

    Args:
        slug: A valid slug naming an ``editorjs`` document.
        expected_version: The version the caller believes is current. A
            mismatch raises before the body runs.
        author: Who is making the change. Required, and not defaulted on
            purpose: every mutation must record exactly one revision entry, and
            a default would let a caller omit it and silently break that. The
            entry is diffed from the document as loaded to the document as left
            by the body, so one request is one version bump is one entry by
            construction rather than by each caller remembering to record one.
        summary: Override the generated revision summary.
        extra_ops: Additional ops to include in the recorded entry, for changes
            the block diff cannot express -- notably a revert, whose marker
            records that the change was a deliberate rollback.
        on_commit: Called with the finished document and the revision entry
            about to be written, after the body returns but before either is
            persisted, so state that must be committed together with the version
            bump can be. A raise from it aborts the write and the append, so
            nothing is recorded.

    Yields:
        The loaded document.

    Raises:
        InvalidSlugError: The slug is not valid.
        DocumentNotFoundError: No file exists for the slug.
        MarkdownDocumentError: The slug resolves to a markdown note, which has
            no block-level write path.
        StaleVersionError: ``expected_version`` does not match.
    """
    require_valid_slug(slug)
    with get_doc_lock(slug):
        held = getattr(_NESTED, "slugs", None)
        if held is None:
            held = _NESTED.slugs = set()
        if slug in held:
            # The document lock is re-entrant, so nesting would NOT block. Each
            # level would read the same version, and the inner body's write
            # would be overwritten by the outer body's stale copy on the way
            # out -- a silently discarded write. One body per document.
            raise ValueError(
                f"locked_document('{slug}') is already open on this thread. "
                "Nesting it discards the inner write; do the work in one body."
            )
        held.add(slug)
        try:
            resolved = resolve_format(slug)
            if resolved is None:
                raise DocumentNotFoundError(f"No note found for slug '{slug}'.")
            if resolved == FORMAT_MARKDOWN:
                raise MarkdownDocumentError(MARKDOWN_WRITE_REFUSED)

            document = load_document(slug)
            if (
                expected_version is not None
                and document.meta.version != expected_version
            ):
                raise StaleVersionError(expected_version, document.meta.version)

            # Captured before the body runs, so the recorded entry describes
            # what this request actually changed.
            before = block_snapshot_of(document)

            yield document

            # One bump for the whole body, whatever it touched. Reached only if
            # the body completed: a raise at the yield skips it, so a rejected
            # change leaves the document untouched.
            document.meta.version += 1
            document.meta.last_author = author

            from wichy.tools.notes.revisions import (
                append_entry,
                diff_ops,
                prepare_revision,
            )

            # The entry is allocated here rather than by the caller, so "one
            # mutation is one version bump is one revision" holds by
            # construction. Diffed inside the lock, and one body cannot record
            # two entries.
            entry = prepare_revision(
                document,
                author=author,
                ops=[*extra_ops, *diff_ops(before, block_snapshot_of(document))],
                summary=summary,
            )
            if on_commit is not None:
                # Runs before the write, so anything it changes in meta lands in
                # the same atomic write as the version bump. A raise here aborts
                # without writing, and without appending the entry either.
                on_commit(document, entry)

            # Order matters. The document (carrying the advanced counter) is
            # written FIRST, then the entry is appended. A crash in between then
            # leaves the counter ahead of the log -- a gap, which is recoverable.
            # The reverse order would leave an entry whose id the counter will
            # hand out again, producing duplicate ids that no reader can
            # disambiguate.
            save_document(document)
            set_doc_version(slug, document.meta.version)
            append_entry(slug, entry)
        finally:
            held.discard(slug)


def create_document(
    title: str,
    blocks: Iterable[Mapping[str, Any]] | None = None,
    *,
    author: Author = "user",
) -> BlockDocument:
    """Create a new block document.

    Args:
        title: Document title. An empty title is rejected: it would produce a
            document the sidebar cannot label.
        blocks: Initial blocks, each ``{type, data, id?}``.
        author: Who is creating the document.

    Returns:
        The stored document.

    Raises:
        ValueError: The title is empty.
        BlockDataError: An initial block has invalid data.
    """
    if not title or not title.strip():
        raise ValueError("A document needs a title.")

    stamp = now_iso()
    document = BlockDocument(
        meta=DocumentMeta(
            title=title.strip(),
            slug=unique_slug_for_title(title),
            version=1,
            created=stamp,
            updated=stamp,
            last_author=author,
        )
    )
    for raw_block in blocks or []:
        document.blocks.append(
            make_block(
                str(raw_block.get("type", "")),
                raw_block.get("data"),
                author=author,
                block_id=raw_block.get("id"),
                document=document,
            )
        )

    # Slug uniqueness is a property of the whole directory, not of one
    # document, so the per-document lock cannot protect it: two creators can
    # pick the same free name while holding locks for DIFFERENT slugs and never
    # serialise. The allocation lock is held across the choice AND the write, so
    # a name cannot be claimed by two creators. It is always taken before the
    # document lock and never after, so the two cannot deadlock.
    #
    # Revision 1 is written here as a full snapshot. Without it the log would
    # begin with a change from an unknown baseline, so replay could never
    # rebuild the document and a revert to the beginning would have nothing to
    # restore.
    from wichy.tools.notes.revisions import append_entry as append_revision
    from wichy.tools.notes.revisions import baseline_entry

    with _SLUG_ALLOCATION_LOCK:
        document.meta.slug = make_unique_slug(document.meta.slug)
        with get_doc_lock(document.meta.slug):
            # Same order as locked_document: write the document first, then
            # append. A crash in between must not leave an entry with an id the
            # counter will hand out again.
            entry = baseline_entry(document)
            save_document(document)
            set_doc_version(document.meta.slug, document.meta.version)
            append_revision(document.meta.slug, entry)
    return document


def make_block(
    block_type: str,
    data: Mapping[str, Any] | None,
    *,
    author: Author,
    block_id: str | None = None,
    document: BlockDocument | None = None,
) -> Block:
    """Build a validated block.

    Args:
        block_type: The block's type.
        data: The block's data, validated against the model for ``block_type``.
        author: Who authored the block.
        block_id: An explicit id, or None to generate one.
        document: When given, the generated id is checked against the
            document's existing ids and regenerated on collision.

    Raises:
        BlockDataError: The type is unknown or the data does not match it.
        ValueError: An explicit ``block_id`` is already used in ``document``.
    """
    validated = validate_block_data(block_type, data)
    if block_id is None:
        block_id = new_document_block_id(document)
    elif document is not None and document.has_block(block_id):
        raise ValueError(f"Block id '{block_id}' is already used in this document.")

    meta = BlockMeta(author=author, touched_by=[author])
    return Block(
        id=block_id,
        type=block_type,
        data=validated.model_dump(mode="json"),
        meta=meta,
    )


def new_document_block_id(document: BlockDocument | None) -> str:
    """A block id not already present in ``document``."""
    candidate = new_block_id()
    if document is None:
        return candidate
    while document.has_block(candidate):
        candidate = new_block_id()
    return candidate


def touch_block(block: Block, author: Author) -> None:
    """Record that ``author`` has edited ``block``.

    ``touched_by`` is ordered by first touch and never repeats an actor, so the
    agent can tell a block it has already worked on from one it has not.
    """
    block.meta.updated = now_iso()
    if author not in block.meta.touched_by:
        block.meta.touched_by.append(author)


def replace_block(
    document: BlockDocument,
    block_id: str,
    *,
    data: Mapping[str, Any],
    author: Author,
    block_type: str | None = None,
) -> Block:
    """Replace one block's data in place, keeping its id.

    Args:
        document: The document to mutate.
        block_id: The block to update.
        data: The new data.
        author: Who is making the change.
        block_type: A new type, or None to keep the current one.

    Returns:
        The updated block.

    Raises:
        BlockNotFoundError: No such block.
        BlockDataError: The data does not match the type's schema.
    """
    block = document.get_block(block_id)
    if block is None:
        raise BlockNotFoundError(f"No block '{block_id}' in this document.")

    resolved_type = block_type or block.type
    validated = validate_block_data(resolved_type, data)
    block.type = resolved_type
    block.data = validated.model_dump(mode="json")
    touch_block(block, author)
    return block


def insert_block(
    document: BlockDocument,
    *,
    block_type: str,
    data: Mapping[str, Any],
    author: Author,
    after_block_id: str | None = None,
) -> Block:
    """Insert a new block, generating a new id.

    Args:
        document: The document to mutate.
        block_type: The new block's type.
        data: The new block's data.
        author: Who is inserting.
        after_block_id: Insert directly after this block, or at the end when
            None.

    Returns:
        The inserted block.

    Raises:
        BlockNotFoundError: ``after_block_id`` is given but not present.
        BlockDataError: The data does not match the type's schema.
    """
    if after_block_id is not None and not document.has_block(after_block_id):
        raise BlockNotFoundError(
            f"Cannot insert after '{after_block_id}': no such block."
        )

    block = make_block(block_type, data, author=author, document=document)
    if after_block_id is None:
        document.blocks.append(block)
    else:
        document.blocks.insert(document.block_index(after_block_id) + 1, block)
    return block


def delete_block(document: BlockDocument, block_id: str) -> Block:
    """Remove and return a block.

    Raises:
        BlockNotFoundError: No such block.
    """
    index = document.block_index(block_id)
    if index < 0:
        raise BlockNotFoundError(f"No block '{block_id}' in this document.")
    return document.blocks.pop(index)


def move_block(
    document: BlockDocument, block_id: str, *, after_block_id: str | None = None
) -> Block:
    """Move a block to a new position.

    Args:
        document: The document to mutate.
        block_id: The block to move.
        after_block_id: Put it directly after this block, or at the end when
            None.

    Raises:
        BlockNotFoundError: The block, or the anchor, is not present.
        ValueError: The anchor is the block being moved, which has no meaning.
    """
    block = document.get_block(block_id)
    if block is None:
        raise BlockNotFoundError(f"No block '{block_id}' in this document.")
    if after_block_id is not None and not document.has_block(after_block_id):
        raise BlockNotFoundError(
            f"Cannot move after '{after_block_id}': no such block."
        )
    if after_block_id == block_id:
        raise ValueError("A block cannot be moved after itself.")

    document.blocks.remove(block)
    if after_block_id is None:
        document.blocks.append(block)
    else:
        document.blocks.insert(document.block_index(after_block_id) + 1, block)
    return block


def read_blocks(
    document: BlockDocument,
    *,
    block_type: str | None = None,
    start: int | None = None,
    end: int | None = None,
) -> list[Block]:
    """Select blocks by type and/or index range.

    The range is half-open, ``[start, end)``, applied after type filtering, so
    ``read_blocks(doc, block_type="todo", start=0, end=2)`` means "the first two
    todos" rather than "todos among the first two blocks".

    Args:
        document: The document to read.
        block_type: Keep only blocks of this type.
        start: First index to keep, or None for the beginning.
        end: Index to stop before, or None for the end.

    Returns:
        The selected blocks, in document order.

    Raises:
        ValueError: ``start`` or ``end`` is negative, or ``end`` precedes
            ``start``.
    """
    if start is not None and start < 0:
        raise ValueError("start must not be negative.")
    if end is not None and end < 0:
        raise ValueError("end must not be negative.")
    if start is not None and end is not None and end < start:
        raise ValueError("end must not precede start.")

    selected = [
        block
        for block in document.blocks
        if block_type is None or block.type == block_type
    ]
    return selected[start:end]


def merged_blocks(
    stored: BlockDocument, incoming: Iterable[Mapping[str, Any]], author: Author
) -> list[Block]:
    """Merge editor-supplied blocks with stored server-side meta.

    The editor's ``save()`` returns ``{id, type, data, tunes}`` with no room for
    meta, so incoming blocks replace data and order while the stored
    ``created``/``author``/``touched_by`` are carried across by block id. A
    block with no stored counterpart is a new block and is stamped with the
    acting author.

    Args:
        stored: The document as currently persisted.
        incoming: Blocks as sent by the editor or a caller.
        author: Who is saving.

    Returns:
        The merged block list.

    Raises:
        BlockDataError: A block's data does not match its type's schema.
        ValueError: Two incoming blocks share an id.
    """
    by_id = {block.id: block for block in stored.blocks}
    merged: list[Block] = []
    seen: set[str] = set()

    for raw in incoming:
        block_type = str(raw.get("type", ""))
        validated = validate_block_data(block_type, raw.get("data"))
        raw_id = raw.get("id")
        if raw_id:
            block_id = str(raw_id)
        else:
            # A generated id must not collide with one already placed by this
            # same save, or the merged list would hold a duplicate id and the
            # document would have two blocks a single id could address.
            block_id = new_block_id()
            # Against both this save's ids and the stored ones: matching a
            # stored id would silently adopt that block's created/author/
            # touched_by for what is actually a brand-new block.
            while block_id in seen or block_id in by_id:
                block_id = new_block_id()
        if block_id in seen:
            raise ValueError(f"Block id '{block_id}' appears twice in this save.")
        seen.add(block_id)

        previous = by_id.get(block_id)
        if previous is None:
            meta = BlockMeta(author=author, touched_by=[author])
        else:
            meta = previous.meta.model_copy(deep=True)
            touch_block_from_meta(meta, author)

        merged.append(
            Block(
                id=block_id,
                type=block_type,
                data=validated.model_dump(mode="json"),
                meta=meta,
            )
        )
    return merged


def touch_block_from_meta(meta: BlockMeta, author: Author) -> None:
    """Stamp ``meta`` as edited by ``author`` now.

    The meta-level half of :func:`touch_block`, for merges where only meta has
    been reconstructed so far.
    """
    meta.updated = now_iso()
    if author not in meta.touched_by:
        meta.touched_by.append(author)


def delete_document_files(slug: str) -> list[str]:
    """Remove every file belonging to ``slug``.

    Everything, not just the document: a surviving markdown backup would make
    the note reappear on the next list call, and a surviving revision log would
    outlive the document it describes.

    Args:
        slug: The slug to purge.

    Returns:
        The names of the files that were removed.
    """
    require_valid_slug(slug)
    removed: list[str] = []
    failed: list[str] = []
    # Under the document lock, so a writer that entered before the delete
    # cannot finish afterwards and re-create the file it just removed.
    with get_doc_lock(slug):
        for path in document_files(slug):
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                failed.append(path.name)
    # The slug no longer exists, so a cached version for it would satisfy a
    # stale-write check on a document that is gone.
    clear_doc_version(slug)
    if failed:
        raise DocumentDeletionError(
            f"Could not delete {', '.join(sorted(failed))} for slug '{slug}'. "
            "The note may reappear on the next list; check file permissions."
        )
    return removed


def document_files(slug: str) -> list[Path]:
    """Every file belonging to ``slug``: document, backup, and revision logs."""
    require_valid_slug(slug)
    directory = notes_dir()
    found = [
        directory / f"{slug}.json",
        directory / f"{slug}.md",
        directory / f"{slug}.revisions.jsonl",
    ]
    # Rotated logs carry a timestamp between the slug and the extension, so
    # they are matched by prefix rather than constructed.
    found.extend(sorted(directory.glob(f"{slug}.revisions.*.jsonl")))
    return [path for path in found if path.exists()]


def rename_document_files(old_slug: str, new_slug: str) -> None:
    """Move every file for ``old_slug`` to ``new_slug``.

    The backup moves with the document, so a converted note does not leave its
    original markdown behind under the old slug where it would resurrect as a
    separate note.

    Raises:
        InvalidSlugError: Either slug is invalid.
        ValueError: The target slug already has files.
        DocumentNotFoundError: The source slug has none.
    """
    require_valid_slug(old_slug)
    require_valid_slug(new_slug)
    if slug_exists(new_slug):
        raise ValueError(f"Slug '{new_slug}' is already in use.")

    sources = document_files(old_slug)
    if not sources:
        raise DocumentNotFoundError(f"No note found for slug '{old_slug}'.")

    directory = notes_dir()
    version = 0
    # Held across the move, so a writer that entered under the old slug cannot
    # complete afterwards and re-create it -- which would leave two live
    # documents for what is meant to be one.
    with get_doc_lock(old_slug):
        try:
            version = current_version(old_slug)
        except (DocumentNotFoundError, InvalidDocumentError, MarkdownDocumentError):
            version = 0
        for path in sources:
            suffix = path.name[len(old_slug) :]
            path.rename(directory / f"{new_slug}{suffix}")
        # Queued ops, the lock object and the cached version all move with the
        # document; the browser is polling under the old slug and would never
        # see operations left behind there.
        rename_state(old_slug, new_slug, version)


def list_documents() -> list[dict[str, Any]]:
    """Summarise every document, one row per slug.

    Slugs come from filenames, which nothing guarantees are valid: the notes
    directory is user-writable. A file whose stem is not a valid slug is
    skipped rather than renamed or normalised, because silently rewriting a
    user's filename is worse than declining to serve it.

    A slug with both a ``.json`` and a ``.md`` yields one row, not two.

    Returns:
        Rows of ``{slug, title, version, updated, format}``, sorted by slug.
    """
    directory = notes_dir()
    if not directory.exists():
        return []

    stems: set[str] = set()
    for pattern in ("*.json", "*.md"):
        for path in directory.glob(pattern):
            stem = path.stem
            if is_valid_slug(stem):
                stems.add(stem)

    rows: list[dict[str, Any]] = []
    for slug in sorted(stems):
        try:
            document, resolved = load_document(slug, with_format=True)
        except (DocumentNotFoundError, InvalidDocumentError, InvalidSlugError):
            continue
        rows.append(
            {
                "slug": slug,
                "title": document.meta.title,
                "version": document.meta.version,
                "updated": document.meta.updated,
                "format": resolved,
            }
        )
    return rows


def current_version(slug: str) -> int:
    """The stored version of ``slug``, read from disk.

    Read through the document rather than the in-memory cache, so the answer is
    correct after a restart and for a document the in-memory map has never
    seen.
    """
    return load_document(slug).meta.version


def remember_version(slug: str) -> int:
    """Refresh the in-memory version cache for ``slug`` and return it."""
    version = current_version(slug)
    set_doc_version(slug, version)
    return version


def cached_version(slug: str) -> int:
    """The in-memory version for ``slug``, or 0 when it is not cached."""
    return get_doc_version(slug)
