"""Tests for the proposal store and its HTTP routes.

Grouped by what each group defends: the per-note mode flag, the file lifecycle,
supersede-per-target, and the accept/reject transitions against the document.
"""

from __future__ import annotations

import json

import pytest
from flask import Blueprint, Flask

from wichy.config import settings
from wichy.tools.notes import api
from wichy.tools.notes.blocks import (
    load_document,
    revisions_path,
)
from wichy.tools.notes.models import Proposal
from wichy.tools.notes.proposals import (
    create_or_supersede,
    fingerprint_block,
    load_proposals,
    proposals_path,
)
from wichy.tools.notes.state import reset_state

PREFIX = "/tools/notes"


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    target = tmp_path / "notes"
    monkeypatch.setattr(settings, "notes_dir_name", str(target))
    target.mkdir(parents=True, exist_ok=True)
    reset_state()
    yield target
    reset_state()


@pytest.fixture
def client(notes_dir):
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("notes", __name__, url_prefix=PREFIX)
    api.register_routes(bp)
    app.register_blueprint(bp)
    with app.test_client() as client:
        yield client


def create(client, title="My Note", content=""):
    payload = {"title": title, "content": content}
    response = client.post(f"{PREFIX}/api/notes", json=payload)
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()


def make_legacy(notes_dir, slug="legacy"):
    (notes_dir / f"{slug}.md").write_text(
        "---\ntitle: Legacy\n---\n# H\n\ntext\n", encoding="utf-8"
    )


def add_proposal(slug, kind, **kwargs):
    proposal = Proposal(id=kwargs.pop("id", f"prop-{kind}"), kind=kind, **kwargs)
    create_or_supersede(slug, proposal)
    return proposal


class TestMode:
    def test_defaults_on_and_round_trips(self, client):
        slug = create(client)["slug"]
        assert client.get(f"{PREFIX}/api/notes/{slug}/proposals-mode").get_json() == {
            "enabled": True
        }
        assert client.post(
            f"{PREFIX}/api/notes/{slug}/proposals-mode", json={"enabled": False}
        ).get_json() == {"enabled": False}
        assert client.get(f"{PREFIX}/api/notes/{slug}/proposals-mode").get_json() == {
            "enabled": False
        }

    def test_unknown_slug_404(self, client):
        assert client.get(f"{PREFIX}/api/notes/nope/proposals-mode").status_code == 404
        assert (
            client.post(
                f"{PREFIX}/api/notes/nope/proposals-mode", json={"enabled": True}
            ).status_code
            == 404
        )

    def test_markdown_reads_enabled_but_write_refused(self, client, notes_dir):
        make_legacy(notes_dir)
        assert client.get(f"{PREFIX}/api/notes/legacy/proposals-mode").get_json() == {
            "enabled": True
        }
        response = client.post(
            f"{PREFIX}/api/notes/legacy/proposals-mode", json={"enabled": False}
        )
        assert response.status_code == 409


