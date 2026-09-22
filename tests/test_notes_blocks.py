"""Tests for block models, the document store, slug identity and the marker.

Grouped by the decision each group defends:

- data models: block data is untrusted input and is validated strictly
- block CRUD: ids are preserved on update and fresh on insert
- versioning: one locked body is one version bump, whatever it changed
- slug identity: one slug is one document, ``.json`` wins, ``.md`` goes inert
- files: delete and rename cover every file belonging to a slug
- marker: the new shape still reads the old one
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from wichy.config import settings
from wichy.tools.notes import (
    get_scratchpad_slug,
    get_scratchpad_state,
    set_scratchpad_slug,
    set_scratchpad_state,
)
from wichy.tools.notes.blocks import (
    FORMAT_EDITORJS,
    FORMAT_MARKDOWN,
    MARKDOWN_WRITE_REFUSED,
    BlockNotFoundError,
    DocumentNotFoundError,
    InvalidSlugError,
    MarkdownDocumentError,
    StaleVersionError,
    create_document,
    delete_block,
    delete_document_files,
    document_files,
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
from wichy.tools.notes.models import (
    BLOCK_DATA_MODELS,
    FALLBACK_SLUG,
    BlockDataError,
    BlockDocument,
    is_valid_slug,
    new_block_id,
    schema_summary,
    validate_block_data,
)
from wichy.tools.notes.state import reset_state

BLOCK_DATA = {
    "paragraph": {"text": "hello"},
    "header": {"text": "Title", "level": 2},
    "list": {"items": ["a", "b"], "style": "ordered"},
    "code": {"code": "x = 1", "language": "python"},
    "quote": {"text": "quoted", "caption": "someone"},
    "checklist": {"items": [{"text": "done", "checked": True}]},
    "delimiter": {},
    "question": {"text": "why?", "answered": False},
    "decision": {"text": "we chose this"},
    "todo": {"text": "do it", "checked": False},
}


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    """Point the notes directory at a temporary path for one test.

    ``notes_dir`` is a derived property over ``notes_dir_name``, and joining an
    absolute name onto a relative base yields the absolute path, so overriding
    the name is enough and monkeypatch restores it afterwards.
    """
    target = tmp_path / "notes"
    monkeypatch.setattr(settings, "notes_dir_name", str(target))
    target.mkdir(parents=True, exist_ok=True)
    reset_state()
    yield target
    reset_state()


# ---------------------------------------------------------------------------
# Slug validity
# ---------------------------------------------------------------------------


class TestSlugValidity:
    @pytest.mark.parametrize(
        "slug",
        ["a", "note", "my-note", "note-1", "a1", "2026-09-17-thing", "0"],
    )
    def test_accepts_conforming_slugs(self, slug):
        assert is_valid_slug(slug)

    @pytest.mark.parametrize(
        "slug",
        [
            "",
            "my.note",  # a dot would alias <slug>.md with <slug>.revisions.x.jsonl
            "My-Note",  # uppercase is not what the generator emits
            "has space",
            "slash/slug",
            "-leading",
            "trailing-",
            "under_score",
            "caf\u00e9",  # non-ASCII letters are not [a-z0-9-]
            "note!",
        ],
    )
    def test_rejects_non_conforming_slugs(self, slug):
        assert not is_valid_slug(slug)

    def test_require_valid_slug_raises_with_guidance(self, notes_dir):
        from wichy.tools.notes.blocks import require_valid_slug

        with pytest.raises(InvalidSlugError) as err:
            require_valid_slug("bad.slug")
        # The message must say what a valid slug looks like, not merely that
        # this one is wrong.
        assert "lowercase" in str(err.value)


class TestGenerateSlug:
    def test_lowercases_and_hyphenates(self):
        assert generate_slug("My Great Note") == "my-great-note"

    def test_strips_dots_so_slugs_cannot_alias_logs(self):
        assert generate_slug("my.note") == "mynote"

    @pytest.mark.parametrize("title", ["!!!", "   ", "\u00e9\u00e9", "..."])
    def test_never_returns_empty(self, title):
        slug = generate_slug(title)
        assert slug
        assert is_valid_slug(slug)

    def test_symbol_only_title_falls_back(self):
        assert generate_slug("!!!") == FALLBACK_SLUG

    def test_make_unique_slug_avoids_existing(self, notes_dir):
        create_document("Shared Title")
        assert make_unique_slug("shared-title") == "shared-title-1"


# ---------------------------------------------------------------------------
# Per-type data models
# ---------------------------------------------------------------------------


class TestBlockDataModels:
    @pytest.mark.parametrize("block_type", sorted(BLOCK_DATA))
    def test_every_type_has_a_model(self, block_type):
        assert block_type in BLOCK_DATA_MODELS

    @pytest.mark.parametrize("block_type", sorted(BLOCK_DATA))
    def test_accepts_valid_data_and_preserves_it(self, block_type):
        """The data must come back whole, not merely not-raise."""
        model = validate_block_data(block_type, BLOCK_DATA[block_type])
        assert model.model_dump(mode="json") == BLOCK_DATA[block_type]

    @pytest.mark.parametrize("block_type", sorted(BLOCK_DATA))
    def test_rejects_extra_keys(self, block_type):
        data = dict(BLOCK_DATA[block_type])
        data["surprise"] = "value"
        with pytest.raises(BlockDataError):
            validate_block_data(block_type, data)

    def test_unknown_type_names_the_valid_ones(self):
        with pytest.raises(BlockDataError) as err:
            validate_block_data("wat", {"text": "x"})
        assert "paragraph" in str(err.value)
        assert "todo" in str(err.value)

    def test_header_level_bounds(self):
        validate_block_data("header", {"text": "t", "level": 1})
        validate_block_data("header", {"text": "t", "level": 6})
        for level in (0, 7, -1):
            with pytest.raises(BlockDataError):
                validate_block_data("header", {"text": "t", "level": level})

    def test_list_style_is_constrained(self):
        with pytest.raises(BlockDataError):
            validate_block_data("list", {"items": ["a"], "style": "bullet"})

    def test_missing_required_field_is_rejected(self):
        with pytest.raises(BlockDataError):
            validate_block_data("paragraph", {})

    def test_delimiter_needs_no_data(self):
        assert validate_block_data("delimiter", None).model_dump() == {}
        assert validate_block_data("delimiter", {}).model_dump() == {}
        # Strictness still applies: "no data" is not "any data".
        with pytest.raises(BlockDataError):
            validate_block_data("delimiter", {"unexpected": 1})

    def test_checklist_item_shape_is_nested(self):
        validate_block_data("checklist", {"items": [{"text": "a", "checked": False}]})
        with pytest.raises(BlockDataError):
            validate_block_data("checklist", {"items": ["bare string"]})
        # A checklist item is not a block type of its own.
        assert "checklist-item" not in BLOCK_DATA_MODELS

    def test_error_message_names_the_schema(self):
        with pytest.raises(BlockDataError) as err:
            validate_block_data("quote", {"text": 5})
        assert "QuoteData" in str(err.value)

    def test_schema_summary_marks_optional_fields(self):
        summary = schema_summary("code")
        assert "CodeData" in summary
        # `language` has a default and must be labelled; `code` must not be.
        assert "language: str = optional" in summary
        assert "code: str = optional" not in summary

    def test_schema_summary_unknown_type(self):
        assert "unknown block type" in schema_summary("nope")


# ---------------------------------------------------------------------------
# Block CRUD
# ---------------------------------------------------------------------------


class TestBlockCrud:
    def _doc(self):
        return create_document(
            "CRUD",
            [
                {"type": "paragraph", "data": {"text": "one"}},
                {"type": "header", "data": {"text": "two", "level": 1}},
            ],
        )

    def test_create_assigns_ids_and_defaults_version(self, notes_dir):
        document = self._doc()
        assert len(document.blocks) == 2
        assert document.meta.version == 1
        assert len({b.id for b in document.blocks}) == 2

    def test_replace_keeps_block_id(self, notes_dir):
        document = self._doc()
        block_id = document.blocks[0].id
        with locked_document(document.meta.slug, 1, author="user") as doc:
            replace_block(doc, block_id, data={"text": "changed"}, author="agent")

        reloaded = load_document(document.meta.slug)
        assert reloaded.blocks[0].id == block_id
        assert reloaded.blocks[0].data["text"] == "changed"

    def test_insert_generates_a_new_id(self, notes_dir):
        document = self._doc()
        existing = {b.id for b in document.blocks}
        with locked_document(document.meta.slug, 1, author="user") as doc:
            inserted = insert_block(
                doc, block_type="todo", data={"text": "new"}, author="agent"
            )
            assert inserted.id not in existing

        assert len(load_document(document.meta.slug).blocks) == 3

    def test_insert_after_anchor_places_correctly(self, notes_dir):
        document = self._doc()
        anchor = document.blocks[0].id
        with locked_document(document.meta.slug, 1, author="user") as doc:
            insert_block(
                doc,
                block_type="todo",
                data={"text": "mid"},
                author="agent",
                after_block_id=anchor,
            )
        reloaded = load_document(document.meta.slug)
        assert [b.type for b in reloaded.blocks] == ["paragraph", "todo", "header"]

    def test_insert_after_missing_anchor_is_rejected(self, notes_dir):
        document = self._doc()
        with pytest.raises(BlockNotFoundError):
            with locked_document(document.meta.slug, 1, author="user") as doc:
                insert_block(
                    doc,
                    block_type="todo",
                    data={"text": "x"},
                    author="agent",
                    after_block_id="blk-nope",
                )

    def test_delete_removes_block(self, notes_dir):
        document = self._doc()
        first = document.blocks[0].id
        with locked_document(document.meta.slug, 1, author="user") as doc:
            delete_block(doc, first)
        reloaded = load_document(document.meta.slug)
        assert [b.type for b in reloaded.blocks] == ["header"]

    def test_delete_missing_block_is_rejected(self, notes_dir):
        document = self._doc()
        with pytest.raises(BlockNotFoundError):
            with locked_document(document.meta.slug, 1, author="user") as doc:
                delete_block(doc, "blk-nope")

    def test_move_reorders(self, notes_dir):
        document = self._doc()
        first, second = document.blocks[0].id, document.blocks[1].id
        with locked_document(document.meta.slug, 1, author="user") as doc:
            move_block(doc, first, after_block_id=second)
        reloaded = load_document(document.meta.slug)
        assert [b.id for b in reloaded.blocks] == [second, first]

    def test_move_to_end(self, notes_dir):
        document = self._doc()
        first, second = document.blocks[0].id, document.blocks[1].id
        with locked_document(document.meta.slug, 1, author="user") as doc:
            move_block(doc, first, after_block_id=None)
        reloaded = load_document(document.meta.slug)
        assert [b.id for b in reloaded.blocks] == [second, first]

    def test_move_after_self_is_rejected(self, notes_dir):
        document = self._doc()
        first = document.blocks[0].id
        with pytest.raises(ValueError):
            with locked_document(document.meta.slug, 1, author="user") as doc:
                move_block(doc, first, after_block_id=first)

    def test_replace_rejects_invalid_data_and_leaves_document_intact(self, notes_dir):
        document = self._doc()
        first = document.blocks[0].id
        with pytest.raises(BlockDataError):
            with locked_document(document.meta.slug, 1, author="user") as doc:
                replace_block(doc, first, data={"nope": 1}, author="agent")

        # The rejection happened before the body completed, so the version must
        # not have moved and the text must be unchanged.
        reloaded = load_document(document.meta.slug)
        assert reloaded.meta.version == 1
        assert reloaded.blocks[0].data["text"] == "one"

    def test_touched_by_records_each_actor_once_in_first_touch_order(self, notes_dir):
        """INV-007: the agent can tell a block it has touched from one it has not."""
        document = self._doc()
        first = document.blocks[0].id
        # The document was created by the user, so the user is already recorded
        # as having touched this block before the agent ever sees it.
        assert load_document(document.meta.slug).blocks[0].meta.touched_by == ["user"]

        with locked_document(document.meta.slug, 1, author="user") as doc:
            replace_block(doc, first, data={"text": "a"}, author="agent")
        with locked_document(document.meta.slug, 2, author="user") as doc:
            replace_block(doc, first, data={"text": "b"}, author="user")
        with locked_document(document.meta.slug, 3, author="user") as doc:
            replace_block(doc, first, data={"text": "c"}, author="agent")

        block = load_document(document.meta.slug).blocks[0]
        # Each actor appears once, in the order it first touched the block.
        assert block.meta.touched_by == ["user", "agent"]
        # author is who created the block, not who last edited it.
        assert block.meta.author == "user"

    def test_agent_authored_block_is_distinguishable(self, notes_dir):
        document = create_document(
            "Authored",
            [{"type": "paragraph", "data": {"text": "by agent"}}],
            author="agent",
        )
        block = load_document(document.meta.slug).blocks[0]
        assert block.meta.author == "agent"
        assert block.meta.touched_by == ["agent"]

    def test_read_blocks_filters_by_type(self, notes_dir):
        document = create_document(
            "Filter",
            [
                {"type": "todo", "data": {"text": "t1"}},
                {"type": "paragraph", "data": {"text": "p"}},
                {"type": "todo", "data": {"text": "t2"}},
            ],
        )
        todos = read_blocks(document, block_type="todo")
        assert [b.data["text"] for b in todos] == ["t1", "t2"]

    def test_read_blocks_range_applies_after_type_filter(self, notes_dir):
        document = create_document(
            "Range",
            [
                {"type": "todo", "data": {"text": "t1"}},
                {"type": "paragraph", "data": {"text": "p"}},
                {"type": "todo", "data": {"text": "t2"}},
                {"type": "todo", "data": {"text": "t3"}},
            ],
        )
        assert [
            b.data["text"] for b in read_blocks(document, block_type="todo", start=1)
        ] == [
            "t2",
            "t3",
        ]
        # Inclusive: start=1, end=2 names the second and third todos.
        assert [
            b.data["text"]
            for b in read_blocks(document, block_type="todo", start=1, end=2)
        ] == ["t2", "t3"]
        # A single index selects exactly one block.
        assert [
            b.data["text"]
            for b in read_blocks(document, block_type="todo", start=1, end=1)
        ] == ["t2"]

    @pytest.mark.parametrize(
        "kwargs",
        [{"start": -1}, {"end": -1}, {"start": 3, "end": 1}],
    )
    def test_read_blocks_rejects_bad_range(self, notes_dir, kwargs):
        document = create_document("Bad", [{"type": "todo", "data": {"text": "t"}}])
        with pytest.raises(ValueError):
            read_blocks(document, **kwargs)


# ---------------------------------------------------------------------------
# Versioning and the lock
# ---------------------------------------------------------------------------


class TestVersioning:
    def test_one_locked_body_is_one_version_bump(self, notes_dir):
        document = create_document(
            "Bump",
            [
                {"type": "paragraph", "data": {"text": "a"}},
                {"type": "paragraph", "data": {"text": "b"}},
                {"type": "paragraph", "data": {"text": "c"}},
            ],
        )
        ids = [b.id for b in document.blocks]
        with locked_document(document.meta.slug, 1, author="user") as doc:
            for block_id in ids:
                replace_block(doc, block_id, data={"text": "x"}, author="user")

        reloaded = load_document(document.meta.slug)
        # Three blocks changed, but one request is one transaction.
        assert reloaded.meta.version == 2

    def test_stale_version_raises_and_changes_nothing(self, notes_dir):
        document = create_document(
            "Stale", [{"type": "paragraph", "data": {"text": "a"}}]
        )
        with locked_document(document.meta.slug, 1, author="user") as doc:
            replace_block(doc, doc.blocks[0].id, data={"text": "b"}, author="user")

        with pytest.raises(StaleVersionError) as err:
            with locked_document(document.meta.slug, 1, author="user") as doc:
                replace_block(doc, doc.blocks[0].id, data={"text": "c"}, author="user")
        assert err.value.expected == 1
        assert err.value.actual == 2

        reloaded = load_document(document.meta.slug)
        assert reloaded.meta.version == 2
        assert reloaded.blocks[0].data["text"] == "b"

    def test_updated_advances_on_save(self, notes_dir):
        document = create_document(
            "Clock", [{"type": "paragraph", "data": {"text": "a"}}]
        )
        before = document.meta.updated
        time.sleep(0.01)
        with locked_document(document.meta.slug, 1, author="user") as doc:
            replace_block(doc, doc.blocks[0].id, data={"text": "b"}, author="user")
        assert load_document(document.meta.slug).meta.updated > before

    def test_lock_is_held_for_the_whole_body(self, notes_dir):
        """A second actor cannot enter the body while the first is inside it.

        Without the lock the version compare and the write are not one step, and
        two writers that both read version V both write V+1.
        """
        document = create_document(
            "Locked", [{"type": "paragraph", "data": {"text": "a"}}]
        )
        entered = threading.Event()
        release = threading.Event()
        blocked = threading.Event()
        outcome: list[str] = []

        def first():
            with locked_document(document.meta.slug, 1, author="user") as doc:
                entered.set()
                release.wait(timeout=5)
                replace_block(
                    doc, doc.blocks[0].id, data={"text": "first"}, author="user"
                )
            outcome.append("first-wrote")

        def second():
            entered.wait(timeout=5)
            # Signal intent, then try to enter. The wait below is what proves
            # exclusion: if the lock were absent this would proceed immediately.
            blocked.set()
            try:
                with locked_document(document.meta.slug, 1, author="user") as doc:
                    replace_block(
                        doc, doc.blocks[0].id, data={"text": "second"}, author="user"
                    )
                outcome.append("second-wrote")
            except StaleVersionError:
                outcome.append("second-conflicted")

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        try:
            assert blocked.wait(timeout=5)
            # While the first holds the body, the second must not get through.
            assert outcome == []
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=5)

        # Exactly one actor wrote; the other lost the version race rather than
        # silently overwriting the winner's edit.
        assert sorted(outcome) == ["first-wrote", "second-conflicted"]
        for thread in threads:
            assert not thread.is_alive()
        assert load_document(document.meta.slug).blocks[0].data["text"] == "first"

    def test_markdown_document_refuses_block_writes(self, notes_dir):
        (notes_dir / "legacy.md").write_text("# Legacy\n\nbody\n", encoding="utf-8")
        with pytest.raises(MarkdownDocumentError) as err:
            with locked_document("legacy", 1, author="user"):
                pass
        assert str(err.value) == MARKDOWN_WRITE_REFUSED

    def test_missing_document_is_not_found(self, notes_dir):
        with pytest.raises(DocumentNotFoundError):
            with locked_document("nope", 1, author="user"):
                pass

    def test_invalid_slug_is_rejected_before_any_io(self, notes_dir):
        with pytest.raises(InvalidSlugError):
            with locked_document("bad.slug", 1, author="user"):
                pass


# ---------------------------------------------------------------------------
# Slug identity and file resolution
# ---------------------------------------------------------------------------


class TestSlugIdentity:
    def test_create_then_load_round_trips(self, notes_dir):
        document = create_document(
            "Round Trip", [{"type": "paragraph", "data": {"text": "x"}}]
        )
        assert document.meta.slug == "round-trip"
        reloaded = load_document("round-trip")
        assert reloaded.blocks[0].data["text"] == "x"

    def test_json_wins_over_md(self, notes_dir):
        document = create_document(
            "Both", [{"type": "paragraph", "data": {"text": "json"}}]
        )
        (notes_dir / "both.md").write_text("markdown body", encoding="utf-8")

        assert resolve_format("both") == FORMAT_EDITORJS
        document = load_document("both")
        assert document.blocks[0].data["text"] == "json"

    def test_list_emits_one_row_per_slug_not_per_file(self, notes_dir):
        create_document("One Row", [{"type": "paragraph", "data": {"text": "j"}}])
        (notes_dir / "one-row.md").write_text("backup", encoding="utf-8")

        rows = [row for row in list_documents() if row["slug"] == "one-row"]
        assert len(rows) == 1
        assert rows[0]["format"] == FORMAT_EDITORJS

    def test_md_only_note_reports_markdown_format(self, notes_dir):
        (notes_dir / "old-note.md").write_text("# Old\n\ntext\n", encoding="utf-8")
        assert resolve_format("old-note") == FORMAT_MARKDOWN
        rows = [row for row in list_documents() if row["slug"] == "old-note"]
        assert rows[0]["format"] == FORMAT_MARKDOWN

    def test_legacy_note_is_readable_as_one_synthetic_block(self, notes_dir):
        (notes_dir / "old-note.md").write_text("# Old\n\ntext\n", encoding="utf-8")
        document, resolved = load_document("old-note", with_format=True)
        assert resolved == FORMAT_MARKDOWN
        assert len(document.blocks) == 1
        assert document.blocks[0].type == "paragraph"
        assert "# Old" in document.blocks[0].data["text"]

    def test_md_is_never_written_once_a_json_exists(self, notes_dir):
        """The backup is inert: a block write must not touch it."""
        create_document("Preserve", [{"type": "paragraph", "data": {"text": "json"}}])
        backup = notes_dir / "preserve.md"
        backup.write_text("markdown body", encoding="utf-8")

        with locked_document("preserve", 1, author="user") as doc:
            replace_block(doc, doc.blocks[0].id, data={"text": "edited"}, author="user")

        # Byte-identical, and the edit landed in the .json.
        assert backup.read_text(encoding="utf-8") == "markdown body"
        assert load_document("preserve").blocks[0].data["text"] == "edited"

    def test_a_json_only_mutation_creates_no_md(self, notes_dir):
        create_document("No Backup", [{"type": "paragraph", "data": {"text": "x"}}])
        with locked_document("no-backup", 1, author="user") as doc:
            replace_block(doc, doc.blocks[0].id, data={"text": "y"}, author="user")
        assert not (notes_dir / "no-backup.md").exists()

    @pytest.mark.parametrize("entry", ["delete", "files", "rename_old", "rename_new"])
    def test_file_lifecycle_refuses_an_invalid_slug(self, notes_dir, entry):
        create_document("Fine", [{"type": "paragraph", "data": {"text": "x"}}])
        with pytest.raises(InvalidSlugError):
            if entry == "delete":
                delete_document_files("bad.slug")
            elif entry == "files":
                document_files("bad.slug")
            elif entry == "rename_old":
                rename_document_files("bad.slug", "fine")
            else:
                rename_document_files("fine", "bad.slug")

    def test_slug_exists_checks_both_extensions(self, notes_dir):
        create_document("Ext", [{"type": "paragraph", "data": {"text": "x"}}])
        assert slug_exists("ext")
        (notes_dir / "md-only.md").write_text("body", encoding="utf-8")
        assert slug_exists("md-only")
        assert not slug_exists("nothing")

    def test_new_document_does_not_adopt_a_json_slug(self, notes_dir):
        """A title whose slug is taken yields a new slug, never a second document."""
        create_document("Taken", [{"type": "paragraph", "data": {"text": "first"}}])
        second = create_document(
            "Taken", [{"type": "paragraph", "data": {"text": "second"}}]
        )
        assert second.meta.slug == "taken-1"
        assert load_document("taken").blocks[0].data["text"] == "first"

    def test_new_document_does_not_adopt_a_md_slug(self, notes_dir):
        (notes_dir / "occupied.md").write_text("legacy", encoding="utf-8")
        document = create_document(
            "Occupied", [{"type": "paragraph", "data": {"text": "x"}}]
        )
        assert document.meta.slug == "occupied-1"

    def test_list_skips_files_whose_stem_is_not_a_valid_slug(self, notes_dir):
        create_document("Fine", [{"type": "paragraph", "data": {"text": "x"}}])
        (notes_dir / "my.note.md").write_text("dotted", encoding="utf-8")

        slugs = {row["slug"] for row in list_documents()}
        assert "fine" in slugs
        # Skipped, not renamed and not normalised: it is a user's file.
        assert "my.note" not in slugs
        assert (notes_dir / "my.note.md").exists()

    def test_dotted_slug_is_rejected_on_direct_access(self, notes_dir):
        (notes_dir / "my.note.md").write_text("dotted", encoding="utf-8")
        with pytest.raises(InvalidSlugError):
            load_document("my.note")

    def test_list_excludes_marker_files(self, notes_dir):
        """The marker itself, and dotfiles generally, never become notes."""
        set_scratchpad_state("something")
        assert list_documents() == []

        # Positive control: a real note in the same directory IS listed, so the
        # assertion above cannot be satisfied by returning [] unconditionally.
        create_document("Real Note", [{"type": "paragraph", "data": {"text": "x"}}])
        assert {row["slug"] for row in list_documents()} == {"real-note"}

    def test_list_on_missing_directory_is_empty(self, tmp_path, monkeypatch):
        """An absent directory lists nothing, and does not raise.

        ``get_notes_dir`` creates the directory, so the guard is only reachable
        by skipping that helper -- which is what the patch below does.
        """
        import wichy.tools.notes as notes_pkg

        absent = tmp_path / "absent"
        monkeypatch.setattr(notes_pkg, "get_notes_dir", lambda: str(absent))
        assert list_documents() == []
        # Positive control: the same patched helper lists a note once one exists.
        absent.mkdir()
        (absent / "later.json").write_text(
            '{"meta": {"slug": "later", "title": "Later"}, "blocks": []}',
            encoding="utf-8",
        )
        assert {row["slug"] for row in list_documents()} == {"later"}

    def test_create_rejects_empty_title(self, notes_dir):
        with pytest.raises(ValueError):
            create_document("   ")


# ---------------------------------------------------------------------------
# File lifecycle: delete and rename
# ---------------------------------------------------------------------------


class TestDocumentFiles:
    def test_delete_removes_document_backup_and_logs(self, notes_dir):
        create_document("Doomed", [{"type": "paragraph", "data": {"text": "x"}}])
        (notes_dir / "doomed.md").write_text("backup", encoding="utf-8")
        (notes_dir / "doomed.revisions.jsonl").write_text("{}\n", encoding="utf-8")
        (notes_dir / "doomed.revisions.20260101T000000.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )

        removed = delete_document_files("doomed")
        assert len(removed) == 4
        # Nothing for the slug survives, so the note cannot reappear on list.
        assert document_files("doomed") == []
        assert list_documents() == []

    def test_delete_leaves_other_slugs_alone(self, notes_dir):
        create_document("Keep", [{"type": "paragraph", "data": {"text": "k"}}])
        create_document("Drop", [{"type": "paragraph", "data": {"text": "d"}}])
        delete_document_files("drop")
        assert {row["slug"] for row in list_documents()} == {"keep"}

    def test_rename_moves_the_backup_and_the_log(self, notes_dir):
        create_document("Alpha", [{"type": "paragraph", "data": {"text": "x"}}])
        (notes_dir / "alpha.md").write_text("backup", encoding="utf-8")
        (notes_dir / "alpha.revisions.jsonl").write_text("{}\n", encoding="utf-8")

        rename_document_files("alpha", "beta")

        assert not (notes_dir / "alpha.json").exists()
        assert (notes_dir / "beta.json").exists()
        assert (notes_dir / "beta.md").exists()
        assert (notes_dir / "beta.revisions.jsonl").exists()
        assert load_document("beta").blocks[0].data["text"] == "x"

    def test_rename_onto_an_occupied_slug_is_rejected(self, notes_dir):
        create_document("Alpha", [{"type": "paragraph", "data": {"text": "a"}}])
        create_document("Beta", [{"type": "paragraph", "data": {"text": "b"}}])
        with pytest.raises(ValueError):
            rename_document_files("alpha", "beta")
        # The occupant is untouched.
        assert load_document("beta").blocks[0].data["text"] == "b"

    def test_rename_missing_document_is_not_found(self, notes_dir):
        with pytest.raises(DocumentNotFoundError):
            rename_document_files("ghost", "new-ghost")


# ---------------------------------------------------------------------------
# Merge: server-side meta is preserved across an editor save
# ---------------------------------------------------------------------------


class TestMergedBlocks:
    def test_stored_meta_is_carried_across_by_id(self, notes_dir):
        create_document("Merge", [{"type": "paragraph", "data": {"text": "x"}}])
        stored = load_document("merge")
        original = stored.blocks[0]

        merged = merged_blocks(
            stored,
            [{"id": original.id, "type": "paragraph", "data": {"text": "edited"}}],
            author="user",
        )

        assert merged[0].id == original.id
        assert merged[0].data["text"] == "edited"
        # created survives; updated moves.
        assert merged[0].meta.created == original.meta.created
        assert merged[0].meta.updated > original.meta.updated

    def test_a_new_block_is_stamped_with_the_acting_author(self, notes_dir):
        create_document("Stamp", [{"type": "paragraph", "data": {"text": "x"}}])
        stored = load_document("stamp")
        merged = merged_blocks(
            stored,
            [{"type": "todo", "data": {"text": "fresh"}}],
            author="agent",
        )
        assert merged[0].meta.author == "agent"
        assert merged[0].meta.touched_by == ["agent"]

    def test_merge_reorders_by_the_incoming_order(self, notes_dir):
        create_document(
            "Reorder",
            [
                {"type": "paragraph", "data": {"text": "first"}},
                {"type": "paragraph", "data": {"text": "second"}},
            ],
        )
        stored = load_document("reorder")
        first, second = stored.blocks
        merged = merged_blocks(
            stored,
            [
                {"id": second.id, "type": "paragraph", "data": {"text": "second"}},
                {"id": first.id, "type": "paragraph", "data": {"text": "first"}},
            ],
            author="user",
        )
        assert [b.id for b in merged] == [second.id, first.id]

    def test_duplicate_incoming_id_is_rejected(self, notes_dir):
        create_document("Dupe", [{"type": "paragraph", "data": {"text": "x"}}])
        stored = load_document("dupe")
        block_id = stored.blocks[0].id
        with pytest.raises(ValueError):
            merged_blocks(
                stored,
                [
                    {"id": block_id, "type": "paragraph", "data": {"text": "a"}},
                    {"id": block_id, "type": "paragraph", "data": {"text": "b"}},
                ],
                author="user",
            )

    def test_merge_validates_data(self, notes_dir):
        create_document("Valid", [{"type": "paragraph", "data": {"text": "x"}}])
        stored = load_document("valid")
        with pytest.raises(BlockDataError):
            merged_blocks(
                stored,
                [{"type": "header", "data": {"text": "no level"}}],
                author="user",
            )


# ---------------------------------------------------------------------------
# Scratchpad marker
# ---------------------------------------------------------------------------


class TestScratchpadMarker:
    def test_absent_marker_reads_as_unpinned(self, notes_dir):
        assert get_scratchpad_state() == {"primary": None, "pinned": []}
        assert get_scratchpad_slug() is None

    def test_round_trips_the_new_format(self, notes_dir):
        set_scratchpad_state("doc-one")
        assert get_scratchpad_slug() == "doc-one"
        assert get_scratchpad_state()["primary"] == "doc-one"

    def test_reads_a_legacy_slug_marker(self, notes_dir):
        (notes_dir / ".scratchpad").write_text(
            json.dumps({"slug": "legacy-doc"}), encoding="utf-8"
        )
        assert get_scratchpad_slug() == "legacy-doc"
        assert get_scratchpad_state() == {"primary": "legacy-doc", "pinned": []}

    def test_legacy_marker_with_null_slug_is_unpinned(self, notes_dir):
        (notes_dir / ".scratchpad").write_text(
            json.dumps({"slug": None}), encoding="utf-8"
        )
        assert get_scratchpad_slug() is None

    def test_writes_the_new_format(self, notes_dir):
        set_scratchpad_state("written")
        raw = json.loads((notes_dir / ".scratchpad").read_text(encoding="utf-8"))
        assert raw == {"primary": "written", "pinned": ["written"]}

    def test_clearing_writes_nulls_rather_than_unlinking(self, notes_dir):
        set_scratchpad_state("doc")
        set_scratchpad_slug(None)

        marker = notes_dir / ".scratchpad"
        assert marker.exists()
        assert json.loads(marker.read_text(encoding="utf-8")) == {
            "primary": None,
            "pinned": [],
        }

    def test_set_scratchpad_slug_resets_the_pinned_list(self, notes_dir):
        """One scratchpad: setting a primary cannot leave another slug pinned."""
        set_scratchpad_state("a", ["a", "b"])
        set_scratchpad_slug("c")
        assert get_scratchpad_state() == {"primary": "c", "pinned": ["c"]}

    def test_set_scratchpad_state_keeps_an_explicit_pinned_list(self, notes_dir):
        """The pair-wise entry point is what the UI's pinned list uses."""
        set_scratchpad_state("a", ["a", "b"])
        assert get_scratchpad_state() == {"primary": "a", "pinned": ["a", "b"]}

    def test_pinned_list_is_de_duplicated_and_includes_primary(self, notes_dir):
        set_scratchpad_state("a", ["a", "a", "b"])
        state = get_scratchpad_state()
        assert state["pinned"] == ["a", "b"]

    def test_corrupt_marker_reads_as_unpinned(self, notes_dir):
        (notes_dir / ".scratchpad").write_text("{not json", encoding="utf-8")
        assert get_scratchpad_state() == {"primary": None, "pinned": []}

    def test_primary_is_the_only_field_that_selects_the_scratchpad(self, notes_dir):
        """A marker listing several pinned docs still names exactly one primary."""
        (notes_dir / ".scratchpad").write_text(
            json.dumps({"primary": "the-one", "pinned": ["the-one", "another"]}),
            encoding="utf-8",
        )
        state = get_scratchpad_state()
        assert get_scratchpad_slug() == "the-one"
        assert state["pinned"] == ["the-one", "another"]


