"""Tests for the change-notification API and the concurrency model.

Two things live here:

- **The lost update.** `atomic_write` replaces a file atomically but does not
  lock, so two writers that both read version V both pass their version check
  and both write V+1 -- the second silently reverting the first. The
  per-document lock is what makes the 409 meaningful, and the test below is the
  one that fails without it.
- **The change pipeline.** The browser posts what the user did, the server
  injects a summary into the agent's context, and the agent's own edits are
  queued back for the browser to poll. Agent-authored ops must never be injected
  into the agent's own context, or a turn reacts to itself.

The three change routes are driven through a Flask test client, since they are
the surface the browser actually uses.
"""

from __future__ import annotations

import threading

import pytest
from flask import Blueprint, Flask

from wichy.config import settings
from wichy.tools.notes import api
from wichy.tools.notes.blocks import (
    create_document,
    load_document,
    locked_document,
    replace_block,
    revisions_path,
)
from wichy.tools.notes.state import (
    count_distinct_blocks,
    collapse_by_block,
    queue_agent_change,
    reset_state,
    set_agent_busy,
)

PREFIX = "/tools/notes"


class FakeContext:
    """Stands in for a root agent's context, recording what was injected."""

    def __init__(self):
        self.injected: list[tuple[str, str]] = []

    def add(self, role, content):
        """Record one injected message."""
        self.injected.append((role, content))


class FakeAgent:
    """Stands in for a root agent."""

    def __init__(self):
        self.context = FakeContext()


class FakeSession:
    """Stands in for the active chat session."""

    def __init__(self, root_agent=None):
        self.root_agent = root_agent if root_agent is not None else FakeAgent()


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
def doc(notes_dir):
    """A document with three blocks, returned as (slug, [block ids])."""
    document = create_document(
        "Change Doc",
        [
            {"type": "paragraph", "data": {"text": "one"}},
            {"type": "paragraph", "data": {"text": "two"}},
            {"type": "paragraph", "data": {"text": "three"}},
        ],
    )
    return document.meta.slug, [b.id for b in document.blocks]


@pytest.fixture
def session(monkeypatch):
    """An active session with a recording context."""
    from wichy.wichy_server import api as server_api

    fake = FakeSession()
    server_api.set_active_session(fake)
    yield fake
    server_api.set_active_session(None)


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


def post_changes(client, slug, ops, version=1):
    """Post a set of change operations."""
    return client.post(
        f"{PREFIX}/api/changes",
        json={"slug": slug, "version": version, "ops": ops},
    )


def op(kind="update", block_id="blk-a", block_type="paragraph", author="user", **extra):
    """One change operation."""
    return {
        "op": kind,
        "block_id": block_id,
        "block_type": block_type,
        "author": author,
        **extra,
    }


# ---------------------------------------------------------------------------
# The lost update
# ---------------------------------------------------------------------------