class TestStore:
    def test_create_writes_file_without_touching_document(self, client, notes_dir):
        slug = create(client)["slug"]
        before = client.get(f"{PREFIX}/api/notes/{slug}").get_json()["meta"]["version"]
        revisions_before = revisions_path(slug).read_text(encoding="utf-8")
        add_proposal(slug, "delete", block_id="blk-x")
        assert proposals_path(slug).exists()
        after = client.get(f"{PREFIX}/api/notes/{slug}").get_json()["meta"]["version"]
        assert before == after
        assert revisions_path(slug).read_text(encoding="utf-8") == revisions_before

    def test_supersede_replaces_same_target(self, client):
        slug = create(client)["slug"]
        add_proposal(slug, "delete", block_id="blk-x", id="first")
        add_proposal(slug, "delete", block_id="blk-x", id="second")
        stored = load_proposals(slug)
        assert set(stored) == {"second"}

    def test_supersede_keeps_other_targets(self, client):
        slug = create(client)["slug"]
        add_proposal(slug, "delete", block_id="blk-x", id="a")
        add_proposal(slug, "move", block_id="blk-y", id="b")
        add_proposal(slug, "delete", block_id="blk-x", id="c")
        assert set(load_proposals(slug)) == {"b", "c"}

    def test_get_lists_only_unresolved_with_version(self, client):
        slug = create(client)["slug"]
        add_proposal(slug, "delete", block_id="blk-x", id="open")
        resolved = Proposal(id="done", kind="delete", block_id="blk-y", resolved=True)
        create_or_supersede(slug, resolved)
        body = client.get(f"{PREFIX}/api/notes/{slug}/proposals").get_json()
        assert [p["id"] for p in body["proposals"]] == ["open"]
        assert body["version"] == 1

    def test_reject_removes_and_never_changes_document(self, client):
        slug = create(client)["slug"]
        add_proposal(slug, "delete", block_id="blk-x", id="p1")
        response = client.post(f"{PREFIX}/api/notes/{slug}/proposals/p1/reject")
        assert response.status_code == 200
        assert response.get_json()["proposals"] == []
        assert (
            client.get(f"{PREFIX}/api/notes/{slug}/proposals").get_json()["proposals"]
            == []
        )
        assert (
            client.get(f"{PREFIX}/api/notes/{slug}").get_json()["meta"]["version"] == 1
        )

    def test_reject_unknown_is_404(self, client):
        slug = create(client)["slug"]
        assert (
            client.post(
                f"{PREFIX}/api/notes/{slug}/proposals/missing/reject"
            ).status_code
            == 404
        )


class TestAccept:
    def _make_note(self, client):
        return create(client, "Doc", "# H\n\nsome text")

    def test_accept_write_applies_one_revision(self, client):
        slug = self._make_note(client)["slug"]
        document = load_document(slug)
        target = document.blocks[1]
        add_proposal(
            slug,
            "write",
            block_id=target.id,
            payload={"text": "new text"},
            fingerprint=fingerprint_block(target),
        )
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-write/accept",
            json={"version": 1},
        )
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["version"] == 2
        assert body["applied"] == {"block_id": target.id, "noop": False}
        assert body["proposals"] == []
        doc = load_document(slug)
        assert doc.blocks[1].data["text"] == "new text"
        entries = [
            json.loads(line)
            for line in revisions_path(slug).read_text(encoding="utf-8").splitlines()
        ]
        assert entries[-1]["author"] == "agent"

    def test_accept_insert_uses_reserved_id(self, client):
        slug = self._make_note(client)["slug"]
        add_proposal(
            slug,
            "insert",
            block_id_hint="blk-reserved",
            payload={"type": "paragraph", "text": "added"},
        )
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-insert/accept",
            json={"version": 1},
        )
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["applied"] == {"block_id": "blk-reserved", "noop": False}
        assert load_document(slug).has_block("blk-reserved")

    def test_stale_version_409_leaves_document(self, client):
        slug = self._make_note(client)["slug"]
        document = load_document(slug)
        target = document.blocks[1]
        add_proposal(
            slug,
            "write",
            block_id=target.id,
            payload={"text": "new"},
            fingerprint=fingerprint_block(target),
        )
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-write/accept",
            json={"version": 5},
        )
        assert response.status_code == 409
        assert load_document(slug).meta.version == 1

    def test_changed_fingerprint_409_leaves_document(self, client):
        slug = self._make_note(client)["slug"]
        document = load_document(slug)
        target = document.blocks[1]
        add_proposal(
            slug,
            "write",
            block_id=target.id,
            payload={"text": "new"},
            fingerprint="deadbeef",
        )
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-write/accept",
            json={"version": 1},
        )
        assert response.status_code == 409
        assert load_document(slug).meta.version == 1

    def test_second_accept_of_insert_is_noop(self, client):
        slug = self._make_note(client)["slug"]
        add_proposal(
            slug,
            "insert",
            block_id_hint="blk-reserved",
            payload={"type": "paragraph", "text": "added"},
        )
        client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-insert/accept",
            json={"version": 1},
        )
        add_proposal(
            slug,
            "insert",
            block_id_hint="blk-reserved",
            payload={"type": "paragraph", "text": "added"},
        )
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-insert/accept",
            json={"version": 2},
        )
        body = response.get_json()
        assert response.status_code == 200, body
        assert body["applied"]["noop"] is True
        assert body["version"] == 2
        doc = load_document(slug)
        assert [b.id for b in doc.blocks].count("blk-reserved") == 1