# ---------------------------------------------------------------------------
# Model envelope
# ---------------------------------------------------------------------------


class TestDocumentModel:
    def test_block_index_and_lookup(self):
        document = BlockDocument()
        assert document.block_index("nope") == -1
        assert document.get_block("nope") is None
        assert not document.has_block("nope")

        # Positive control: a lookup that always answers -1 would pass above.
        from wichy.tools.notes.models import Block

        document.blocks.append(Block(id="blk-x", type="delimiter", data={}))
        assert document.block_index("blk-x") == 0
        assert document.get_block("blk-x").id == "blk-x"
        assert document.has_block("blk-x")

    def test_extra_top_level_keys_are_rejected(self):
        from wichy.tools.notes.models import Block, BlockMeta

        with pytest.raises(ValueError):
            BlockDocument.model_validate({"meta": {}, "blocks": [], "extra": 1})
        with pytest.raises(ValueError):
            Block.model_validate(
                {"id": "b", "type": "delimiter", "data": {}, "extra": 1}
            )
        with pytest.raises(ValueError):
            BlockMeta.model_validate({"extra": 1})

    def test_unreadable_stored_json_is_reported(self, notes_dir):
        (notes_dir / "broken.json").write_text("{not json", encoding="utf-8")
        from wichy.tools.notes.blocks import InvalidDocumentError

        with pytest.raises(InvalidDocumentError):
            load_document("broken")

    def test_new_block_id_is_prefixed_and_unique(self):
        ids = {new_block_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(i.startswith("blk-") for i in ids)


# ---------------------------------------------------------------------------
# Defects found in review: each of these fails against the code as first written
# ---------------------------------------------------------------------------


class TestSlugWhitespaceIsRejected:
    @pytest.mark.parametrize("slug", ["note\n", "note\t", "\nnote", "note\r"])
    def test_trailing_and_leading_whitespace_is_invalid(self, slug):
        """A regex anchored with $ accepts a trailing newline; \\Z does not.

        A slug with a newline in it would reach the filesystem as a path with a
        newline, and the same hole is reachable from a URL segment.
        """
        assert not is_valid_slug(slug)

    def test_whitespace_slug_is_refused_at_the_document_entry_points(self, notes_dir):
        with pytest.raises(InvalidSlugError):
            load_document("note\n")
        with pytest.raises(InvalidSlugError):
            delete_document_files("note\n")
        with pytest.raises(InvalidSlugError):
            document_files("note\n")
        with pytest.raises(InvalidSlugError):
            rename_document_files("note\n", "ok")

    def test_path_helpers_refuse_an_invalid_slug(self, notes_dir):
        from wichy.tools.notes.blocks import (
            document_path,
            legacy_path,
            revisions_path,
            slug_exists,
        )

        for call in (
            lambda: document_path("bad.slug"),
            lambda: legacy_path("bad.slug"),
            lambda: revisions_path("bad.slug"),
            lambda: slug_exists("bad.slug"),
            lambda: resolve_format("bad.slug"),
        ):
            with pytest.raises(InvalidSlugError):
                call()


class TestNonMappingDataIsRejectedCleanly:
    """A malformed payload must be a 400, not a crash that a route turns into 500."""

    @pytest.mark.parametrize("data", [5, "text", ["a", "b"], 3.5])
    def test_non_mapping_data_raises_block_data_error(self, data):
        with pytest.raises(BlockDataError) as err:
            validate_block_data("paragraph", data)
        assert "object" in str(err.value)


class TestCreateIsAtomicUnderConcurrency:
    def test_concurrent_creates_of_one_title_do_not_collide(self, notes_dir):
        """Two creates must not both claim the same slug.

        Uniqueness is resolved before the lock; the re-check inside the lock is
        what stops the second write from silently overwriting the first.
        """
        results: list[str] = []
        barrier = threading.Barrier(4, timeout=5)

        def create(index: int) -> None:
            barrier.wait()
            results.append(
                create_document(
                    f"Same Title {index}" if index else "Same Title"
                ).meta.slug
            )
            results.append(create_document("Colliding Title").meta.slug)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        for thread in threads:
            assert not thread.is_alive()

        # Every create reported a slug, and no two reported the same one.
        assert len(results) == 8
        assert len(set(results)) == 8
        # And each of those slugs really is a separate document on disk.
        assert {row["slug"] for row in list_documents()} == set(results)


class TestDeleteAndRenameTakeTheLock:
    def test_delete_clears_the_cached_version(self, notes_dir):
        """A deleted document must not keep a version a stale-write check could use."""
        from wichy.tools.notes.blocks import cached_version
        from wichy.tools.notes.state import clear_doc_version

        create_document("Vanishing", [{"type": "paragraph", "data": {"text": "x"}}])
        assert cached_version("vanishing") == 1
        clear_doc_version("vanishing")  # reset the cache to a known state
        create_document("Vanishing Two", [{"type": "paragraph", "data": {"text": "x"}}])
        assert cached_version("vanishing-two") == 1

        delete_document_files("vanishing-two")
        assert cached_version("vanishing-two") == 0

    def test_delete_reports_a_file_it_cannot_remove(self, notes_dir):
        """A partial delete must not report success: the note would reappear."""
        from wichy.tools.notes.blocks import DocumentDeletionError

        create_document("Stubborn", [{"type": "paragraph", "data": {"text": "x"}}])
        # A non-empty directory cannot be unlinked, so the removal fails.
        stubborn = notes_dir / "stubborn.md"
        stubborn.mkdir()
        (stubborn / "child").write_text("x", encoding="utf-8")

        with pytest.raises(DocumentDeletionError):
            delete_document_files("stubborn")

    def test_rename_moves_queued_agent_changes_and_version(self, notes_dir):
        """Renaming the pinned document must not lose queued ops or its version."""
        from wichy.tools.notes.blocks import cached_version
        from wichy.tools.notes.state import peek_agent_changes, queue_agent_change

        create_document("Moving", [{"type": "paragraph", "data": {"text": "x"}}])
        queue_agent_change("moving", {"op": "add", "author": "agent"})

        rename_document_files("moving", "moved")

        assert peek_agent_changes("moving") == []
        assert len(peek_agent_changes("moved")) == 1
        assert cached_version("moved") == 1
        assert cached_version("moving") == 0

    def test_rename_moves_a_rotated_log(self, notes_dir):
        create_document("Rot", [{"type": "paragraph", "data": {"text": "x"}}])
        rotated = notes_dir / "rot.revisions.20260101T000000.jsonl"
        rotated.write_text("{}\n", encoding="utf-8")

        rename_document_files("rot", "rotted")

        assert not rotated.exists()
        assert (notes_dir / "rotted.revisions.20260101T000000.jsonl").exists()


class TestNestedLockIsRefused:
    def test_nesting_one_document_is_an_error_not_a_silent_loser(self, notes_dir):
        """The lock is re-entrant, so a nested body would be silently clobbered."""
        create_document("Nest", [{"type": "paragraph", "data": {"text": "x"}}])
        with pytest.raises(ValueError) as err:
            with locked_document("nest", 1, author="user"):
                with locked_document("nest", 1, author="user"):
                    pass
        assert "already open" in str(err.value)

    def test_the_guard_is_released_after_an_error(self, notes_dir):
        """A failed body must not leave the slug permanently 'open'."""
        create_document("Release", [{"type": "paragraph", "data": {"text": "x"}}])
        with pytest.raises(ValueError):
            with locked_document("release", 1, author="user"):
                raise ValueError("boom")

        # Usable again afterwards.
        with locked_document("release", 1, author="user") as doc:
            replace_block(doc, doc.blocks[0].id, data={"text": "y"}, author="user")
        assert load_document("release").meta.version == 2

    def test_two_threads_may_each_hold_their_own_body(self, notes_dir):
        """The guard is thread-local; the lock still serialises across threads."""
        create_document("Threads", [{"type": "paragraph", "data": {"text": "x"}}])
        seen: list[int] = []
        errors: list[str] = []

        def run(version: int) -> None:
            try:
                with locked_document("threads", version, author="user") as doc:
                    seen.append(version)
                    replace_block(
                        doc,
                        doc.blocks[0].id,
                        data={"text": str(version)},
                        author="user",
                    )
            except StaleVersionError:
                errors.append(f"stale-{version}")

        threads = [threading.Thread(target=run, args=(1,)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        for thread in threads:
            assert not thread.is_alive()
        # One writer won; the other was told it was stale rather than losing
        # its write silently.
        assert len(seen) == 1
        assert len(errors) == 1


class TestSaveRefusesInvalidStoredData:
    def test_a_document_with_invalid_block_data_cannot_be_re_saved(self, notes_dir):
        """Persisting must not launder data that is already invalid on disk.

        A hand-edited or otherwise malformed document can be read back; a later
        edit to a different block must not write the invalid data out again.
        """
        from wichy.tools.notes.blocks import save_document
        from wichy.tools.notes.models import BlockDocument

        malformed = BlockDocument.model_validate(
            {
                "meta": {"slug": "malformed", "title": "Malformed"},
                "blocks": [
                    {"id": "blk-bad1", "type": "header", "data": {"text": "no level"}}
                ],
            }
        )
        with pytest.raises(BlockDataError):
            save_document(malformed)
        # Nothing was written.
        assert not (notes_dir / "malformed.json").exists()

    def test_a_valid_document_saves(self, notes_dir):
        """The positive control: the guard above must not reject good data."""
        from wichy.tools.notes.blocks import save_document
        from wichy.tools.notes.models import BlockDocument

        good = BlockDocument.model_validate(
            {
                "meta": {"slug": "gooddoc", "title": "Good"},
                "blocks": [
                    {
                        "id": "blk-good1",
                        "type": "header",
                        "data": {"text": "t", "level": 3},
                    }
                ],
            }
        )
        save_document(good)
        assert load_document("gooddoc").blocks[0].data["level"] == 3


class TestStoredMetaIsNotInheritedByNewBlocks:
    def test_generated_id_skips_ids_already_on_disk(self, notes_dir):
        """A new block must not adopt a stored block's meta via id collision."""
        create_document("No Inherit", [{"type": "paragraph", "data": {"text": "x"}}])
        stored = load_document("no-inherit")
        stored_id = stored.blocks[0].id

        # Force the generator to hand out the stored id first, then a fresh one.
        handed = [stored_id, "blk-fresh000"]
        with patch(
            "wichy.tools.notes.blocks.new_block_id", side_effect=lambda: handed.pop(0)
        ):
            merged = merged_blocks(
                stored, [{"type": "todo", "data": {"text": "new"}}], author="agent"
            )

        assert merged[0].id != stored_id
        assert merged[0].meta.touched_by == ["agent"]
        assert merged[0].meta.created != stored.blocks[0].meta.created