class TestLostUpdate:
    """Two writers, one document, exactly one winner.

    This is the test the per-document lock exists for. Without it both writers
    read version V, both pass the check, both write V+1, and one edit is lost
    while both calls report success.
    """

    def test_two_writers_produce_exactly_one_409(self, notes_dir, doc):
        slug, ids = doc
        outcomes: list[str] = []
        barrier = threading.Barrier(2, timeout=5)

        def attempt(label: str) -> None:
            try:
                barrier.wait()
                with locked_document(slug, 1, author="user") as document:
                    replace_block(document, ids[0], data={"text": label}, author="user")
                outcomes.append(label)
            except Exception as e:  # a failed thread must be visible, not swallowed
                outcomes.append(f"error:{type(e).__name__}")

        threads = [
            threading.Thread(target=attempt, args=(name,)) for name in ("A", "B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        for thread in threads:
            assert not thread.is_alive()

        # One won; the other was told its version was stale.
        assert (
            sorted(outcomes) == ["A", "B"]
            or sorted(outcomes)
            == [
                "StaleVersionError",
                "error:StaleVersionError",
            ]
            or "error:StaleVersionError" in outcomes
        )
        # Exactly one writer succeeded.
        winners = [o for o in outcomes if not o.startswith("error:")]
        assert len(winners) == 1
        assert load_document(slug).meta.version == 2

    def test_the_winner_write_is_the_one_that_survived(self, notes_dir, doc):
        slug, ids = doc
        winner: list[str] = []
        barrier = threading.Barrier(2, timeout=5)

        def attempt(label: str) -> None:
            try:
                barrier.wait()
                with locked_document(slug, 1, author="user") as document:
                    replace_block(document, ids[0], data={"text": label}, author="user")
                winner.append(label)
            except Exception:
                pass

        threads = [
            threading.Thread(target=attempt, args=(name,)) for name in ("A", "B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert len(winner) == 1
        # The document holds the winner's text, not the loser's.
        assert load_document(slug).get_block(ids[0]).data["text"] == winner[0]

    def test_one_revision_is_recorded_for_the_winning_write(self, notes_dir, doc):
        slug, ids = doc
        barrier = threading.Barrier(2, timeout=5)

        def attempt(label: str) -> None:
            try:
                barrier.wait()
                with locked_document(slug, 1, author="user") as document:
                    replace_block(document, ids[0], data={"text": label}, author="user")
            except Exception:
                pass

        threads = [
            threading.Thread(target=attempt, args=(name,)) for name in ("A", "B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        entries = [
            line
            for line in revisions_path(slug).read_text().splitlines()
            if line.strip()
        ]
        # Creation baseline plus the single successful edit.
        assert len(entries) == 2

    def test_many_writers_still_yield_one_winner_per_version(self, notes_dir, doc):
        """The lock serialises an arbitrary number of contenders, not just two."""
        slug, ids = doc
        successes: list[str] = []
        barrier = threading.Barrier(6, timeout=10)

        def attempt(index: int) -> None:
            try:
                barrier.wait()
                version = load_document(slug).meta.version
                with locked_document(slug, version, author="user") as document:
                    replace_block(
                        document, ids[0], data={"text": str(index)}, author="user"
                    )
                successes.append(str(index))
            except Exception:
                pass

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        # Every writer that succeeded bumped the version by exactly one, so the
        # number of successes equals the number of versions beyond the first.
        assert len(successes) == load_document(slug).meta.version - 1


# ---------------------------------------------------------------------------
# POST /api/changes
# ---------------------------------------------------------------------------


class TestPostChanges:
    def test_a_user_change_is_injected(self, client, doc, session):
        slug, ids = doc
        response = post_changes(client, slug, [op(block_id=ids[0])])
        assert response.status_code == 200
        assert response.get_json()["injected"] is True

        assert len(session.root_agent.context.injected) == 1
        role, content = session.root_agent.context.injected[0]
        assert role == "user"
        assert content.startswith("[Document changes for: Change Doc]")
        assert content.endswith("[End document changes]")

    def test_the_message_names_the_document_title(self, client, doc, session):
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        content = session.root_agent.context.injected[0][1]
        assert "Change Doc" in content

    def test_the_message_describes_each_op(self, client, doc, session):
        slug, ids = doc
        post_changes(
            client,
            slug,
            [
                op("update", ids[0], "paragraph"),
                op("add", ids[1], "todo"),
            ],
        )
        content = session.root_agent.context.injected[0][1]
        assert f"Updated paragraph block (id: {ids[0]})" in content
        assert f"Added todo block (id: {ids[1]})" in content

    def test_agent_authored_ops_are_never_injected(self, client, doc, session):
        """A turn must not be handed a description of its own edits."""
        slug, ids = doc
        response = post_changes(client, slug, [op(block_id=ids[0], author="agent")])
        assert response.status_code == 200
        assert response.get_json()["injected"] is False
        assert session.root_agent.context.injected == []

    def test_agent_ops_are_filtered_from_a_mixed_batch(self, client, doc, session):
        slug, ids = doc
        post_changes(
            client,
            slug,
            [
                op(block_id=ids[0], author="agent"),
                op(block_id=ids[1], author="user"),
            ],
        )
        assert len(session.root_agent.context.injected) == 1
        content = session.root_agent.context.injected[0][1]
        # Only the user's op is described.
        assert ids[1] in content
        assert ids[0] not in content

    def test_user_ops_are_queued_for_the_browser(self, client, doc, session):
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert len(pending["changes"]) == 1
        assert pending["changes"][0]["block_id"] == ids[0]

    def test_no_session_is_503(self, client, doc):
        slug, ids = doc
        from wichy.wichy_server import api as server_api

        server_api.set_active_session(None)
        response = post_changes(client, slug, [op(block_id=ids[0])])
        assert response.status_code == 503

    def test_no_root_agent_is_also_503(self, client, doc):
        """Both bodies are 503: the client branches on the code, not the text."""
        slug, ids = doc
        from wichy.wichy_server import api as server_api

        session = FakeSession()
        session.root_agent = None
        server_api.set_active_session(session)
        try:
            response = post_changes(client, slug, [op(block_id=ids[0])])
            assert response.status_code == 503
        finally:
            server_api.set_active_session(None)

    def test_the_two_503_bodies_are_distinguishable(self, client, doc):
        """For a human reading logs, even though the client ignores the text."""
        slug, ids = doc
        from wichy.wichy_server import api as server_api

        server_api.set_active_session(None)
        no_session = post_changes(client, slug, [op(block_id=ids[0])]).get_json()[
            "error"
        ]

        session = FakeSession()
        session.root_agent = None
        server_api.set_active_session(session)
        try:
            no_agent = post_changes(client, slug, [op(block_id=ids[0])]).get_json()[
                "error"
            ]
        finally:
            server_api.set_active_session(None)

        assert no_session != no_agent

    def test_a_stale_version_is_409(self, client, doc, session):
        slug, ids = doc
        response = post_changes(client, slug, [op(block_id=ids[0])], version=99)
        assert response.status_code == 409
        assert session.root_agent.context.injected == []

    def test_an_unknown_slug_is_404(self, client, session):
        assert post_changes(client, "nope", [op()]).status_code == 404

    def test_an_invalid_slug_is_400(self, client, session):
        assert post_changes(client, "bad.slug", [op()]).status_code == 400

    def test_ops_must_be_a_list(self, client, doc, session):
        slug, ids = doc
        response = client.post(
            f"{PREFIX}/api/changes", json={"slug": slug, "ops": "not a list"}
        )
        assert response.status_code == 400

    def test_a_non_object_op_is_rejected(self, client, doc, session):
        slug, ids = doc
        response = client.post(
            f"{PREFIX}/api/changes", json={"slug": slug, "ops": [1, 2]}
        )
        assert response.status_code == 400

    def test_a_missing_slug_is_rejected(self, client, session):
        assert client.post(f"{PREFIX}/api/changes", json={"ops": []}).status_code == 400

    def test_an_empty_op_list_injects_nothing(self, client, doc, session):
        slug, ids = doc
        response = post_changes(client, slug, [])
        assert response.get_json()["injected"] is False
        assert session.root_agent.context.injected == []


# ---------------------------------------------------------------------------
# GET /api/changes/pending
# ---------------------------------------------------------------------------


class TestPendingChanges:
    def test_reports_queued_ops_version_and_busy(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))

        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()

        assert len(body["changes"]) == 1
        assert body["version"] == 1
        assert body["agent_busy"] is False
        assert body["slug"] == slug

    def test_busy_reflects_the_agent_state(self, client, doc):
        slug, ids = doc
        set_agent_busy(True)
        try:
            assert client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "agent_busy"
            ]
        finally:
            set_agent_busy(False)

    def test_a_slug_is_required(self, client):
        assert client.get(f"{PREFIX}/api/changes/pending").status_code == 400

    def test_an_invalid_slug_is_400(self, client):
        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug=bad.slug").status_code == 400
        )

    def test_an_unknown_document_is_404(self, client):
        assert client.get(f"{PREFIX}/api/changes/pending?slug=nope").status_code == 404

    def test_polling_drains_the_queue(self, client, doc):
        """A poll consumes what it is given, so a dropped response costs nothing."""
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent"))

        first = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        second = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()

        assert len(first["changes"]) == 1
        assert second["changes"] == []

    def test_the_version_is_read_from_disk_on_a_cold_cache(self, client, doc):
        """A cache miss must not report version 0, which reads as 'all stale'."""
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "x"}, author="user")
        # The document is at version 2 on disk; clear the cache to simulate a
        # poll from a process that has not seen it.
        from wichy.tools.notes.state import clear_doc_version

        clear_doc_version(slug)

        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "version"
            ]
            == 2
        )

    def test_ops_for_one_block_collapse_to_the_latest(self, client, doc):
        slug, ids = doc
        queue_agent_change(
            slug, op(block_id=ids[0], author="agent", data={"text": "a"})
        )
        queue_agent_change(
            slug, op(block_id=ids[0], author="agent", data={"text": "b"})
        )

        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()

        assert len(body["changes"]) == 1
        assert body["changes"][0]["data"] == {"text": "b"}

    def test_the_conflict_flag_is_off_for_a_small_queue(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent"))
        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "conflicted"
            ]
            is False
        )

    def test_the_conflict_flag_is_set_beyond_the_cap(self, client, doc):
        """Past the cap, piecemeal application is no longer safe."""
        slug, ids = doc
        for index in range(api.MAX_CONFLICT_BLOCKS + 1):
            queue_agent_change(slug, op(block_id=f"blk-{index:04d}", author="agent"))

        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert body["conflicted"] is True

    def test_the_cap_counts_distinct_blocks_not_ops(self, client, doc):
        """Many ops on one block are one block, so they must not trip the cap."""
        slug, ids = doc
        for _ in range(api.MAX_CONFLICT_BLOCKS * 3):
            queue_agent_change(slug, op(block_id=ids[0], author="agent"))

        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert body["conflicted"] is False
        assert len(body["changes"]) == 1


