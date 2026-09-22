"""HTTP API for block notes, revisions, and change notification.

Routes live on the notes blueprint, so every path here is served under
``/tools/notes``.

Two rules apply to almost every route and are enforced in one place each rather
than per handler:

- **A slug is validated before use.** The path segment comes straight from a URL,
  and nothing before this point rejects a dot, a slash or whitespace. An invalid
  slug is a 400, not a 404, because it names no document and never could.
- **A ``markdown``-format document refuses block-level writes with a 409.** A
  block write against a legacy ``.md`` would materialise a ``.json`` beside it and
  give one slug two live documents. The same message is used everywhere so the
  browser and the agent tools cannot disagree about what happened.

Change notification is the other half: the browser posts the ops a user made, the
server injects a summary into the agent's context, and the agent's own edits are
queued back for the browser to poll. Agent-authored ops are never injected back
into the agent's own context, or a turn would be reacting to itself.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from flask import Blueprint, Response, jsonify, request

from wichy.tools.notes import (
    get_scratchpad_state,
    set_scratchpad_state,
)
from wichy.tools.notes.blocks import (
    FORMAT_EDITORJS,
    FORMAT_MARKDOWN,
    MARKDOWN_WRITE_REFUSED,
    BlockNotFoundError,
    DocumentDeletionError,
    DocumentExistsError,
    DocumentNotFoundError,
    InvalidDocumentError,
    InvalidSlugError,
    MarkdownDocumentError,
    StaleVersionError,
    create_document,
    delete_block,
    delete_document_files,
    generate_slug,
    insert_block,
    list_documents,
    load_document,
    locked_document,
    make_unique_slug,
    merged_blocks,
    move_block,
    read_blocks,
    rename_document_files,
    replace_block,
    resolve_format,
    slug_exists,
)
from wichy.tools.notes.markdown import (
    count_blocks,
    export_legacy,
    export_markdown,
    lossy_features,
    markdown_to_blocks,
)
from wichy.tools.notes.models import (
    Author,
    BlockDataError,
    BlockDocument,
    is_valid_slug,
    now_iso,
)
from wichy.tools.notes.state import (
    collapse_by_block,
    get_doc_lock,
    count_distinct_blocks,
    describe_pending,
    discard_stale_changes,
    get_doc_version,
    set_doc_version,
)
from wichy.tools.notes.revisions import (
    IncompleteHistoryError,
    RevisionNotFoundError,
    count_revisions,
    get_revision,
    read_revisions,
    revert_document,
)

#: Message for a slug that names no document at all.
NOT_FOUND = "Note not found"

#: How many distinct blocks may have queued agent ops for one document before
#: the browser is told to re-fetch rather than apply more piecemeal. Counted by
#: DISTINCT BLOCK, not by op: several ops on one block collapse to the latest, so
#: an op count would flag a document the browser can still handle.
MAX_CONFLICT_BLOCKS = 20

#: Brackets around the injected change message, so the agent can tell where a
#: notification starts and ends when several arrive in one context.
CHANGE_MESSAGE_OPEN = "[Document changes for: {}]"
CHANGE_MESSAGE_CLOSE = "[End document changes]"

#: Verb per op kind, for the injected summary.
_OP_VERBS = {
    "add": "Added",
    "update": "Updated",
    "remove": "Deleted",
    "move": "Moved",
}


def _error(message: str, status: int):
    """A JSON error response.

    Errors carry a message rather than a bare code, because the browser shows it
    and the agent reads it: "invalid slug" without saying what a valid slug looks
    like leaves both guessing.
    """
    return jsonify({"error": message}), status


def _json_body() -> dict | None:
    """The request body as a dict, or None if it is not a JSON object.

    A JSON body may be an array, a string or a bare number; ``request.get_json``
    returns whatever was sent. Every caller then indexes it as an object, so a
    non-object body would raise ``AttributeError`` and escape as an HTML 500 --
    which the browser cannot parse and which hides the real problem, that the
    request was malformed. This returns None instead, so the route can answer 400.
    """
    body = request.get_json(silent=True)
    if body is None:
        return {}
    return body if isinstance(body, dict) else None


def _validate_slug(slug: str):
    """Return None if ``slug`` is usable, else a 400 response."""
    if not is_valid_slug(slug):
        return _error(
            f"Invalid slug '{slug}'. Slugs may contain only lowercase letters, "
            "digits and hyphens, and must start and end with a letter or digit.",
            400,
        )
    return None


def _build_document(
    slug: str,
    title: str,
    raw_blocks: Iterable[Mapping[str, Any]],
) -> BlockDocument:
    """Create a block document that KEEPS the given slug.

    ``create_document`` allocates a free slug, which is right for a new note and
    wrong for a conversion: the ``.md`` being converted already occupies the slug,
    so the allocator would move the document to ``<slug>-1`` and the converted
    note would become a different document from the one the user asked for.

    The document is therefore built here and written under ``slug`` directly.

    Args:
        slug: The slug the document must have.
        title: Its title.
        raw_blocks: Block mappings.

    Returns:
        The stored document.

    Raises:
        BlockDataError: A block's data does not match its type's schema.
        ValueError: The title is empty.
    """
    from wichy.tools.notes.blocks import (
        get_doc_lock,
        notes_dir as _notes_dir,
        make_block,
        save_document,
        set_doc_version,
    )
    from wichy.tools.notes.models import BlockDocument, DocumentMeta
    from wichy.tools.notes.revisions import append_entry, baseline_entry

    if not title or not title.strip():
        raise ValueError("A document needs a title.")

    stamp = now_iso()
    document = BlockDocument(
        meta=DocumentMeta(
            title=title.strip(),
            slug=slug,
            version=1,
            created=stamp,
            updated=stamp,
            last_author="system",
        )
    )
    for raw in raw_blocks:
        document.blocks.append(
            make_block(
                str(raw.get("type", "")),
                raw.get("data"),
                author="system",
                block_id=raw.get("id"),
                document=document,
            )
        )

    with get_doc_lock(slug):
        # Re-checked under the lock, which is the only check that counts: two
        # concurrent converts would otherwise both pass the caller's test, both
        # write, and both append a revision with id 1.
        if (_notes_dir() / f"{slug}.json").exists():
            raise DocumentExistsError(
                "This note is already a block document; converting it again would "
                "create a second document for the same slug."
            )
        entry = baseline_entry(document)
        # Document first, then the entry: a crash in between leaves a gap in the
        # ids rather than an id the counter will hand out again.
        save_document(document)
        set_doc_version(slug, document.meta.version)
        append_entry(slug, entry)
    return document


def change_message(title: str, ops: Iterable[Mapping[str, Any]]) -> str:
    """The message injected into the agent's context when a document changes.

    Names the document because the agent may have switched documents since it
    last looked: a bare list of ops would not say which document they describe.

    Args:
        title: The document's title.
        ops: The user's operations.

    Returns:
        The message, with one line per operation.
    """
    lines = [CHANGE_MESSAGE_OPEN.format(title)]
    for op in ops:
        verb = _OP_VERBS.get(str(op.get("op")), "Changed")
        block_type = op.get("block_type")
        block_id = op.get("block_id") or "?"
        # With no type, "Changed block" reads better than "Changed block block".
        described = f"{block_type} block" if block_type else "block"
        lines.append(f"- {verb} {described} (id: {block_id})")
    lines.append(CHANGE_MESSAGE_CLOSE)
    return "\n".join(lines)


def _move_marker(old_slug: str, new_slug: str) -> None:
    """Repoint the scratchpad marker after a rename.

    Without this the marker would name a slug that no longer exists, so the agent
    tools would be aiming at a document that is gone. Both ``primary`` and the
    pinned list are rewritten: the pinned list is presentation state, but leaving
    a stale entry in it would render a marker for a document that is not there.
    """
    state = get_scratchpad_state()
    primary = new_slug if state["primary"] == old_slug else state["primary"]
    pinned = [new_slug if entry == old_slug else entry for entry in state["pinned"]]
    if primary != state["primary"] or pinned != state["pinned"]:
        set_scratchpad_state(primary, pinned)


def _apply_locked(
    slug: str,
    expected_version: int | None,
    author: Author,
    mutate: Callable[[BlockDocument], None],
    **kwargs: Any,
):
    """Run ``mutate`` inside the document lock and report what happened.

    Every mutating route funnels through here so that slug validation, the
    markdown refusal, the version check and the error mapping are identical
    everywhere. A route that forgot one of them would be a hole nothing else
    closes.

    Args:
        slug: The document slug from the URL.
        expected_version: The caller's version, or None to skip the check.
        author: Who is making the change.
        mutate: Called with the open document; may raise BlockNotFoundError.
        **kwargs: Passed through to the lock (summary, extra_ops).

    Returns:
        ``(document, error_response)``; exactly one is None.
    """
    invalid = _validate_slug(slug)
    if invalid is not None:
        return None, invalid

    written: dict[str, BlockDocument] = {}
    try:
        with locked_document(
            slug, expected_version, author=author, **kwargs
        ) as document:
            mutate(document)
            # Captured from INSIDE the lock. Re-reading after the block exits
            # would race: a concurrent writer could bump the version again, so
            # the response would report a version this request did not produce,
            # and a concurrent delete would turn a successful write into a 404.
            written["document"] = document
        return written["document"], None
    except InvalidSlugError as e:
        return None, _error(str(e), 400)
    except DocumentNotFoundError:
        return None, _error(NOT_FOUND, 404)
    except MarkdownDocumentError:
        return None, _error(MARKDOWN_WRITE_REFUSED, 409)
    except StaleVersionError as e:
        return None, _error(str(e), 409)
    except BlockNotFoundError as e:
        return None, _error(str(e), 404)
    except BlockDataError as e:
        return None, _error(str(e), 400)
    except InvalidDocumentError as e:
        # A stored document that cannot be parsed is the server's problem, not
        # the caller's: the request itself may have been perfectly well formed.
        return None, _error(f"Note '{slug}' could not be read: {e}", 500)
    except (ValueError, AttributeError, TypeError) as e:
        # A malformed body reaches deep enough to raise one of these: a non-list
        # `blocks`, a non-dict block entry, a string where an object belongs.
        # All are the caller's mistake, so they map to 400 rather than escaping
        # as an HTML 500 the browser cannot read.
        return None, _error(str(e), 400)
    except OSError as e:
        return None, _error(f"Could not write the note: {e}", 500)


def _document_payload(document, fmt: str) -> dict:
    """The wire form of a document: meta, blocks and format."""
    return {
        "meta": document.meta.model_dump(mode="json"),
        "blocks": [block.model_dump(mode="json") for block in document.blocks],
        "format": fmt,
    }


def register_routes(bp: Blueprint):
    """Register all API routes on the given blueprint."""

    # -------------------------------------------------------------------------
    # Documents
    # -------------------------------------------------------------------------

    @bp.route("/api/notes")
    def list_notes():
        """List every document, one row per slug."""
        try:
            return jsonify({"notes": list_documents()})
        except OSError as e:
            return _error(f"Could not list notes: {e}", 500)

    @bp.route("/api/notes", methods=["POST"])
    def create_note():
        """Create a document.

        A body of markdown is converted to blocks on the way in, so the browser's
        "new note" path does not have to know the conversion rules.
        """
        data = _json_body()
        if data is None:
            return _error("The request body must be a JSON object.", 400)
        title = str(data.get("title") or "").strip()
        if not title:
            return _error("A document needs a title.", 400)

        try:
            raw_blocks = data.get("blocks")
            if raw_blocks is not None and not isinstance(raw_blocks, list):
                return _error("blocks must be a list.", 400)
            if raw_blocks is None:
                # No blocks given: start from the markdown content if there is
                # any, otherwise an empty document.
                raw_blocks = markdown_to_blocks(str(data.get("content") or ""))
            document = create_document(title, raw_blocks, author="user")
        except BlockDataError as e:
            return _error(str(e), 400)
        except (ValueError, AttributeError, TypeError) as e:
            return _error(str(e), 400)
        except OSError as e:
            return _error(f"Could not create the note: {e}", 500)

        return (
            jsonify(
                {
                    "slug": document.meta.slug,
                    "title": document.meta.title,
                    "version": document.meta.version,
                    "created": document.meta.created,
                    "updated": document.meta.updated,
                    "format": FORMAT_EDITORJS,
                }
            ),
            201,
        )

    @bp.route("/api/notes/scratchpad")
    def scratchpad_status():
        """The pinned scratchpad's primary slug and title, plus the pinned list."""
        try:
            state = get_scratchpad_state()
            primary = state["primary"]
            title = None
            if primary and resolve_format(primary) is not None:
                title = load_document(primary).meta.title
            return jsonify(
                {"primary": primary, "title": title, "pinned": state["pinned"]}
            )
        except (InvalidSlugError, InvalidDocumentError) as e:
            return _error(str(e), 500)
        except OSError as e:
            return _error(f"Could not read the scratchpad marker: {e}", 500)

    @bp.route("/api/notes/<slug>")
    def get_note(slug: str):
        """Read one document. A legacy ``.md`` is served as a markdown document."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        try:
            document, fmt = load_document(slug, with_format=True)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (InvalidDocumentError, UnicodeDecodeError, OSError) as e:
            return _error(f"Note '{slug}' exists but could not be read: {e}", 500)

        return jsonify(_document_payload(document, fmt))

    @bp.route("/api/notes/<slug>", methods=["PUT"])
    def update_note(slug: str):
        """Replace a block document's whole block list, or rename it.

        A ``markdown`` document is refused: it has no block-level write path, and
        writing one would create a ``.json`` beside the ``.md``.
        """
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        data = _json_body()

        if data is None:

            return _error("The request body must be a JSON object.", 400)
        expected = data.get("version")
        if expected is None:
            return _error("A version is required to update a document.", 400)

        try:
            current, fmt = load_document(slug, with_format=True)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (InvalidDocumentError, UnicodeDecodeError, OSError) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)

        if fmt == FORMAT_MARKDOWN:
            return _error(MARKDOWN_WRITE_REFUSED, 409)

        meta = data.get("meta")
        if meta is not None and not isinstance(meta, dict):
            return _error("meta must be an object.", 400)
        raw_title = (meta or {}).get("title")
        if raw_title is not None and not isinstance(raw_title, str):
            return _error("meta.title must be a string.", 400)
        new_title = (raw_title or "").strip()
        incoming = data.get("blocks")
        if incoming is not None and not isinstance(incoming, list):
            return _error("blocks must be a list.", 400)
        renaming = bool(new_title) and new_title != current.meta.title

        # Everything -- validating the incoming blocks, renaming, and the write --
        # happens inside ONE locked body, in that order. Renaming moves the
        # document's files, its queued ops and the marker, so a rename that
        # happened before a request was rejected would leave the document moved
        # under a slug the caller never asked for, with an error response that
        # says nothing changed.
        target_slug = slug
        rename_to: list[str] = []

        def mutate(document):
            nonlocal target_slug
            # Merged FIRST, so invalid incoming blocks raise before anything has
            # been moved.
            merged = (
                merged_blocks(document, incoming, "user")
                if incoming is not None
                else None
            )
            if renaming:
                candidate = make_unique_slug(generate_slug(new_title))
                if candidate != slug:
                    rename_document_files(slug, candidate)
                    _move_marker(slug, candidate)
                target_slug = candidate
                rename_to.append(candidate)
                document.meta.title = new_title
                # The document carries its own slug, so the write lands on the
                # renamed file rather than recreating the old one.
                document.meta.slug = candidate
            if merged is not None:
                document.blocks = merged

        document, error = _apply_locked(slug, expected, "user", mutate)
        if error is not None:
            return error
        return jsonify(
            {
                "slug": target_slug,
                "version": document.meta.version,
                "updated": document.meta.updated,
            }
        )

    @bp.route("/api/notes/<slug>", methods=["DELETE"])
    def delete_note(slug: str):
        """Delete every file for a slug, and clear the marker if it pointed here."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        if not slug_exists(slug):
            return _error(NOT_FOUND, 404)

        try:
            delete_document_files(slug)
        except DocumentDeletionError as e:
            return _error(str(e), 500)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except OSError as e:
            return _error(f"Could not delete the note: {e}", 500)

        # A marker left pointing at a deleted slug would aim the agent tools at a
        # document that no longer exists.
        state = get_scratchpad_state()
        if state["primary"] == slug or slug in state["pinned"]:
            primary = None if state["primary"] == slug else state["primary"]
            pinned = [entry for entry in state["pinned"] if entry != slug]
            set_scratchpad_state(primary, pinned)

        return jsonify({"success": True})

    # -------------------------------------------------------------------------
    # Conversion and export
    # -------------------------------------------------------------------------

    @bp.route("/api/notes/<slug>/conversion-preview")
    def conversion_preview(slug: str):
        """What a conversion would produce, and what it would lose."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        try:
            document, fmt = load_document(slug, with_format=True)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (InvalidDocumentError, UnicodeDecodeError, OSError) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)

        if fmt == FORMAT_EDITORJS:
            # Already converted: report the document's own state, never the
            # stale backup's, so the UI does not offer conversion.
            return jsonify(
                {"lossy_features": [], "blocks_estimate": len(document.blocks)}
            )

        body = document.blocks[0].data.get("text", "") if document.blocks else ""
        return jsonify(
            {
                "lossy_features": lossy_features(body),
                "blocks_estimate": count_blocks(body),
            }
        )

    @bp.route("/api/notes/<slug>/convert", methods=["POST"])
    def convert_note(slug: str):
        """Convert a legacy markdown note to a block document.

        The ``.md`` is kept as a backup and becomes inert afterwards. Re-running
        this is a 409 rather than a re-conversion, so a slug can never hold two
        live documents.
        """
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        # Resolved ONCE and kept, so a file appearing between two checks cannot
        # make the route read a block document and treat its first block as
        # markdown. The authoritative re-check happens under the lock below.
        resolved = resolve_format(slug)
        if resolved == FORMAT_EDITORJS:
            return _error(
                "This note is already a block document; converting it again would "
                "create a second document for the same slug.",
                409,
            )
        if resolved is None:
            return _error(NOT_FOUND, 404)

        try:
            legacy = load_document(slug)
        except (DocumentNotFoundError, InvalidDocumentError, OSError) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)

        body = legacy.blocks[0].data.get("text", "") if legacy.blocks else ""
        blocks = markdown_to_blocks(body)
        title = legacy.meta.title or slug

        # The converted document must KEEP the slug it was asked for, so it is the
        # same document before and after conversion rather than a new one. The
        # create path allocates a unique slug, which here collides with the very
        # ``.md`` being converted, so the document is built directly under the
        # intended slug instead of being created and then renamed.
        try:
            document = _build_document(slug, title, blocks)
        except DocumentExistsError as e:
            return _error(str(e), 409)
        except (BlockDataError, ValueError) as e:
            return _error(str(e), 400)
        except OSError as e:
            return _error(f"Could not convert '{slug}': {e}", 500)

        return jsonify(
            {
                "slug": slug,
                "format": FORMAT_EDITORJS,
                "converted_blocks": len(document.blocks),
            }
        )

    @bp.route("/api/notes/<slug>/export")
    def export_note(slug: str):
        """Export as markdown. Read-only: no revision, no version bump."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        try:
            document, fmt = load_document(slug, with_format=True)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (InvalidDocumentError, UnicodeDecodeError, OSError) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)

        if fmt == FORMAT_MARKDOWN:
            body = document.blocks[0].data.get("text", "") if document.blocks else ""
            text = export_legacy(
                body,
                document.meta.title,
                document.meta.created,
                document.meta.updated,
            )
        else:
            text = export_markdown(document)

        disposition = "attachment" if request.args.get("download") == "1" else "inline"
        return Response(
            text,
            mimetype="text/markdown",
            headers={
                "Content-Disposition": f'{disposition}; filename="{slug}.md"',
            },
        )

    # -------------------------------------------------------------------------
    # Pinning
    # -------------------------------------------------------------------------

    @bp.route("/api/notes/<slug>/pin", methods=["POST"])
    def pin_note(slug: str):
        """Pin a document as the scratchpad, or unpin it.

        Pinning a legacy ``.md`` is allowed: it becomes the scratchpad and the
        agent tools then refuse with the markdown message. Refusing to pin it
        would give the user no way to reach a note they can still read.
        """
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        if resolve_format(slug) is None:
            return _error(NOT_FOUND, 404)

        data = _json_body()

        if data is None:

            return _error("The request body must be a JSON object.", 400)
        pinned = bool(data.get("pinned", True))
        state = get_scratchpad_state()

        if pinned:
            entries = list(state["pinned"])
            if slug not in entries:
                entries.append(slug)
            set_scratchpad_state(slug, entries)
        else:
            primary = None if state["primary"] == slug else state["primary"]
            entries = [entry for entry in state["pinned"] if entry != slug]
            set_scratchpad_state(primary, entries)

        fresh = get_scratchpad_state()
        return jsonify({"primary": fresh["primary"], "pinned": fresh["pinned"]})

    # -------------------------------------------------------------------------
    # Blocks
    # -------------------------------------------------------------------------

    @bp.route("/api/notes/<slug>/blocks")
    def get_blocks(slug: str):
        """Read blocks, optionally filtered by type or index range."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        try:
            document, fmt = load_document(slug, with_format=True)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (InvalidDocumentError, UnicodeDecodeError, OSError) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)

        block_type = request.args.get("type")
        start = request.args.get("start", type=int)
        end = request.args.get("end", type=int)

        try:
            selected = read_blocks(
                document, block_type=block_type, start=start, end=end
            )
        except ValueError as e:
            return _error(str(e), 400)

        return jsonify(
            {
                "version": document.meta.version,
                "format": fmt,
                "blocks": [block.model_dump(mode="json") for block in selected],
            }
        )

    @bp.route("/api/notes/<slug>/blocks/<block_id>", methods=["PATCH"])
    def patch_block(slug: str, block_id: str):
        """Replace one block's data, keeping its id."""
        data = _json_body()
        if data is None:
            return _error("The request body must be a JSON object.", 400)
        expected = data.get("version")
        if expected is None:
            return _error("A version is required to update a block.", 400)
        block_type = data.get("block_type")
        if not block_type:
            return _error("block_type is required.", 400)
        if "data" not in data:
            return _error("data is required.", 400)

        def mutate(document):
            replace_block(
                document,
                block_id,
                data=data.get("data") or {},
                author="user",
                block_type=block_type,
            )

        document, error = _apply_locked(slug, expected, "user", mutate)
        if error is not None:
            return error
        return jsonify(
            {
                "version": document.meta.version,
                "block": document.get_block(block_id).model_dump(mode="json"),
            }
        )

    @bp.route("/api/notes/<slug>/blocks", methods=["POST"])
    def add_block(slug: str):
        """Insert a new block, after an anchor or at the end."""
        data = _json_body()
        if data is None:
            return _error("The request body must be a JSON object.", 400)
        expected = data.get("version")
        if expected is None:
            return _error("A version is required to add a block.", 400)
        block_type = data.get("block_type")
        if not block_type:
            return _error("block_type is required.", 400)

        created: dict = {}

        def mutate(document):
            block = insert_block(
                document,
                block_type=block_type,
                data=data.get("data") or {},
                author="user",
                after_block_id=data.get("after_block_id"),
            )
            created["block"] = block

        document, error = _apply_locked(slug, expected, "user", mutate)
        if error is not None:
            return error
        return (
            jsonify(
                {
                    "version": document.meta.version,
                    "block": document.get_block(created["block"].id).model_dump(
                        mode="json"
                    ),
                }
            ),
            201,
        )

    @bp.route("/api/notes/<slug>/blocks/<block_id>", methods=["DELETE"])
    def remove_block(slug: str, block_id: str):
        """Delete one block."""
        expected = request.args.get("version", type=int)
        if expected is None:
            return _error("A version is required to delete a block.", 400)

        def mutate(document):
            delete_block(document, block_id)

        document, error = _apply_locked(slug, expected, "user", mutate)
        if error is not None:
            return error
        return jsonify({"version": document.meta.version, "deleted": block_id})

    @bp.route("/api/notes/<slug>/blocks/<block_id>/move", methods=["POST"])
    def reorder_block(slug: str, block_id: str):
        """Move one block to a new position."""
        data = _json_body()
        if data is None:
            return _error("The request body must be a JSON object.", 400)
        expected = data.get("version")
        if expected is None:
            return _error("A version is required to move a block.", 400)

        def mutate(document):
            move_block(document, block_id, after_block_id=data.get("after_block_id"))

        document, error = _apply_locked(slug, expected, "user", mutate)
        if error is not None:
            return error
        return jsonify({"version": document.meta.version, "block_id": block_id})

    # -------------------------------------------------------------------------
    # Revisions
    # -------------------------------------------------------------------------

    @bp.route("/api/notes/<slug>/revisions")
    def list_revisions(slug: str):
        """Read a document's revisions, newest first."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        if resolve_format(slug) is None:
            return _error(NOT_FOUND, 404)

        try:
            revisions = read_revisions(
                slug,
                limit=request.args.get("limit", type=int),
                since_id=request.args.get("since_id", type=int),
                author=request.args.get("author"),
            )
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except OSError as e:
            return _error(f"Could not read revisions: {e}", 500)

        return jsonify({"revisions": revisions, "total": count_revisions(slug)})

    @bp.route("/api/notes/<slug>/revisions/<int:revision_id>")
    def show_revision(slug: str, revision_id: int):
        """One revision by id."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        try:
            return jsonify({"revision": get_revision(slug, revision_id)})
        except RevisionNotFoundError as e:
            return _error(str(e), 404)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except OSError as e:
            return _error(f"Could not read revisions: {e}", 500)

    @bp.route("/api/notes/<slug>/revisions/<int:revision_id>/revert", methods=["POST"])
    def revert(slug: str, revision_id: int):
        """Restore the state as of before ``revision_id``, as a new revision."""
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        data = _json_body()

        if data is None:

            return _error("The request body must be a JSON object.", 400)
        expected = data.get("version")
        if expected is None:
            return _error("A version is required to revert.", 400)

        # The format is checked before the revision is looked up. A markdown note
        # keeps no revision log, so a lookup-first order would answer 404 ("no such
        # revision") for a document that is present and readable, hiding the real
        # reason: a markdown document has no block-level write path at all.
        try:
            _document, fmt = load_document(slug, with_format=True)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (InvalidDocumentError, UnicodeDecodeError, OSError) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)
        if fmt == FORMAT_MARKDOWN:
            return _error(MARKDOWN_WRITE_REFUSED, 409)

        try:
            document, _entry = revert_document(
                slug, revision_id, expected_version=expected
            )
        except RevisionNotFoundError as e:
            return _error(str(e), 404)
        except IncompleteHistoryError as e:
            # The history does not reach back far enough to rebuild the state
            # exactly. Reported as a conflict with the request rather than a
            # server fault, because a revert built on a partial history would
            # silently drop blocks the log never saw.
            return _error(str(e), 409)
        except InvalidSlugError as e:
            return _error(str(e), 400)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except MarkdownDocumentError:
            return _error(MARKDOWN_WRITE_REFUSED, 409)
        except StaleVersionError as e:
            return _error(str(e), 409)
        except BlockDataError as e:
            return _error(str(e), 400)
        except OSError as e:
            return _error(f"Could not revert: {e}", 500)

        return jsonify(
            {
                "slug": slug,
                "version": document.meta.version,
                "updated": document.meta.updated,
            }
        )

    # -------------------------------------------------------------------------
    # Change notification
    # -------------------------------------------------------------------------

    @bp.route("/api/changes/pending")
    def pending_changes():
        """What the browser polls for: queued agent ops, the version, and busy."""
        slug = request.args.get("slug")
        if not slug:
            return _error("A slug is required.", 400)
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        # The cache is only a fast path; the file is authoritative. A document
        # this process has not seen yet reports its real version rather than 0,
        # which the browser would read as "everything you have is stale".
        # The document must exist. Reporting an empty queue for a slug that
        # names nothing would look to the browser like "no changes" rather than
        # "you are polling a document that is not there", and it would keep
        # polling forever.
        if resolve_format(slug) is None:
            return _error(NOT_FOUND, 404)

        try:
            # Read the version under the document lock. Warming the cache from an
            # unlocked read could overwrite a concurrent write's newer version,
            # and because the warm-up only runs on a cache miss it would then
            # report the stale value forever -- so the browser's own staleness
            # check could never fire.
            with get_doc_lock(slug):
                if get_doc_version(slug) == 0:
                    set_doc_version(slug, load_document(slug).meta.version)
                pending = describe_pending(slug)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            return _error(f"Could not read pending changes: {e}", 500)

        changes = collapse_by_block(pending["changes"])
        pending["changes"] = changes
        pending["slug"] = slug
        # Too many distinct blocks means applying the queue piecemeal is no
        # longer safe, so the browser is told to re-fetch instead.
        pending["conflicted"] = count_distinct_blocks(changes) > MAX_CONFLICT_BLOCKS
        return jsonify(pending)

    @bp.route("/api/changes/ack", methods=["POST"])
    def ack_changes():
        """Acknowledge ops the browser has applied, dropping anything older."""
        data = _json_body()
        if data is None:
            return _error("The request body must be a JSON object.", 400)
        slug = data.get("slug")
        if not slug or not isinstance(slug, str):
            return _error("A slug is required.", 400)
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        up_to = data.get("up_to_version")
        if not isinstance(up_to, int) or isinstance(up_to, bool):
            return _error("up_to_version must be an integer.", 400)

        if resolve_format(slug) is None:
            return _error(NOT_FOUND, 404)

        try:
            discard_stale_changes(slug, up_to)
        except (TypeError, ValueError) as e:
            # A queued op with an unusable version. Reported rather than raised,
            # because the client cannot read an HTML 500.
            return _error(f"Could not acknowledge changes: {e}", 400)
        return jsonify({"status": "ok"})

    @bp.route("/api/changes", methods=["POST"])
    def post_changes():
        """Accept the user's ops: queue them for the browser and inject a summary.

        Ops authored by the agent are filtered out before injection. Without that
        a turn would be handed a description of its own edits and would react to
        itself.
        """
        data = _json_body()
        if data is None:
            return _error("The request body must be a JSON object.", 400)
        slug = data.get("slug")
        if not slug or not isinstance(slug, str):
            return _error("A slug is required.", 400)
        invalid = _validate_slug(slug)
        if invalid is not None:
            return invalid

        ops = data.get("ops")
        if not isinstance(ops, list):
            return _error("ops must be a list.", 400)
        for op in ops:
            if not isinstance(op, dict):
                return _error("Each op must be an object.", 400)

        try:
            document = load_document(slug)
        except DocumentNotFoundError:
            return _error(NOT_FOUND, 404)
        except (
            InvalidDocumentError,
            InvalidSlugError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            return _error(f"Note '{slug}' could not be read: {e}", 500)

        # The session is checked separately from the root agent so the two 503
        # bodies stay distinct. The client branches on the STATUS CODE, so what
        # matters is that both are 503; the body is for a human reading logs.
        from wichy.wichy_server.api import get_active_session

        session = get_active_session()
        if session is None:
            return _error("no active session", 503)
        root_agent = getattr(session, "root_agent", None)
        if root_agent is None:
            return _error("no active root agent", 503)

        version = document.meta.version
        expected = data.get("version")
        if expected is None:
            return _error("A version is required.", 400)
        if isinstance(expected, bool) or not isinstance(expected, int):
            return _error("version must be an integer.", 400)
        if expected != version:
            return _error(
                f"Version mismatch: expected {expected}, found {version}", 409
            )

        # Only ops explicitly authored by the user are injected. The filter is
        # an allow-list, not a deny-list: a missing, null, capitalised or
        # non-string author is not evidence of a user, and treating it as one
        # would leak an agent-authored op into the agent's own context.
        user_ops = [
            {**op, "version": version, "author": "user"}
            for op in ops
            if op.get("author") == "user"
        ]

        if not user_ops:
            return jsonify({"status": "queued", "injected": False})

        message = change_message(document.meta.title, user_ops)
        try:
            # context.add(), not steer(): steer prints to the console on every
            # call, and this is an automatic notification, not a user command.
            root_agent.context.add("user", message)
        except Exception as e:  # pragma: no cover - depends on context internals
            return _error(f"Could not inject the change: {e}", 500)

        return jsonify({"status": "queued", "injected": True})

    # -------------------------------------------------------------------------
    # Settings for the frontend
    # -------------------------------------------------------------------------

    @bp.route("/api/notes/settings")
    def notes_settings():
        """The intervals and mode the frontend needs, so they are not hardcoded."""
        from wichy.config import settings as app_settings

        return jsonify(
            {
                "poll_interval_ms": app_settings.notes_poll_interval_ms,
                "change_debounce_ms": app_settings.notes_change_debounce_ms,
                "save_debounce_ms": app_settings.notes_save_debounce_ms,
                "notification_mode": app_settings.notification_default_mode,
            }
        )


__all__ = ["register_routes"]