class TestFileLifecycle:
    def test_rename_moves_and_delete_removes(self, client, notes_dir):
        slug = create(client, "Movable")["slug"]
        add_proposal(slug, "delete", block_id="blk-x")
        assert proposals_path(slug).exists()
        renamed = client.put(
            f"{PREFIX}/api/notes/{slug}",
            json={"version": 1, "meta": {"title": "Moved Away"}},
        )
        assert renamed.status_code == 200, renamed.get_data(as_text=True)
        moved_slug = renamed.get_json()["slug"]
        assert moved_slug != slug
        assert proposals_path(moved_slug).exists()
        assert not proposals_path(slug).exists()
        assert client.delete(f"{PREFIX}/api/notes/{moved_slug}").status_code == 200
        assert not proposals_path(moved_slug).exists()


class TestAcceptedInsertOps:
    """An accepted insert records exactly one added block, not the whole note."""

    def test_an_insert_revision_adds_one_block(self, client, notes_dir):
        from wichy.tools.notes.blocks import create_document
        from wichy.tools.notes import set_scratchpad_state
        from wichy.tools.notes.revisions import get_revision

        document = create_document(
            "Big",
            [
                {"type": "paragraph", "data": {"text": "one"}},
                {"type": "paragraph", "data": {"text": "two"}},
                {"type": "paragraph", "data": {"text": "three"}},
            ],
        )
        slug = document.meta.slug
        set_scratchpad_state(slug)
        version = document.meta.version
        add_proposal(
            slug,
            "insert",
            anchor_id=None,
            payload={"type": "paragraph", "text": "four"},
            block_id_hint="blk-hint0001",
        )
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/proposals/prop-insert/accept",
            json={"version": version},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        new_version = response.get_json()["version"]

        after = client.get(f"{PREFIX}/api/notes/{slug}").get_json()
        assert len(after["blocks"]) == 4

        entry = get_revision(slug, new_version)
        adds = [op for op in entry["ops"] if op["op"] == "add"]
        assert len(adds) == 1, entry["ops"]
        assert adds[0]["block_id"] == "blk-hint0001"


class TestAnswerQuestionProposal:
    def test_an_answer_is_proposed_and_applied_as_a_flag(self, client, notes_dir):
        from wichy.tools.notes.agent_tools import AnswerQuestionTool
        from wichy.tools.notes.blocks import create_document
        from wichy.tools.notes import set_scratchpad_state

        document = create_document(
            "Q", [{"type": "question", "data": {"text": "Which?", "answered": False}}]
        )
        set_scratchpad_state(document.meta.slug)
        block_id = document.blocks[0].id

        result = AnswerQuestionTool().execute(block_id=block_id)
        assert "review" in result.lower()
        (proposal,) = load_proposals(document.meta.slug).values()
        assert proposal.payload == {"answer": True}

        version = load_document(document.meta.slug).meta.version
        response = client.post(
            f"{PREFIX}/api/notes/{document.meta.slug}/proposals/{proposal.id}/accept",
            json={"version": version},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        block = load_document(document.meta.slug).blocks[0]
        assert block.data["answered"] is True
        assert block.data["text"] == "Which?"