# ---------------------------------------------------------------------------
# POST /api/changes/ack
# ---------------------------------------------------------------------------


class TestAckChanges:
    def test_acking_drops_older_ops(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=1))

        response = client.post(
            f"{PREFIX}/api/changes/ack",
            json={"slug": slug, "up_to_version": 1},
        )
        assert response.status_code == 200
        assert response.get_json()["status"] == "ok"

        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert body["changes"] == []

    def test_acking_keeps_newer_ops(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=5))

        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 3}
        )

        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert len(body["changes"]) == 1

    def test_a_missing_version_is_rejected(self, client, doc):
        slug, ids = doc
        response = client.post(f"{PREFIX}/api/changes/ack", json={"slug": slug})
        assert response.status_code == 400

    def test_a_non_integer_version_is_rejected(self, client, doc):
        slug, ids = doc
        response = client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": "one"}
        )
        assert response.status_code == 400

    def test_a_missing_slug_is_rejected(self, client, doc):
        assert (
            client.post(
                f"{PREFIX}/api/changes/ack", json={"up_to_version": 1}
            ).status_code
            == 400
        )

    def test_an_invalid_slug_is_400(self, client, doc):
        response = client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": "bad.slug", "up_to_version": 1}
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------


class TestQueueHelpers:
    def test_collapse_keeps_the_last_op_per_block(self):
        ops = [
            {"block_id": "a", "n": 1},
            {"block_id": "b", "n": 2},
            {"block_id": "a", "n": 3},
        ]
        assert collapse_by_block(ops) == [
            {"block_id": "a", "n": 3},
            {"block_id": "b", "n": 2},
        ]

    def test_collapse_preserves_first_touch_order(self):
        ops = [
            {"block_id": "b", "n": 1},
            {"block_id": "a", "n": 2},
            {"block_id": "b", "n": 3},
        ]
        assert [o["block_id"] for o in collapse_by_block(ops)] == ["b", "a"]

    def test_collapse_of_an_empty_queue(self):
        assert collapse_by_block([]) == []

    def test_count_distinct_blocks(self):
        ops = [{"block_id": "a"}, {"block_id": "a"}, {"block_id": "b"}]
        assert count_distinct_blocks(ops) == 2


