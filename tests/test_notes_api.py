"""Tests for the notes HTTP API.

Driven through a Flask test client, as the existing blueprint tests are: build a
bare app, register the routes, and point the notes directory at a temporary path.

Grouped by what each group defends:

- documents: create, read, list, update, delete, and the error cases
- slug validity: every route that takes a slug refuses an invalid one
- the markdown bridge: a legacy ``.md`` is readable, refuses every write, and
  refuses conversion twice
- blocks: the five block routes, version checks, and unknown ids
- one request is one transaction: a request touching several blocks bumps the
  version once and appends one revision
- revisions: listing, get-by-id, revert
- export: both formats, read-only, download header
- pinning: which document the agent tools would edit
"""

from __future__ import annotations

import pytest
from flask import Blueprint, Flask

from wichy.config import settings
from wichy.tools.notes import api
from wichy.tools.notes.blocks import MARKDOWN_WRITE_REFUSED, revisions_path
from wichy.tools.notes.state import reset_state

PREFIX = "/tools/notes"


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    """Point the notes directory at a temporary path and reset shared state."""
    target = tmp_path / "notes"
    monkeypatch.setattr(settings, "notes_dir_name", str(target))
    target.mkdir(parents=True, exist_ok=True)
    reset_state()
    yield target
    reset_state()