# ---------------------------------------------------------------------------
# Document switching and expiry
# ---------------------------------------------------------------------------


class TestDocumentSwitching:
    def test_queues_are_per_document(self, client, doc):
        """Switching documents must not mix their queues."""
        slug, ids = doc
        other = create_document("Other", [{"type": "paragraph", "data": {"text": "x"}}])

        queue_agent_change(slug, op(block_id=ids[0], author="agent"))

        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "changes"
            ]
            != []
        )
        assert (
            client.get(
                f"{PREFIX}/api/changes/pending?slug={other.meta.slug}"
            ).get_json()["changes"]
            == []
        )

    def test_a_converted_document_keeps_its_queue_slot(self, client, notes_dir):
        """Conversion is the same document, so it cannot lose queued ops."""
        (notes_dir / "kept.md").write_text(
            "---\ntitle: Kept\n---\n# H\n\nbody\n", encoding="utf-8"
        )
        queue_agent_change("kept", op(block_id="blk-x", author="agent", version=1))

        client.post(f"{PREFIX}/api/notes/kept/convert")

        body = client.get(f"{PREFIX}/api/changes/pending?slug=kept").get_json()
        assert len(body["changes"]) == 1

    def test_acking_does_not_affect_another_document(self, client, doc):
        slug, ids = doc
        other = create_document(
            "Other2", [{"type": "paragraph", "data": {"text": "x"}}]
        )
        queue_agent_change(
            other.meta.slug, op(block_id="blk-y", author="agent", version=1)
        )

        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 99}
        )

        kept = client.get(
            f"{PREFIX}/api/changes/pending?slug={other.meta.slug}"
        ).get_json()
        assert len(kept["changes"]) == 1


class TestRenamingCarriesTheQueue:
    def test_a_rename_moves_queued_ops(self, client, doc):
        """Renaming the pinned document must not strand its queued edits."""
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=1))
        version = load_document(slug).meta.version

        response = client.put(
            f"{PREFIX}/api/notes/{slug}",
            json={"version": version, "meta": {"title": "Renamed Doc"}},
        )
        assert response.status_code == 200
        new_slug = response.get_json()["slug"]

        body = client.get(f"{PREFIX}/api/changes/pending?slug={new_slug}").get_json()
        assert len(body["changes"]) == 1
        # And the old slug no longer has one.
        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").status_code == 404
        )


# ---------------------------------------------------------------------------
# The injected message
# ---------------------------------------------------------------------------


class TestInjectedMessage:
    def test_it_is_plain_text_not_markdown_heading(self):
        """The agent reads it as a message, so brackets delimit it, not markdown."""
        message = api.change_message("Title", [op(block_id="blk-a")])
        assert message.startswith("[Document changes for: Title]")
        assert message.endswith("[End document changes]")

    def test_one_line_per_op(self):
        message = api.change_message(
            "T", [op("update", "blk-a"), op("remove", "blk-b")]
        )
        assert message.count("- ") == 2

    def test_an_unknown_op_kind_still_renders(self):
        message = api.change_message("T", [{"op": "mystery", "block_id": "blk-a"}])
        assert "blk-a" in message

    def test_a_missing_block_id_does_not_break_it(self):
        message = api.change_message("T", [{"op": "update"}])
        assert "?" in message

    def test_the_title_is_included_verbatim(self):
        message = api.change_message("Notes: plan", [op()])
        assert "Notes: plan" in message