@pytest.fixture
def client(notes_dir):
    """A Flask test client with the notes routes registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("notes", __name__, url_prefix=PREFIX)
    api.register_routes(bp)
    app.register_blueprint(bp)
    with app.test_client() as client:
        yield client


def create(client, title="My Note", content="", **extra):
    """Create a document and return the response body."""
    payload = {"title": title}
    if content:
        payload["content"] = content
    payload.update(extra)
    response = client.post(f"{PREFIX}/api/notes", json=payload)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def make_legacy(
    notes_dir, slug="legacy", body="# Heading\n\nsome text\n", title="Legacy"
):
    """Write a legacy markdown note the way the old API did."""
    (notes_dir / f"{slug}.md").write_text(
        f"---\ntitle: {title}\ncreated: 2026-01-01T00:00:00+00:00\n"
        f"updated: 2026-01-02T00:00:00+00:00\n---\n{body}",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class TestCreateAndRead:
    def test_create_returns_the_document(self, client):
        body = create(client, "My Note", "# H\n\ntext")
        assert body["slug"] == "my-note"
        assert body["title"] == "My Note"
        assert body["version"] == 1
        assert body["format"] == "editorjs"

    def test_create_converts_initial_markdown(self, client):
        create(client, "Converted", "# Heading\n\n- a\n- b\n")
        blocks = client.get(f"{PREFIX}/api/notes/converted").get_json()["blocks"]
        assert [b["type"] for b in blocks] == ["header", "list"]

    def test_create_accepts_explicit_blocks(self, client):
        create(
            client,
            "With Blocks",
            blocks=[{"type": "todo", "data": {"text": "do it"}}],
        )
        blocks = client.get(f"{PREFIX}/api/notes/with-blocks").get_json()["blocks"]
        assert blocks[0]["type"] == "todo"

    def test_create_without_a_title_is_rejected(self, client):
        response = client.post(f"{PREFIX}/api/notes", json={"title": "  "})
        assert response.status_code == 400
        assert "title" in response.get_json()["error"].lower()

    def test_create_with_invalid_block_data_is_rejected(self, client):
        response = client.post(
            f"{PREFIX}/api/notes",
            json={
                "title": "Bad",
                "blocks": [{"type": "header", "data": {"text": "x"}}],
            },
        )
        assert response.status_code == 400

    def test_create_avoids_taking_an_existing_slug(self, client):
        create(client, "Same")
        second = create(client, "Same")
        assert second["slug"] == "same-1"

    def test_get_returns_meta_blocks_and_format(self, client):
        create(client, "Shape", "text")
        body = client.get(f"{PREFIX}/api/notes/shape").get_json()
        assert set(body) == {"meta", "blocks", "format"}
        assert body["meta"]["slug"] == "shape"
        assert body["format"] == "editorjs"

    def test_get_unknown_slug_is_404(self, client):
        assert client.get(f"{PREFIX}/api/notes/nope").status_code == 404

    def test_list_reports_one_row_per_slug(self, client, notes_dir):
        create(client, "One")
        make_legacy(notes_dir, "one", title="One")
        rows = [
            r
            for r in client.get(f"{PREFIX}/api/notes").get_json()["notes"]
            if r["slug"] == "one"
        ]
        assert len(rows) == 1
        assert rows[0]["format"] == "editorjs"

    def test_list_includes_a_markdown_note_with_its_format(self, client, notes_dir):
        make_legacy(notes_dir)
        rows = client.get(f"{PREFIX}/api/notes").get_json()["notes"]
        assert [r["format"] for r in rows if r["slug"] == "legacy"] == ["markdown"]

    def test_list_is_empty_at_start(self, client):
        assert client.get(f"{PREFIX}/api/notes").get_json()["notes"] == []


class TestUpdate:
    def test_put_replaces_the_blocks(self, client):
        body = create(client, "Updatable", "a\n\nb")
        blocks = client.get(f"{PREFIX}/api/notes/updatable").get_json()["blocks"]
        blocks[0]["data"]["text"] = "changed"
        response = client.put(
            f"{PREFIX}/api/notes/updatable",
            json={"version": body["version"], "blocks": blocks},
        )
        assert response.status_code == 200
        assert response.get_json()["version"] == 2
        # Read back: a route that bumped the version and discarded the payload
        # would satisfy the assertions above.
        after = client.get(f"{PREFIX}/api/notes/updatable").get_json()
        assert after["blocks"][0]["data"]["text"] == "changed"

    def test_put_requires_a_version(self, client):
        create(client, "NoVer", "x")
        response = client.put(f"{PREFIX}/api/notes/nover", json={"blocks": []})
        assert response.status_code == 400

    def test_put_with_a_stale_version_is_409(self, client):
        create(client, "Stale", "x")
        response = client.put(
            f"{PREFIX}/api/notes/stale", json={"version": 99, "blocks": []}
        )
        assert response.status_code == 409

    def test_put_with_a_stale_version_changes_nothing(self, client):
        body = create(client, "Intact", "original")
        client.put(f"{PREFIX}/api/notes/intact", json={"version": 99, "blocks": []})
        after = client.get(f"{PREFIX}/api/notes/intact").get_json()
        assert after["meta"]["version"] == body["version"]
        assert after["blocks"][0]["data"]["text"] == "original"

    def test_put_renames_and_moves_the_document(self, client, notes_dir):
        body = create(client, "Old Name", "x")
        response = client.put(
            f"{PREFIX}/api/notes/old-name",
            json={"version": body["version"], "meta": {"title": "New Name"}},
        )
        assert response.status_code == 200
        assert response.get_json()["slug"] == "new-name"
        # The old slug is gone and the new one serves the document.
        assert not (notes_dir / "old-name.json").exists()
        assert client.get(f"{PREFIX}/api/notes/new-name").status_code == 200

    def test_put_rename_moves_the_markdown_backup(self, client, notes_dir):
        make_legacy(notes_dir, "kept", title="Kept")
        client.post(f"{PREFIX}/api/notes/kept/convert")
        assert (notes_dir / "kept.md").exists()
        body = client.get(f"{PREFIX}/api/notes/kept").get_json()
        client.put(
            f"{PREFIX}/api/notes/kept",
            json={"version": body["meta"]["version"], "meta": {"title": "Moved"}},
        )
        assert not (notes_dir / "kept.md").exists()
        assert (notes_dir / "moved.md").exists()

    def test_put_rename_repoints_the_marker(self, client, notes_dir, monkeypatch):
        """Renaming the pinned document must not leave the pin dangling."""
        create(client, "Pinned Doc", "x")
        client.post(f"{PREFIX}/api/notes/pinned-doc/pin", json={"pinned": True})
        body = client.get(f"{PREFIX}/api/notes/pinned-doc").get_json()

        client.put(
            f"{PREFIX}/api/notes/pinned-doc",
            json={"version": body["meta"]["version"], "meta": {"title": "Renamed Doc"}},
        )

        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["primary"]
            == "renamed-doc"
        )

    def test_one_put_that_renames_and_edits_is_one_revision(self, client):
        """A title change and a block change in one request is one transaction."""
        body = create(client, "Both", "a\n\nb")
        blocks = client.get(f"{PREFIX}/api/notes/both").get_json()["blocks"]
        blocks[0]["data"]["text"] = "changed"

        response = client.put(
            f"{PREFIX}/api/notes/both",
            json={
                "version": body["version"],
                "meta": {"title": "Both Renamed"},
                "blocks": blocks,
            },
        )
        assert response.status_code == 200
        assert response.get_json()["version"] == 2

        # Both halves of the request landed, and they landed together.
        after = client.get(f"{PREFIX}/api/notes/both-renamed").get_json()
        assert after["meta"]["title"] == "Both Renamed"
        assert after["blocks"][0]["data"]["text"] == "changed"

        # Baseline + exactly ONE entry, and that entry records the block edit.
        revisions = client.get(f"{PREFIX}/api/notes/both-renamed/revisions").get_json()
        assert revisions["total"] == 2
        ops = revisions["revisions"][0]["ops"]
        assert [op["op"] for op in ops] == ["update"]


class TestDelete:
    def test_delete_removes_the_document(self, client, notes_dir):
        create(client, "Doomed", "x")
        assert client.delete(f"{PREFIX}/api/notes/doomed").status_code == 200
        assert client.get(f"{PREFIX}/api/notes/doomed").status_code == 404
        assert not (notes_dir / "doomed.json").exists()

    def test_delete_removes_the_backup_and_revision_log(self, client, notes_dir):
        make_legacy(notes_dir, "gone", title="Gone")
        client.post(f"{PREFIX}/api/notes/gone/convert")
        assert revisions_path("gone").exists()

        client.delete(f"{PREFIX}/api/notes/gone")

        assert not (notes_dir / "gone.json").exists()
        assert not (notes_dir / "gone.md").exists()
        assert not revisions_path("gone").exists()

    def test_delete_clears_the_marker(self, client):
        create(client, "Pinned", "x")
        client.post(f"{PREFIX}/api/notes/pinned/pin", json={"pinned": True})
        client.delete(f"{PREFIX}/api/notes/pinned")
        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["primary"] is None
        )

    def test_delete_unknown_is_404(self, client):
        assert client.delete(f"{PREFIX}/api/notes/nothing").status_code == 404

    def test_a_deleted_note_does_not_reappear_in_the_list(self, client, notes_dir):
        make_legacy(notes_dir, "resurrect", title="R")
        client.post(f"{PREFIX}/api/notes/resurrect/convert")
        client.delete(f"{PREFIX}/api/notes/resurrect")
        slugs = {
            row["slug"] for row in client.get(f"{PREFIX}/api/notes").get_json()["notes"]
        }
        assert "resurrect" not in slugs


# ---------------------------------------------------------------------------
# Slug validity
# ---------------------------------------------------------------------------


MUTATING_ROUTES = [
    ("get", "/api/notes/{slug}", None),
    ("put", "/api/notes/{slug}", {"version": 1, "blocks": []}),
    ("delete", "/api/notes/{slug}", None),
    ("post", "/api/notes/{slug}/convert", None),
    ("get", "/api/notes/{slug}/conversion-preview", None),
    ("get", "/api/notes/{slug}/export", None),
    ("post", "/api/notes/{slug}/pin", {"pinned": True}),
    ("get", "/api/notes/{slug}/blocks", None),
    (
        "post",
        "/api/notes/{slug}/blocks",
        {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
    ),
    (
        "patch",
        "/api/notes/{slug}/blocks/blk-1",
        {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
    ),
    ("delete", "/api/notes/{slug}/blocks/blk-1?version=1", None),
    ("post", "/api/notes/{slug}/blocks/blk-1/move", {"version": 1}),
    ("get", "/api/notes/{slug}/revisions", None),
    ("get", "/api/notes/{slug}/revisions/1", None),
    ("post", "/api/notes/{slug}/revisions/1/revert", {"version": 1}),
]


class TestSlugValidity:
    @pytest.mark.parametrize("method,path,payload", MUTATING_ROUTES)
    def test_every_route_refuses_a_dotted_slug(self, client, method, path, payload):
        """A slug is validated at the route boundary, not inferred from disk."""
        response = getattr(client, method)(
            f"{PREFIX}{path.format(slug='bad.slug')}", json=payload
        )
        assert (
            response.status_code == 400
        ), f"{method.upper()} {path} returned {response.status_code}"

    @pytest.mark.parametrize("method,path,payload", MUTATING_ROUTES)
    def test_every_route_refuses_a_space_in_a_slug(self, client, method, path, payload):
        response = getattr(client, method)(
            f"{PREFIX}{path.format(slug='bad slug')}", json=payload
        )
        assert response.status_code == 400

    def test_a_dotted_file_on_disk_is_skipped_by_list(self, client, notes_dir):
        (notes_dir / "my.note.md").write_text("body", encoding="utf-8")
        slugs = {
            row["slug"] for row in client.get(f"{PREFIX}/api/notes").get_json()["notes"]
        }
        assert "my.note" not in slugs
        # And it is not served directly either.
        assert client.get(f"{PREFIX}/api/notes/my.note").status_code == 400

    def test_a_dotted_file_is_not_deleted_by_a_refused_slug(self, client, notes_dir):
        (notes_dir / "my.note.md").write_text("body", encoding="utf-8")
        client.delete(f"{PREFIX}/api/notes/my.note")
        # Refused, not normalised and not removed: it is the user's file.
        assert (notes_dir / "my.note.md").exists()


# ---------------------------------------------------------------------------
# The legacy markdown bridge
# ---------------------------------------------------------------------------


class TestMarkdownBridge:
    def test_a_md_note_is_served_as_one_synthetic_block(self, client, notes_dir):
        make_legacy(notes_dir)
        body = client.get(f"{PREFIX}/api/notes/legacy").get_json()
        assert body["format"] == "markdown"
        assert len(body["blocks"]) == 1
        assert body["blocks"][0]["type"] == "paragraph"
        assert "# Heading" in body["blocks"][0]["data"]["text"]

    def test_a_md_note_reports_version_one(self, client, notes_dir):
        make_legacy(notes_dir)
        assert (
            client.get(f"{PREFIX}/api/notes/legacy").get_json()["meta"]["version"] == 1
        )

    # Routes that write BLOCKS. Delete, convert and pin are deliberately absent:
    # they are not block writes, and each is allowed on a markdown note (deleting
    # it, converting it, and pinning it so the tools can report the refusal).
    BLOCK_WRITE_ROUTES = [
        (
            "put",
            "/api/notes/{slug}",
            {"version": 1, "blocks": [{"type": "paragraph", "data": {"text": "x"}}]},
        ),
        (
            "post",
            "/api/notes/{slug}/blocks",
            {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
        ),
        (
            "patch",
            "/api/notes/{slug}/blocks/blk-1",
            {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
        ),
        ("delete", "/api/notes/{slug}/blocks/blk-1?version=1", None),
        ("post", "/api/notes/{slug}/blocks/blk-1/move", {"version": 1}),
        ("post", "/api/notes/{slug}/revisions/1/revert", {"version": 1}),
    ]

    @pytest.mark.parametrize("method,path,payload", BLOCK_WRITE_ROUTES)
    def test_every_block_write_refuses_a_markdown_document(
        self, client, notes_dir, method, path, payload
    ):
        """A block write on a `.md` would give one slug two live documents."""
        make_legacy(notes_dir)
        response = getattr(client, method)(
            f"{PREFIX}{path.format(slug='legacy')}", json=payload
        )
        assert (
            response.status_code == 409
        ), f"{method.upper()} {path} returned {response.status_code}"
        assert response.get_json()["error"] == MARKDOWN_WRITE_REFUSED

    def test_revert_on_a_markdown_note_refuses_by_format_not_by_missing_revision(
        self, client, notes_dir
    ):
        """A markdown note keeps no revision log, so 404 would hide the real reason."""
        make_legacy(notes_dir)
        response = client.post(
            f"{PREFIX}/api/notes/legacy/revisions/1/revert", json={"version": 1}
        )
        assert response.status_code == 409
        assert response.get_json()["error"] == MARKDOWN_WRITE_REFUSED

    def test_delete_is_allowed_on_a_markdown_note(self, client, notes_dir):
        make_legacy(notes_dir)
        assert client.delete(f"{PREFIX}/api/notes/legacy").status_code == 200

    def test_pinning_a_markdown_note_is_allowed(self, client, notes_dir):
        make_legacy(notes_dir)
        assert (
            client.post(
                f"{PREFIX}/api/notes/legacy/pin", json={"pinned": True}
            ).status_code
            == 200
        )

    def test_a_markdown_write_never_creates_a_json(self, client, notes_dir):
        make_legacy(notes_dir)
        client.put(
            f"{PREFIX}/api/notes/legacy",
            json={
                "version": 1,
                "blocks": [{"type": "paragraph", "data": {"text": "x"}}],
            },
        )
        assert not (notes_dir / "legacy.json").exists()

    def test_convert_produces_a_json_and_keeps_the_backup(self, client, notes_dir):
        make_legacy(notes_dir)
        response = client.post(f"{PREFIX}/api/notes/legacy/convert")
        assert response.status_code == 200
        body = response.get_json()
        assert body["slug"] == "legacy"
        assert body["format"] == "editorjs"
        assert body["converted_blocks"] == 2
        assert (notes_dir / "legacy.json").exists()
        assert (notes_dir / "legacy.md").exists()

    def test_convert_then_read_returns_the_blocks(self, client, notes_dir):
        make_legacy(notes_dir)
        client.post(f"{PREFIX}/api/notes/legacy/convert")
        body = client.get(f"{PREFIX}/api/notes/legacy").get_json()
        assert body["format"] == "editorjs"
        assert [b["type"] for b in body["blocks"]] == ["header", "paragraph"]

    def test_convert_keeps_the_slug(self, client, notes_dir):
        """The converted note is the same document, not a new one."""
        make_legacy(notes_dir)
        client.post(f"{PREFIX}/api/notes/legacy/convert")
        assert client.get(f"{PREFIX}/api/notes/legacy").status_code == 200
        assert client.get(f"{PREFIX}/api/notes/legacy-1").status_code == 404

    def test_converting_twice_is_409(self, client, notes_dir):
        make_legacy(notes_dir)
        assert client.post(f"{PREFIX}/api/notes/legacy/convert").status_code == 200
        response = client.post(f"{PREFIX}/api/notes/legacy/convert")
        assert response.status_code == 409

    def test_convert_unknown_slug_is_404(self, client):
        assert client.post(f"{PREFIX}/api/notes/nope/convert").status_code == 404

    def test_the_backup_goes_inert_after_conversion(self, client, notes_dir):
        make_legacy(notes_dir)
        client.post(f"{PREFIX}/api/notes/legacy/convert")
        rows = [
            r
            for r in client.get(f"{PREFIX}/api/notes").get_json()["notes"]
            if r["slug"] == "legacy"
        ]
        assert len(rows) == 1
        assert rows[0]["format"] == "editorjs"

    def test_preview_reports_lossy_features(self, client, notes_dir):
        make_legacy(notes_dir, "lossy", body="| a | b |\n|---|---|\n| 1 | 2 |\n")
        body = client.get(f"{PREFIX}/api/notes/lossy/conversion-preview").get_json()
        assert "tables" in body["lossy_features"]
        assert body["blocks_estimate"] >= 1

    def test_preview_on_a_converted_note_reports_its_own_state(self, client, notes_dir):
        """It must not report the stale backup and offer a conversion that cannot run."""
        make_legacy(notes_dir, "done", body="| a |\n|---|\n")
        client.post(f"{PREFIX}/api/notes/done/convert")
        body = client.get(f"{PREFIX}/api/notes/done/conversion-preview").get_json()
        assert body["lossy_features"] == []

    def test_preview_unknown_is_404(self, client):
        assert (
            client.get(f"{PREFIX}/api/notes/nope/conversion-preview").status_code == 404
        )


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


class TestBlocks:
    def test_read_all_blocks(self, client):
        create(client, "Blk", "a\n\nb")
        body = client.get(f"{PREFIX}/api/notes/blk/blocks").get_json()
        assert len(body["blocks"]) == 2
        assert body["version"] == 1

    def test_filter_by_type(self, client):
        create(client, "Filt", "# H\n\ntext\n\n# H2")
        body = client.get(f"{PREFIX}/api/notes/filt/blocks?type=header").get_json()
        assert [b["data"]["text"] for b in body["blocks"]] == ["H", "H2"]

    def test_filter_by_range_is_inclusive(self, client):
        """start and end both name blocks to include."""
        create(client, "Range", "a\n\nb\n\nc")
        body = client.get(f"{PREFIX}/api/notes/range/blocks?start=1&end=2").get_json()
        assert len(body["blocks"]) == 2

    def test_a_single_index_selects_one_block(self, client):
        create(client, "OneIdx", "a\n\nb\n\nc")
        body = client.get(f"{PREFIX}/api/notes/oneidx/blocks?start=1&end=1").get_json()
        assert len(body["blocks"]) == 1

    def test_an_invalid_range_is_400(self, client):
        create(client, "BadRange", "a")
        assert (
            client.get(f"{PREFIX}/api/notes/badrange/blocks?start=5&end=1").status_code
            == 400
        )

    def test_patch_replaces_a_block_keeping_its_id(self, client):
        create(client, "Patch", "original")
        block = client.get(f"{PREFIX}/api/notes/patch/blocks").get_json()["blocks"][0]
        response = client.patch(
            f"{PREFIX}/api/notes/patch/blocks/{block['id']}",
            json={"version": 1, "block_type": "paragraph", "data": {"text": "edited"}},
        )
        assert response.status_code == 200
        updated = response.get_json()["block"]
        assert updated["id"] == block["id"]
        assert updated["data"]["text"] == "edited"
        assert response.get_json()["version"] == 2

    def test_patch_unknown_block_is_404(self, client):
        create(client, "NoBlk", "x")
        response = client.patch(
            f"{PREFIX}/api/notes/noblk/blocks/blk-nope",
            json={"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
        )
        assert response.status_code == 404

    def test_patch_with_invalid_data_is_400(self, client):
        create(client, "BadData", "x")
        block = client.get(f"{PREFIX}/api/notes/baddata/blocks").get_json()["blocks"][0]
        response = client.patch(
            f"{PREFIX}/api/notes/baddata/blocks/{block['id']}",
            json={"version": 1, "block_type": "header", "data": {"text": "no level"}},
        )
        assert response.status_code == 400

    def test_patch_requires_version_and_type(self, client):
        create(client, "Missing", "x")
        block = client.get(f"{PREFIX}/api/notes/missing/blocks").get_json()["blocks"][0]
        route = f"{PREFIX}/api/notes/missing/blocks/{block['id']}"
        assert (
            client.patch(
                route, json={"block_type": "paragraph", "data": {}}
            ).status_code
            == 400
        )
        assert client.patch(route, json={"version": 1, "data": {}}).status_code == 400
        assert (
            client.patch(
                route, json={"version": 1, "block_type": "paragraph"}
            ).status_code
            == 400
        )

    def test_add_block_at_the_end(self, client):
        create(client, "AddEnd", "a")
        response = client.post(
            f"{PREFIX}/api/notes/addend/blocks",
            json={"version": 1, "block_type": "todo", "data": {"text": "new"}},
        )
        assert response.status_code == 201
        assert response.get_json()["block"]["type"] == "todo"
        blocks = client.get(f"{PREFIX}/api/notes/addend/blocks").get_json()["blocks"]
        assert [b["type"] for b in blocks] == ["paragraph", "todo"]

    def test_add_block_after_an_anchor(self, client):
        create(client, "AddMid", "a\n\nb")
        blocks = client.get(f"{PREFIX}/api/notes/addmid/blocks").get_json()["blocks"]
        response = client.post(
            f"{PREFIX}/api/notes/addmid/blocks",
            json={
                "version": 1,
                "block_type": "todo",
                "data": {"text": "mid"},
                "after_block_id": blocks[0]["id"],
            },
        )
        assert response.status_code == 201
        after = client.get(f"{PREFIX}/api/notes/addmid/blocks").get_json()["blocks"]
        assert [b["type"] for b in after] == ["paragraph", "todo", "paragraph"]

    def test_add_block_is_201(self, client):
        create(client, "Code201", "x")
        response = client.post(
            f"{PREFIX}/api/notes/code201/blocks",
            json={"version": 1, "block_type": "delimiter", "data": {}},
        )
        assert response.status_code == 201

    def test_delete_a_block(self, client):
        create(client, "Del", "a\n\nb")
        blocks = client.get(f"{PREFIX}/api/notes/del/blocks").get_json()["blocks"]
        response = client.delete(
            f"{PREFIX}/api/notes/del/blocks/{blocks[0]['id']}?version=1"
        )
        assert response.status_code == 200
        assert response.get_json()["deleted"] == blocks[0]["id"]
        remaining = client.get(f"{PREFIX}/api/notes/del/blocks").get_json()["blocks"]
        assert len(remaining) == 1

    def test_delete_block_requires_a_version(self, client):
        create(client, "DelNoVer", "a")
        block = client.get(f"{PREFIX}/api/notes/delnover/blocks").get_json()["blocks"][
            0
        ]
        response = client.delete(f"{PREFIX}/api/notes/delnover/blocks/{block['id']}")
        assert response.status_code == 400

    def test_move_a_block(self, client):
        create(client, "Move", "a\n\nb")
        blocks = client.get(f"{PREFIX}/api/notes/move/blocks").get_json()["blocks"]
        response = client.post(
            f"{PREFIX}/api/notes/move/blocks/{blocks[0]['id']}/move",
            json={"version": 1, "after_block_id": None},
        )
        assert response.status_code == 200
        after = client.get(f"{PREFIX}/api/notes/move/blocks").get_json()["blocks"]
        assert [b["id"] for b in after] == [blocks[1]["id"], blocks[0]["id"]]

    def test_move_unknown_block_is_404(self, client):
        create(client, "MoveNo", "a")
        response = client.post(
            f"{PREFIX}/api/notes/moveno/blocks/blk-nope/move", json={"version": 1}
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "method,route,payload",
        [
            (
                "patch",
                "/blocks/blk-x",
                {"version": 99, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            (
                "post",
                "/blocks",
                {"version": 99, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            ("post", "/blocks/blk-x/move", {"version": 99}),
        ],
    )
    def test_block_writes_reject_a_stale_version(self, client, method, route, payload):
        create(client, "BlkStale", "a")
        block = client.get(f"{PREFIX}/api/notes/blkstale/blocks").get_json()["blocks"][
            0
        ]
        path = f"{PREFIX}/api/notes/blkstale{route}".replace("blk-x", block["id"])
        assert getattr(client, method)(path, json=payload).status_code == 409


class TestOneRequestOneTransaction:
    def test_a_put_touching_several_blocks_bumps_once(self, client):
        body = create(client, "Multi", "a\n\nb\n\nc")
        blocks = client.get(f"{PREFIX}/api/notes/multi/blocks").get_json()["blocks"]
        for block in blocks:
            block["data"]["text"] = block["data"]["text"].upper()

        response = client.put(
            f"{PREFIX}/api/notes/multi",
            json={"version": body["version"], "blocks": blocks},
        )
        assert response.get_json()["version"] == 2

    def test_a_put_touching_several_blocks_appends_one_revision(self, client):
        body = create(client, "MultiRev", "a\n\nb\n\nc")
        blocks = client.get(f"{PREFIX}/api/notes/multirev/blocks").get_json()["blocks"]
        for block in blocks:
            block["data"]["text"] = "x"

        client.put(
            f"{PREFIX}/api/notes/multirev",
            json={"version": body["version"], "blocks": blocks},
        )

        revisions = client.get(f"{PREFIX}/api/notes/multirev/revisions").get_json()
        # Baseline plus exactly one entry for the three-block change.
        assert revisions["total"] == 2
        assert len(revisions["revisions"][0]["ops"]) == 3

    def test_each_block_route_bumps_once(self, client):
        create(client, "PerRoute", "a\n\nb")
        block = client.get(f"{PREFIX}/api/notes/perroute/blocks").get_json()["blocks"][
            0
        ]
        client.patch(
            f"{PREFIX}/api/notes/perroute/blocks/{block['id']}",
            json={"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
        )
        assert (
            client.get(f"{PREFIX}/api/notes/perroute/blocks").get_json()["version"] == 2
        )
        # Baseline + one.
        assert (
            client.get(f"{PREFIX}/api/notes/perroute/revisions").get_json()["total"]
            == 2
        )


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


class TestRevisionRoutes:
    def test_list_is_newest_first(self, client):
        body = create(client, "Rev", "a")
        block = client.get(f"{PREFIX}/api/notes/rev/blocks").get_json()["blocks"][0]
        client.patch(
            f"{PREFIX}/api/notes/rev/blocks/{block['id']}",
            json={
                "version": body["version"],
                "block_type": "paragraph",
                "data": {"text": "b"},
            },
        )
        revisions = client.get(f"{PREFIX}/api/notes/rev/revisions").get_json()
        assert [r["id"] for r in revisions["revisions"]] == [2, 1]

    def test_list_supports_limit(self, client):
        create(client, "RevLimit", "a")
        block = client.get(f"{PREFIX}/api/notes/revlimit/blocks").get_json()["blocks"][
            0
        ]
        client.patch(
            f"{PREFIX}/api/notes/revlimit/blocks/{block['id']}",
            json={"version": 1, "block_type": "paragraph", "data": {"text": "b"}},
        )
        body = client.get(f"{PREFIX}/api/notes/revlimit/revisions?limit=1").get_json()
        assert len(body["revisions"]) == 1

    def test_get_one_revision(self, client):
        create(client, "RevOne", "a")
        body = client.get(f"{PREFIX}/api/notes/revone/revisions/1").get_json()
        assert body["revision"]["id"] == 1

    def test_get_unknown_revision_is_404(self, client):
        create(client, "RevMiss", "a")
        assert client.get(f"{PREFIX}/api/notes/revmiss/revisions/99").status_code == 404

    def test_list_unknown_slug_is_404(self, client):
        assert client.get(f"{PREFIX}/api/notes/nope/revisions").status_code == 404

    def test_revert_restores_the_previous_state(self, client):
        body = create(client, "Revert", "original")
        block = client.get(f"{PREFIX}/api/notes/revert/blocks").get_json()["blocks"][0]
        client.patch(
            f"{PREFIX}/api/notes/revert/blocks/{block['id']}",
            json={
                "version": body["version"],
                "block_type": "paragraph",
                "data": {"text": "changed"},
            },
        )
        assert (
            client.get(f"{PREFIX}/api/notes/revert/blocks").get_json()["blocks"][0][
                "data"
            ]["text"]
            == "changed"
        )

        response = client.post(
            f"{PREFIX}/api/notes/revert/revisions/2/revert", json={"version": 2}
        )
        assert response.status_code == 200
        assert response.get_json()["version"] == 3
        assert (
            client.get(f"{PREFIX}/api/notes/revert/blocks").get_json()["blocks"][0][
                "data"
            ]["text"]
            == "original"
        )

    def test_revert_appends_a_system_entry(self, client):
        create(client, "RevertSys", "a")
        block = client.get(f"{PREFIX}/api/notes/revertsys/blocks").get_json()["blocks"][
            0
        ]
        client.patch(
            f"{PREFIX}/api/notes/revertsys/blocks/{block['id']}",
            json={"version": 1, "block_type": "paragraph", "data": {"text": "b"}},
        )
        client.post(
            f"{PREFIX}/api/notes/revertsys/revisions/2/revert", json={"version": 2}
        )
        latest = client.get(f"{PREFIX}/api/notes/revertsys/revisions").get_json()[
            "revisions"
        ][0]
        assert latest["author"] == "system"
        assert latest["ops"][0]["op"] == "revert"

    def test_revert_requires_a_version(self, client):
        create(client, "RevertNoVer", "a")
        assert (
            client.post(
                f"{PREFIX}/api/notes/revertnover/revisions/1/revert", json={}
            ).status_code
            == 400
        )

    def test_revert_with_a_stale_version_is_409(self, client):
        create(client, "RevertStale", "a")
        response = client.post(
            f"{PREFIX}/api/notes/revertstale/revisions/1/revert", json={"version": 99}
        )
        assert response.status_code == 409

    def test_revert_an_unknown_revision_is_404(self, client):
        create(client, "RevertMiss", "a")
        response = client.post(
            f"{PREFIX}/api/notes/revertmiss/revisions/99/revert", json={"version": 1}
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_block_document_exports_markdown_with_frontmatter(self, client):
        create(client, "Exported", "# H\n\ntext")
        response = client.get(f"{PREFIX}/api/notes/exported/export")
        assert response.status_code == 200
        assert response.mimetype == "text/markdown"
        text = response.get_data(as_text=True)
        assert text.startswith("---\n")
        assert "# H" in text
        assert "text" in text

    def test_markdown_note_exports_its_body_unchanged(self, client, notes_dir):
        make_legacy(notes_dir, "raw", body="# Heading\n\ntext\n")
        text = client.get(f"{PREFIX}/api/notes/raw/export").get_data(as_text=True)
        assert text.endswith("# Heading\n\ntext\n")

    def test_export_is_inline_by_default(self, client):
        create(client, "Inline", "x")
        response = client.get(f"{PREFIX}/api/notes/inline/export")
        assert response.headers["Content-Disposition"].startswith("inline")

    def test_download_sets_an_attachment_filename(self, client):
        create(client, "Down", "x")
        response = client.get(f"{PREFIX}/api/notes/down/export?download=1")
        assert (
            response.headers["Content-Disposition"] == 'attachment; filename="down.md"'
        )

    def test_export_does_not_bump_the_version(self, client):
        body = create(client, "NoBump", "x")
        client.get(f"{PREFIX}/api/notes/nobump/export")
        assert (
            client.get(f"{PREFIX}/api/notes/nobump").get_json()["meta"]["version"]
            == body["version"]
        )

    def test_export_does_not_append_a_revision(self, client):
        create(client, "NoRev", "x")
        before = client.get(f"{PREFIX}/api/notes/norev/revisions").get_json()["total"]
        client.get(f"{PREFIX}/api/notes/norev/export")
        assert (
            client.get(f"{PREFIX}/api/notes/norev/revisions").get_json()["total"]
            == before
        )

    def test_export_does_not_change_updated(self, client):
        create(client, "NoUpdated", "x")
        before = client.get(f"{PREFIX}/api/notes/noupdated").get_json()["meta"][
            "updated"
        ]
        client.get(f"{PREFIX}/api/notes/noupdated/export")
        assert (
            client.get(f"{PREFIX}/api/notes/noupdated").get_json()["meta"]["updated"]
            == before
        )

    def test_export_unknown_is_404(self, client):
        assert client.get(f"{PREFIX}/api/notes/nope/export").status_code == 404

    def test_a_title_with_yaml_metacharacters_exports_readably(self, client):
        create(client, "Notes: plan", "x")
        text = client.get(f"{PREFIX}/api/notes/notes-plan/export").get_data(
            as_text=True
        )
        # Quoted, so the frontmatter still parses.
        assert 'title: "Notes: plan"' in text


# ---------------------------------------------------------------------------
# Pinning
# ---------------------------------------------------------------------------


class TestPinning:
    def test_pin_sets_primary(self, client):
        create(client, "Pin Me", "x")
        body = client.post(
            f"{PREFIX}/api/notes/pin-me/pin", json={"pinned": True}
        ).get_json()
        assert body["primary"] == "pin-me"

    def test_pinning_a_second_document_replaces_the_first(self, client):
        """There is exactly one scratchpad."""
        create(client, "First", "x")
        create(client, "Second", "x")
        client.post(f"{PREFIX}/api/notes/first/pin", json={"pinned": True})
        body = client.post(
            f"{PREFIX}/api/notes/second/pin", json={"pinned": True}
        ).get_json()
        assert body["primary"] == "second"
        assert "first" in body["pinned"]

    def test_unpin_clears_primary(self, client):
        create(client, "Unpin", "x")
        client.post(f"{PREFIX}/api/notes/unpin/pin", json={"pinned": True})
        body = client.post(
            f"{PREFIX}/api/notes/unpin/pin", json={"pinned": False}
        ).get_json()
        assert body["primary"] is None

    def test_pin_reports_the_title(self, client):
        create(client, "Titled", "x")
        client.post(f"{PREFIX}/api/notes/titled/pin", json={"pinned": True})
        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["title"] == "Titled"
        )

    def test_pin_unknown_slug_is_404(self, client):
        assert (
            client.post(
                f"{PREFIX}/api/notes/nope/pin", json={"pinned": True}
            ).status_code
            == 404
        )

    def test_a_legacy_note_can_be_pinned(self, client, notes_dir):
        """Pinning a `.md` is allowed; the block tools refuse it afterwards."""
        make_legacy(notes_dir)
        response = client.post(f"{PREFIX}/api/notes/legacy/pin", json={"pinned": True})
        assert response.status_code == 200
        assert response.get_json()["primary"] == "legacy"

    def test_scratchpad_is_empty_when_nothing_is_pinned(self, client):
        body = client.get(f"{PREFIX}/api/notes/scratchpad").get_json()
        assert body["primary"] is None
        assert body["pinned"] == []

    def test_the_old_routes_are_gone(self, client):
        """The two superseded routes must not still answer.

        ``set-scratchpad`` is checked as "not 200" rather than 404: the path still
        matches the generic ``/api/notes/<slug>`` rule, which accepts GET, PUT and
        DELETE but not POST, so the honest answer is 405. What matters is that it
        no longer sets a pin.
        """
        assert client.get(f"{PREFIX}/api/scratchpad-status").status_code == 404
        response = client.post(f"{PREFIX}/api/notes/set-scratchpad", json={"slug": "x"})
        assert response.status_code != 200
        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["primary"] is None
        )


class TestSettingsRoute:
    def test_reports_the_frontend_intervals(self, client):
        body = client.get(f"{PREFIX}/api/notes/settings").get_json()
        assert set(body) == {
            "poll_interval_ms",
            "change_debounce_ms",
            "save_debounce_ms",
            "notification_mode",
        }
        assert body["poll_interval_ms"] == settings.notes_poll_interval_ms

    def test_intervals_follow_settings(self, client, monkeypatch):
        monkeypatch.setattr(settings, "notes_poll_interval_ms", 1234)
        assert (
            client.get(f"{PREFIX}/api/notes/settings").get_json()["poll_interval_ms"]
            == 1234
        )


# ---------------------------------------------------------------------------
# Frontend source guards
# ---------------------------------------------------------------------------

# There is no JS test runner in this repo, so invariants that would silently
# regress are asserted against the shipped source instead, following the
# precedent in tests/test_ui_escape_helpers.py.


class TestNotesJs:
    def _source(self) -> str:
        with open("src/wichy/static/notes.js") as handle:
            return handle.read()

    def test_the_removed_routes_are_not_called(self):
        """A call to a route that no longer exists fails silently in the browser."""
        source = self._source()
        assert "scratchpad-status" not in source
        assert "set-scratchpad" not in source

    def test_it_reads_the_new_scratchpad_route(self):
        assert "/api/notes/scratchpad" in self._source()

    def test_it_pins_through_the_per_slug_route(self):
        source = self._source()
        assert "/pin" in source
        assert "pinned: true" in source
        assert "pinned: false" in source

    def test_it_reads_the_primary_field_and_never_the_legacy_slug_field(self):
        """The marker's field is `primary`; `slug` was the legacy name.

        A presence check alone cannot catch this: the file also carries several
        legitimate `data.slug` reads from the document routes, so the assertion
        has to target the scratchpad responses specifically.
        """
        source = self._source()
        assert "data.primary" in source
        # The scratchpad payload never carries `slug`, so a read of it would
        # silently yield undefined and the pin marker would never update.
        assert "statusData.slug" not in source

    def test_it_is_wrapped_in_an_iife(self):
        """Top-level let/const would collide with a second script on the page."""
        source = self._source()
        assert "(function () {" in source
        assert source.rstrip().endswith("})();")

    def test_it_declares_no_top_level_let_or_const(self):
        """Every declaration must be inside the wrapper, not at file scope."""
        for line in self._source().splitlines():
            stripped = line.strip()
            if stripped.startswith(("let ", "const ")):
                # The wrapper means indented declarations are the only ones left.
                assert line.startswith((" ", "\t")), f"file-scope declaration: {line}"


# ---------------------------------------------------------------------------
# Defects and gaps found in review
# ---------------------------------------------------------------------------


class TestRejectionLeavesTheDocumentAlone:
    """A rejected request must not change anything -- including its revision log.

    Asserting only the status code cannot detect a route that moves files or
    writes an entry and then reports a conflict.
    """

    def _snapshot(self, client, slug):
        document = client.get(f"{PREFIX}/api/notes/{slug}").get_json()
        revisions = client.get(f"{PREFIX}/api/notes/{slug}/revisions").get_json()
        return document["meta"]["version"], revisions["total"], document["blocks"]

    @pytest.mark.parametrize(
        "method,route,extra,payload",
        [
            (
                "patch",
                "/blocks/{bid}",
                "",
                {"version": 99, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            (
                "post",
                "/blocks",
                "",
                {"version": 99, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            ("post", "/blocks/{bid}/move", "", {"version": 99}),
            ("delete", "/blocks/{bid}", "?version=99", None),
        ],
    )
    def test_a_stale_block_write_changes_nothing(
        self, client, method, route, extra, payload
    ):
        create(client, "Untouched", "original")
        before = self._snapshot(client, "untouched")
        block = client.get(f"{PREFIX}/api/notes/untouched/blocks").get_json()["blocks"][
            0
        ]
        path = f"{PREFIX}/api/notes/untouched{route.format(bid=block['id'])}{extra}"
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 409
        assert self._snapshot(client, "untouched") == before

    def test_a_rejected_revert_changes_nothing(self, client):
        create(client, "NoRevert", "original")
        before = self._snapshot(client, "norevert")
        response = client.post(
            f"{PREFIX}/api/notes/norevert/revisions/1/revert", json={"version": 99}
        )
        assert response.status_code == 409
        assert self._snapshot(client, "norevert") == before

    def test_a_rejected_put_rename_does_not_move_the_document(self, client, notes_dir):
        """A 409 that has already renamed the file is worse than no check.

        The rename moves the document's files, so it must happen only after the
        version has been confirmed.
        """
        create(client, "Original Name", "x")

        response = client.put(
            f"{PREFIX}/api/notes/original-name",
            json={"version": 99, "meta": {"title": "Renamed Anyway"}},
        )
        assert response.status_code == 409
        # Nothing moved.
        assert (notes_dir / "original-name.json").exists()
        assert not (notes_dir / "renamed-anyway.json").exists()
        assert client.get(f"{PREFIX}/api/notes/original-name").status_code == 200

    def test_a_rejected_put_rename_does_not_repoint_the_marker(self, client):
        create(client, "Pinned One", "x")
        client.post(f"{PREFIX}/api/notes/pinned-one/pin", json={"pinned": True})
        client.put(
            f"{PREFIX}/api/notes/pinned-one",
            json={"version": 99, "meta": {"title": "Moved Anyway"}},
        )
        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["primary"]
            == "pinned-one"
        )

    def test_an_invalid_block_leaves_the_version_and_log_alone(self, client):
        create(client, "BadBlock", "a")
        block = client.get(f"{PREFIX}/api/notes/badblock/blocks").get_json()["blocks"][
            0
        ]
        before = self._snapshot(client, "badblock")
        response = client.patch(
            f"{PREFIX}/api/notes/badblock/blocks/{block['id']}",
            json={"version": 1, "block_type": "header", "data": {"text": "no level"}},
        )
        assert response.status_code == 400
        assert self._snapshot(client, "badblock") == before


class TestMalformedBodies:
    """A bad request body is a 400, never an HTML 500 the browser cannot read."""

    @pytest.mark.parametrize(
        "method,route,payload",
        [
            ("post", "/api/notes", {"title": "X", "blocks": "not a list"}),
            ("post", "/api/notes", {"title": "X", "blocks": [123]}),
            (
                "post",
                "/api/notes",
                {"title": "X", "blocks": [{"type": "paragraph", "data": "nope"}]},
            ),
            ("put", "/api/notes/{slug}", {"version": 1, "blocks": "not a list"}),
            ("put", "/api/notes/{slug}", {"version": 1, "blocks": [123]}),
            ("put", "/api/notes/{slug}", {"version": 1, "meta": "not an object"}),
            ("put", "/api/notes/{slug}", {"version": 1, "meta": {"title": 123}}),
            (
                "patch",
                "/api/notes/{slug}/blocks/blk-x",
                {"version": 1, "block_type": "paragraph", "data": "nope"},
            ),
            (
                "post",
                "/api/notes/{slug}/blocks",
                {"version": 1, "block_type": "paragraph", "data": 5},
            ),
            ("post", "/api/notes/{slug}/revisions/1/revert", {"version": "not an int"}),
        ],
    )
    def test_a_malformed_body_is_a_json_error_not_a_crash(
        self, client, method, route, payload
    ):
        create(client, "Body Target", "a")
        block = client.get(f"{PREFIX}/api/notes/body-target/blocks").get_json()[
            "blocks"
        ][0]
        path = f"{PREFIX}{route}".replace("{slug}", "body-target").replace(
            "blk-x", block["id"]
        )
        response = getattr(client, method)(path, json=payload)
        # Any 4xx is acceptable; an unhandled exception (500 with an HTML body)
        # is not, because the browser parses the response as JSON.
        assert (
            400 <= response.status_code < 500
        ), f"{method.upper()} {route} -> {response.status_code}"
        assert "error" in response.get_json()

    def test_a_json_array_body_is_rejected_cleanly(self, client):
        create(client, "ArrayBody", "a")
        block = client.get(f"{PREFIX}/api/notes/arraybody/blocks").get_json()["blocks"][
            0
        ]
        response = client.patch(
            f"{PREFIX}/api/notes/arraybody/blocks/{block['id']}", json=[1, 2]
        )
        assert 400 <= response.status_code < 500
        assert "error" in response.get_json()


class TestHostileSlugs:
    @pytest.mark.parametrize(
        "slug",
        [
            "..",
            "..%2f..",
            "%2e%2e",
            "BAD",
            "a-",
            "-a",
            "caf\u00e9",
            "a.b",
            "a b",
            "\u2026",
        ],
    )
    def test_a_hostile_slug_is_refused_and_touches_nothing(
        self, client, notes_dir, slug
    ):
        create(client, "Guard", "x")
        before = sorted(p.name for p in notes_dir.iterdir())

        response = client.get(f"{PREFIX}/api/notes/{slug}")

        assert response.status_code in (400, 404), response.status_code
        if response.status_code == 400:
            assert "error" in response.get_json()
        # Nothing was created or removed outside the notes directory.
        assert sorted(p.name for p in notes_dir.iterdir()) == before

    def test_traversal_cannot_escape_the_notes_directory(
        self, client, notes_dir, tmp_path
    ):
        secret = tmp_path / "outside.json"
        secret.write_text('{"blocks": []}', encoding="utf-8")
        for slug in ["..", "../outside", "..%2Foutside", "%2e%2e"]:
            response = client.get(f"{PREFIX}/api/notes/{slug}")
            assert response.status_code in (400, 404)
        # The file outside the notes directory is untouched and unserved.
        assert secret.exists()


class TestDeleteClearsEveryMarkerReference:
    def test_delete_clears_a_pinned_entry_that_is_not_the_primary(self, client):
        """The pinned list is presentation state, but a stale entry points at nothing."""
        create(client, "First Doc", "x")
        create(client, "Second Doc", "x")
        client.post(f"{PREFIX}/api/notes/first-doc/pin", json={"pinned": True})
        client.post(f"{PREFIX}/api/notes/second-doc/pin", json={"pinned": True})
        state = client.get(f"{PREFIX}/api/notes/scratchpad").get_json()
        assert state["primary"] == "second-doc"
        assert "first-doc" in state["pinned"]

        client.delete(f"{PREFIX}/api/notes/first-doc")

        after = client.get(f"{PREFIX}/api/notes/scratchpad").get_json()
        assert after["primary"] == "second-doc"
        assert "first-doc" not in after["pinned"]

    def test_delete_of_the_primary_leaves_other_pins(self, client):
        create(client, "Pin A", "x")
        create(client, "Pin B", "x")
        client.post(f"{PREFIX}/api/notes/pin-a/pin", json={"pinned": True})
        client.post(f"{PREFIX}/api/notes/pin-b/pin", json={"pinned": True})

        client.delete(f"{PREFIX}/api/notes/pin-b")

        after = client.get(f"{PREFIX}/api/notes/scratchpad").get_json()
        assert after["primary"] is None
        assert after["pinned"] == ["pin-a"]


class TestCorruptStoredFiles:
    def test_a_corrupt_json_is_reported_not_served(self, client, notes_dir):
        (notes_dir / "broken.json").write_text("{not json", encoding="utf-8")
        response = client.get(f"{PREFIX}/api/notes/broken")
        assert response.status_code == 500
        assert "error" in response.get_json()

    def test_a_corrupt_json_does_not_break_the_list(self, client, notes_dir):
        create(client, "Good", "x")
        (notes_dir / "broken.json").write_text("{not json", encoding="utf-8")
        response = client.get(f"{PREFIX}/api/notes")
        assert response.status_code == 200
        slugs = {row["slug"] for row in response.get_json()["notes"]}
        assert "good" in slugs
        # The unreadable one is skipped rather than taking the list down.
        assert "broken" not in slugs

    def test_a_non_utf8_markdown_file_is_reported_not_crashed(self, client, notes_dir):
        (notes_dir / "binary.md").write_bytes(b"\xff\xfe\x00\x01")
        response = client.get(f"{PREFIX}/api/notes/binary")
        assert response.status_code in (400, 500)
        assert "error" in response.get_json()


class TestRenameCollision:
    def test_renaming_onto_a_taken_title_does_not_steal_it(self, client, notes_dir):
        create(client, "Alpha", "a")
        create(client, "Beta", "b")

        body = client.get(f"{PREFIX}/api/notes/alpha").get_json()
        response = client.put(
            f"{PREFIX}/api/notes/alpha",
            json={"version": body["meta"]["version"], "meta": {"title": "Beta"}},
        )
        assert response.status_code == 200
        # The rename takes a free slug rather than overwriting the occupant.
        assert response.get_json()["slug"] == "beta-1"
        assert (
            client.get(f"{PREFIX}/api/notes/beta").get_json()["meta"]["title"] == "Beta"
        )
        assert client.get(f"{PREFIX}/api/notes/alpha").status_code == 404

    def test_renaming_to_the_same_title_is_a_no_op_rename(self, client):
        create(client, "Same Title", "a")
        body = client.get(f"{PREFIX}/api/notes/same-title").get_json()
        response = client.put(
            f"{PREFIX}/api/notes/same-title",
            json={"version": body["meta"]["version"], "meta": {"title": "Same Title"}},
        )
        assert response.status_code == 200
        assert response.get_json()["slug"] == "same-title"


class TestExportPositiveControls:
    """The "export does not change X" tests need to prove an export happened."""

    def _export(self, client, slug):
        response = client.get(f"{PREFIX}/api/notes/{slug}/export")
        assert response.status_code == 200
        assert response.mimetype == "text/markdown"
        return response.get_data(as_text=True)

    def test_export_returns_the_body_and_bumps_nothing(self, client):
        body = create(client, "ExportCtl", "# H\n\ntext")
        text = self._export(client, "exportctl")
        assert "# H" in text  # positive control: a real export happened

        after = client.get(f"{PREFIX}/api/notes/exportctl").get_json()
        assert after["meta"]["version"] == body["version"]
        assert after["meta"]["updated"] == body["updated"]
        revisions = client.get(f"{PREFIX}/api/notes/exportctl/revisions").get_json()
        assert revisions["total"] == 1

    def test_a_control_write_does_change_updated(self, client):
        """Proves the export assertions above are not vacuous."""
        create(client, "Stamp", "a")
        before = client.get(f"{PREFIX}/api/notes/stamp").get_json()["meta"]["updated"]
        assert before  # the document really has a timestamp
        block = client.get(f"{PREFIX}/api/notes/stamp/blocks").get_json()["blocks"][0]
        client.patch(
            f"{PREFIX}/api/notes/stamp/blocks/{block['id']}",
            json={"version": 1, "block_type": "paragraph", "data": {"text": "b"}},
        )
        after = client.get(f"{PREFIX}/api/notes/stamp").get_json()["meta"]["updated"]
        assert after != before

    def test_a_markdown_export_is_the_body_plus_frontmatter_exactly(
        self, client, notes_dir
    ):
        make_legacy(notes_dir, "exact", body="# Heading\n\ntext\n")
        text = self._export(client, "exact")
        # Split off the frontmatter block and compare the remainder exactly:
        # a suffix check cannot catch an implementation that appends more.
        head, _, tail = text.partition("---\n")[2].partition("---\n")
        assert head
        assert tail == "# Heading\n\ntext\n"


class TestEachBlockRouteIsOneTransaction:
    @pytest.mark.parametrize(
        "method,route,payload",
        [
            (
                "patch",
                "/blocks/{bid}",
                {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            (
                "post",
                "/blocks",
                {"version": 1, "block_type": "todo", "data": {"text": "x"}},
            ),
            ("delete", "/blocks/{bid}?version=1", None),
            ("post", "/blocks/{bid}/move", {"version": 1}),
        ],
    )
    def test_each_block_route_bumps_once_and_logs_once(
        self, client, method, route, payload
    ):
        create(client, "PerRouteAll", "a\n\nb")
        block = client.get(f"{PREFIX}/api/notes/perrouteall/blocks").get_json()[
            "blocks"
        ][0]
        path = f"{PREFIX}/api/notes/perrouteall{route.format(bid=block['id'])}"
        response = getattr(client, method)(path, json=payload)
        assert response.status_code in (200, 201)

        after = client.get(f"{PREFIX}/api/notes/perrouteall").get_json()
        assert after["meta"]["version"] == 2
        revisions = client.get(f"{PREFIX}/api/notes/perrouteall/revisions").get_json()
        assert revisions["total"] == 2


class TestMarkdownWritesTouchNothing:
    @pytest.mark.parametrize(
        "method,path,payload",
        [
            (
                "put",
                "/api/notes/{slug}",
                {
                    "version": 1,
                    "blocks": [{"type": "paragraph", "data": {"text": "x"}}],
                },
            ),
            (
                "post",
                "/api/notes/{slug}/blocks",
                {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            (
                "patch",
                "/api/notes/{slug}/blocks/blk-1",
                {"version": 1, "block_type": "paragraph", "data": {"text": "x"}},
            ),
            ("post", "/api/notes/{slug}/blocks/blk-1/move", {"version": 1}),
        ],
    )
    def test_a_refused_markdown_write_creates_no_json_and_leaves_the_md(
        self, client, notes_dir, method, path, payload
    ):
        make_legacy(notes_dir)
        original = (notes_dir / "legacy.md").read_bytes()

        response = getattr(client, method)(
            f"{PREFIX}{path.format(slug='legacy')}", json=payload
        )

        assert response.status_code == 409
        # Refused means refused: no `.json` materialised, and the `.md` is intact
        # byte for byte. A route that wrote first and refused afterwards would
        # still return 409.
        assert not (notes_dir / "legacy.json").exists()
        assert (notes_dir / "legacy.md").read_bytes() == original


class TestConvertIsAtomic:
    def test_converting_twice_in_one_request_still_yields_one_document(
        self, client, notes_dir
    ):
        """The existence check is repeated under the lock, so a second write cannot happen."""
        make_legacy(notes_dir, "once", title="Once")
        assert client.post(f"{PREFIX}/api/notes/once/convert").status_code == 200
        assert client.post(f"{PREFIX}/api/notes/once/convert").status_code == 409

        # Exactly one baseline revision: a second write would have appended a
        # second entry with the same id.
        revisions = client.get(f"{PREFIX}/api/notes/once/revisions").get_json()
        assert revisions["total"] == 1
        ids = [r["id"] for r in revisions["revisions"]]
        assert len(ids) == len(set(ids))

    def test_a_converted_document_is_not_the_markdown_fallback(self, client, notes_dir):
        """A 200 from the old slug is not proof: the `.md` alone would answer too."""
        make_legacy(notes_dir)
        client.post(f"{PREFIX}/api/notes/legacy/convert")
        body = client.get(f"{PREFIX}/api/notes/legacy").get_json()
        assert body["format"] == "editorjs"
        assert not (notes_dir / "legacy-1.json").exists()


class TestRejectedSlugDoesNotDelete:
    def test_a_dotted_slug_is_refused_rather_than_deleting_the_file(
        self, client, notes_dir
    ):
        (notes_dir / "my.note.md").write_text("body", encoding="utf-8")
        response = client.delete(f"{PREFIX}/api/notes/my.note")
        assert response.status_code == 400
        assert (notes_dir / "my.note.md").exists()


class TestUnhandledRevertErrors:
    def test_a_revert_on_a_pruned_history_is_a_conflict_not_a_crash(
        self, client, monkeypatch
    ):
        """An incomplete log cannot rebuild the state, so the revert must not run."""
        monkeypatch.setattr(settings, "notes_revisions_max_count", 1)
        monkeypatch.setattr(settings, "notes_revisions_retention", 0)
        create(client, "Pruned", "a")
        for _ in range(3):
            body = client.get(f"{PREFIX}/api/notes/pruned").get_json()
            block = client.get(f"{PREFIX}/api/notes/pruned/blocks").get_json()[
                "blocks"
            ][0]
            client.patch(
                f"{PREFIX}/api/notes/pruned/blocks/{block['id']}",
                json={
                    "version": body["meta"]["version"],
                    "block_type": "paragraph",
                    "data": {"text": "x"},
                },
            )
        version = client.get(f"{PREFIX}/api/notes/pruned").get_json()["meta"]["version"]
        response = client.post(
            f"{PREFIX}/api/notes/pruned/revisions/1/revert", json={"version": version}
        )
        # 409 (cannot rebuild exactly) is acceptable; an HTML 500 is not.
        assert response.status_code in (404, 409)
        assert "error" in response.get_json()


class TestSettingsAndSessions:
    def test_the_list_row_matches_the_documented_shape(self, client):
        create(client, "Row", "x")
        row = client.get(f"{PREFIX}/api/notes").get_json()["notes"][0]
        assert set(row) == {"slug", "title", "version", "updated", "format"}


class TestTheResponseReportsThisRequestsOwnWrite:
    """The response must carry the version this request produced.

    The document is captured from inside the lock. Re-reading it after the lock
    is released would race a concurrent writer, so the response could report a
    version this request did not create -- or 404 after a concurrent delete.

    Made deterministic rather than left to a race: any read that happens outside
    the lock is made to return a different version, which is what a concurrent
    writer would produce.
    """

    def test_a_write_response_does_not_reflect_a_later_read(self, client, monkeypatch):
        body = create(client, "OwnWrite", "a")
        block = client.get(f"{PREFIX}/api/notes/ownwrite/blocks").get_json()["blocks"][
            0
        ]

        real_load = api.load_document

        def bumping_load(slug, **kwargs):
            """Stand in for a concurrent writer having bumped the version."""
            document = real_load(slug, **kwargs)
            document.meta.version += 100
            return document

        # Only the ROUTE's own reads are intercepted. Those inside the lock are
        # taken from the yielded document, not by calling this.
        monkeypatch.setattr(api, "load_document", bumping_load)

        response = client.patch(
            f"{PREFIX}/api/notes/ownwrite/blocks/{block['id']}",
            json={
                "version": body["version"],
                "block_type": "paragraph",
                "data": {"text": "changed"},
            },
        )
        assert response.status_code == 200
        # 2 is what this request produced. 102 would mean the response came from
        # a read taken after the lock was released.
        assert response.get_json()["version"] == 2
