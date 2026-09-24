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
    delete_block,
    insert_block,
    load_document,
    locked_document,
    replace_block,
    revisions_path,
)
from wichy.tools.notes.state import (
    count_distinct_blocks,
    collapse_by_block,
    discard_stale_changes,
    peek_agent_changes,
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


def post_and_flush(client, slug, ops, version=1):
    """Post change operations and deliver the notification immediately.

    A real POST buffers its ops and returns; the message is injected once the
    user stops editing, after the settle window. Almost every test here is about
    WHAT the notification says rather than when it arrives, so they post and then
    flush, which is exactly what the settle timer does.

    The deferral itself is covered by TestNotificationIsDeferred.
    """
    response = post_changes(client, slug, ops, version=version)
    flush_changes(slug)
    return response


def flush_changes(slug):
    """Deliver a document's buffered notification now."""
    from wichy.tools.notes.state import flush_pending_notification

    return flush_pending_notification(slug)


def bump_to_version_2(slug, block_id):
    """Move the document to version 2, so an op at version 2 is ackable.

    An ack may no longer name a version the document has not reached, so a test
    that acks version 2 has to put the document there first. That is also what the
    real pipeline does: an op's version is the version of the write it came from.
    """
    with locked_document(slug, 1, author="user") as document:
        replace_block(document, block_id, data={"text": "bumped"}, author="user")


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
        response = post_and_flush(client, slug, [op(block_id=ids[0])])
        assert response.status_code == 200

        assert len(session.root_agent.context.injected) == 1
        role, content = session.root_agent.context.injected[0]
        assert role == "user"
        assert content.startswith("[Document changes for: Change Doc]")
        assert content.endswith("[End document changes]")

    def test_the_message_names_the_document_title(self, client, doc, session):
        slug, ids = doc
        post_and_flush(client, slug, [op(block_id=ids[0])])
        content = session.root_agent.context.injected[0][1]
        assert "Change Doc" in content

    def test_the_message_describes_each_op(self, client, doc, session):
        slug, ids = doc
        post_and_flush(
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

    def test_every_op_on_this_route_is_described(self, client, doc, session):
        """The route is the user's channel, so its whole batch is the user's.

        Filtering by a client-supplied author field was how the guarantee used to
        be made. It is now structural: agent-authored content never reaches this
        route (the browser does not post back what the agent sent it), so nothing
        arriving here is filtered out -- including an op that claims otherwise.
        """
        slug, ids = doc
        post_and_flush(
            client,
            slug,
            [
                op(block_id=ids[0], author="agent"),
                op(block_id=ids[1], author="user"),
            ],
        )
        assert len(session.root_agent.context.injected) == 1
        content = session.root_agent.context.injected[0][1]
        assert ids[0] in content
        assert ids[1] in content

    def test_user_ops_are_not_echoed_back_on_the_agent_queue(
        self, client, doc, session
    ):
        """The two directions are separate queues.

        The browser already has its own ops. Handing them back would have it
        reapply older data over newer local edits and mark the block as
        agent-modified.
        """
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert pending["changes"] == []

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

    def test_a_poll_does_not_consume_the_queue(self, client, doc):
        """A lost response must not lose the agent's ops.

        If the poll drained, the response would be its own acknowledgement: a
        closed tab or a dropped connection would take the ops with it, and the
        browser would never learn a version changed so it would never re-fetch
        them either. The ack is what removes them.
        """
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent"))

        first = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        second = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()

        assert len(first["changes"]) == 1
        assert len(second["changes"]) == 1

    def test_acking_is_what_removes_an_op(self, client, doc):
        slug, ids = doc
        # The document must actually BE at the version being acked: an ack ahead
        # of the document is now refused (see TestAckCannotOutpaceTheDocument).
        bump_to_version_2(slug, ids[0])
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))

        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 2}
        )

        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "changes"
            ]
            == []
        )

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


# ---------------------------------------------------------------------------
# Defects found in review
# ---------------------------------------------------------------------------


class TestARenamedDocumentOnARejectedRequest:
    """A rejection must not have moved the document.

    The rename moves files, queued ops and the marker, so doing it before the
    request is validated leaves an error response that lies: it says nothing
    changed while the document is under a slug the caller never asked for.
    """

    def test_a_rejected_block_write_does_not_rename(self, client, notes_dir):
        """Duplicate incoming ids are a 400 -- raised by the merge, not the store."""
        create_document("Foo", [{"type": "paragraph", "data": {"text": "x"}}])
        version = load_document("foo").meta.version

        response = client.put(
            f"{PREFIX}/api/notes/foo",
            json={
                "version": version,
                "meta": {"title": "Bar"},
                "blocks": [
                    {"id": "blk-x", "type": "paragraph", "data": {"text": "a"}},
                    {"id": "blk-x", "type": "paragraph", "data": {"text": "b"}},
                ],
            },
        )
        assert response.status_code == 400
        # Nothing moved.
        assert (notes_dir / "foo.json").exists()
        assert not (notes_dir / "bar.json").exists()
        assert client.get(f"{PREFIX}/api/notes/foo").status_code == 200

    def test_a_stale_rename_does_not_move_the_document(self, client, notes_dir):
        create_document("Stale Name", [{"type": "paragraph", "data": {"text": "x"}}])
        response = client.put(
            f"{PREFIX}/api/notes/stale-name",
            json={"version": 99, "meta": {"title": "Moved Anyway"}},
        )
        assert response.status_code == 409
        assert (notes_dir / "stale-name.json").exists()
        assert not (notes_dir / "moved-anyway.json").exists()

    def test_a_rejected_rename_leaves_the_marker_and_queue_alone(self, client, doc):
        slug, ids = doc
        client.post(f"{PREFIX}/api/notes/{slug}/pin", json={"pinned": True})
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))

        response = client.put(
            f"{PREFIX}/api/notes/{slug}",
            json={
                "version": 99,
                "meta": {"title": "Nope"},
                "blocks": [
                    {"id": "d", "type": "paragraph", "data": {"text": "a"}},
                    {"id": "d", "type": "paragraph", "data": {"text": "b"}},
                ],
            },
        )
        assert response.status_code in (400, 409)

        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["primary"] == slug
        )
        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert len(pending["changes"]) == 1

    def test_a_successful_rename_still_moves_everything(self, client, doc):
        """The positive control: the guard must not block a legitimate rename."""
        slug, ids = doc
        client.post(f"{PREFIX}/api/notes/{slug}/pin", json={"pinned": True})
        version = load_document(slug).meta.version

        response = client.put(
            f"{PREFIX}/api/notes/{slug}",
            json={"version": version, "meta": {"title": "New Home"}},
        )
        assert response.status_code == 200
        assert response.get_json()["slug"] == "new-home"
        assert client.get(f"{PREFIX}/api/notes/new-home").status_code == 200
        assert (
            client.get(f"{PREFIX}/api/notes/scratchpad").get_json()["primary"]
            == "new-home"
        )


class TestInjectionAuthorshipIsStampedByTheServer:
    """The route IS the user-to-agent channel, so the server decides authorship.

    This used to be a client-supplied allow-list: only ops carrying
    ``author == "user"`` were injected. That made the guarantee conventional
    rather than structural. A crafted POST labelled ``author: "user"`` injected
    arbitrary text into the agent's context, and one labelled ``author: "agent"``
    silently suppressed a genuine notification. The browser no longer sends the
    field, and the server stamps it.
    """

    @pytest.mark.parametrize(
        "author", ["Agent", "AGENT", "agent ", None, 1, ["agent"], "user"]
    )
    def test_whatever_the_client_claims_is_ignored(self, client, doc, session, author):
        slug, ids = doc
        operation = op(block_id=ids[0])
        operation["author"] = author

        response = post_and_flush(client, slug, [operation])

        assert response.status_code == 200
        assert len(session.root_agent.context.injected) == 1

    def test_a_forged_user_author_cannot_smuggle_agent_content(
        self, client, doc, session
    ):
        """The concrete attack: a request that lies about who wrote it.

        There is no longer any field to lie about -- the op is described as the
        user's because the route it arrived on is the user's.
        """
        slug, ids = doc
        post_and_flush(client, slug, [op(block_id=ids[0], author="user")])
        assert len(session.root_agent.context.injected) == 1

    def test_an_agent_labelled_op_is_still_injected(self, client, doc, session):
        """Suppression was the other half of the same hole."""
        slug, ids = doc
        post_and_flush(client, slug, [op(block_id=ids[0], author="agent")])
        assert len(session.root_agent.context.injected) == 1

    def test_an_op_with_no_author_at_all_is_injected(self, client, doc, session):
        """The browser's actual payload: no author field."""
        slug, ids = doc
        operation = op(block_id=ids[0])
        del operation["author"]
        post_and_flush(client, slug, [operation])
        assert len(session.root_agent.context.injected) == 1

    def test_the_injected_message_describes_the_op_on_this_route(
        self, client, doc, session
    ):
        """Stamping does not alter what the agent reads, only who wrote it.

        The message names the block and the verb, and carries no authorship field
        at all, so there is nothing for a client to forge into it either.
        """
        slug, ids = doc
        post_and_flush(client, slug, [op(block_id=ids[0], author="agent")])
        content = session.root_agent.context.injected[0][1]
        assert ids[0] in content
        assert "Updated paragraph block" in content


class TestAckKeepsUnversionedOps:
    def test_an_op_with_no_version_is_kept(self, client, doc):
        """ "I do not know when this was made" is not "already applied"."""
        slug, ids = doc
        queue_agent_change(
            slug, {"op": "update", "block_id": ids[0], "author": "agent"}
        )

        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 1}
        )

        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert len(pending["changes"]) == 1

    def test_an_op_with_a_null_version_is_kept(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, {"op": "update", "block_id": ids[0], "version": None})
        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 5}
        )
        assert (
            len(
                client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                    "changes"
                ]
            )
            == 1
        )

    def test_an_acked_versioned_op_is_dropped(self, client, doc):
        """Positive control: versioned ops at or below the ack are removed."""
        slug, ids = doc
        bump_to_version_2(slug, ids[0])
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))
        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 2}
        )
        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "changes"
            ]
            == []
        )


class TestVersionIsRequiredAndTyped:
    @pytest.mark.parametrize("version", ["1", 1.5, None, True])
    def test_a_non_integer_version_is_rejected(self, client, doc, session, version):
        """A check that silently passes for a non-int lets a stale write through."""
        slug, ids = doc
        response = client.post(
            f"{PREFIX}/api/changes",
            json={"slug": slug, "version": version, "ops": [op(block_id=ids[0])]},
        )
        assert response.status_code == 400
        assert session.root_agent.context.injected == []

    def test_an_integer_version_is_accepted(self, client, doc, session):
        slug, ids = doc
        assert (
            post_changes(client, slug, [op(block_id=ids[0])], version=1).status_code
            == 200
        )


class TestUnreadableDocumentOnTheChangeRoutes:
    def test_post_changes_returns_json_not_a_crash(self, client, notes_dir, session):
        (notes_dir / "broken.json").write_bytes(b"\xff\xfe\x00bad")
        response = client.post(
            f"{PREFIX}/api/changes", json={"slug": "broken", "version": 1, "ops": []}
        )
        assert response.status_code == 500
        assert "error" in response.get_json()

    def test_pending_returns_json_not_a_crash(self, client, notes_dir):
        (notes_dir / "broken.json").write_bytes(b"\xff\xfe\x00bad")
        response = client.get(f"{PREFIX}/api/changes/pending?slug=broken")
        assert response.status_code == 500
        assert "error" in response.get_json()


class TestPendingVersionIsNotStale:
    def test_a_concurrent_write_does_not_leave_a_permanently_stale_version(
        self, client, doc
    ):
        """The warm-up runs once; a wrong value would be reported forever.

        Read under the document lock, so the warm-up cannot overwrite a
        concurrent writer's newer version -- which the browser's staleness check
        depends on.
        """
        slug, ids = doc
        from wichy.tools.notes.state import clear_doc_version

        # Simulate a fresh process: cache cold, document already at version 2.
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "x"}, author="user")
        clear_doc_version(slug)

        reported = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
            "version"
        ]
        assert reported == 2
        # And it stays correct on the next poll.
        assert (
            client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()[
                "version"
            ]
            == 2
        )


class TestAckUnknownDocument:
    def test_acking_an_unknown_document_is_404(self, client, doc):
        response = client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": "nope", "up_to_version": 1}
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# The agent's ops are PRODUCED by the write path
# ---------------------------------------------------------------------------


class TestAgentWritesQueueTheirOwnOps:
    """An agent write tells the browser what changed, without anyone asking it to.

    The ops are taken from the same revision entry the write records, so the
    browser's view and the log cannot disagree. Driven through a real
    ``locked_document``/tool call rather than by calling ``queue_agent_change``
    directly: a test that supplies its own ops passes whether or not the
    production path exists, which is how this gap survived the first eleven
    stages.
    """

    def test_an_agent_edit_queues_an_op_carrying_its_version(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[0],
                data={"text": "changed by the agent"},
                author="agent",
                block_type="paragraph",
            )
            expected_version = document.meta.version + 1

        queued = peek_agent_changes(slug)
        assert len(queued) == 1
        assert queued[0]["op"] == "update"
        assert queued[0]["block_id"] == ids[0]
        assert queued[0]["author"] == "agent"
        assert queued[0]["version"] == expected_version
        assert queued[0]["data"] == {"text": "changed by the agent"}

    def test_it_queues_one_op_per_changed_block(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            for block_id in ids:
                replace_block(
                    document,
                    block_id,
                    data={"text": "touched"},
                    author="agent",
                    block_type="paragraph",
                )

        queued = peek_agent_changes(slug)
        assert {entry["block_id"] for entry in queued} == set(ids)
        assert len(queued) == len(ids)
        # One write is one version, so every op names that same version.
        assert len({entry["version"] for entry in queued}) == 1

    def test_an_add_and_a_remove_are_queued_as_such(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            delete_block(document, ids[0])
            insert_block(
                document,
                block_type="header",
                data={"text": "new", "level": 2},
                author="agent",
            )

        kinds = {entry["op"] for entry in peek_agent_changes(slug)}
        assert kinds == {"add", "remove"}

    def test_a_no_op_agent_write_queues_nothing(self, notes_dir, doc):
        slug, _ids = doc
        with locked_document(slug, None, author="agent"):
            pass

        assert peek_agent_changes(slug) == []

    def test_a_user_write_never_queues(self, notes_dir, doc):
        """The browser is the author of a user write; it must not be told about it.

        Queueing it would send the browser an op describing the edit it just
        made, and applying that op would fight whatever the user types next.
        """
        slug, ids = doc
        with locked_document(slug, None, author="user") as document:
            replace_block(
                document,
                ids[0],
                data={"text": "changed by the user"},
                author="user",
                block_type="paragraph",
            )

        assert peek_agent_changes(slug) == []

    def test_the_queue_is_what_the_browser_polls(self, notes_dir, doc):
        """The queued ops reach the browser through the pending route."""
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[1],
                data={"text": "for the browser"},
                author="agent",
                block_type="paragraph",
            )

        reported = peek_agent_changes(slug)
        assert reported, "the write must have queued something to poll for"
        assert reported[0]["data"] == {"text": "for the browser"}

    def test_a_rename_queues_under_the_new_slug(self, notes_dir, doc):
        """Ops must follow the document, like the revision log does.

        Queueing under the pre-rename slug would leave the ops against a name
        that no longer resolves, so the browser would poll forever and never be
        told.
        """
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            document.meta.title = "Renamed By Agent"
            document.meta.slug = "renamed-by-agent"
            replace_block(
                document,
                ids[0],
                data={"text": "after rename"},
                author="agent",
                block_type="paragraph",
            )

        assert peek_agent_changes(slug) == []
        queued = peek_agent_changes("renamed-by-agent")
        assert len(queued) == 1
        assert queued[0]["block_id"] == ids[0]

    def test_an_ack_at_the_queued_version_discards_the_ops(self, notes_dir, doc):
        """Acking what was applied is what clears them, and only that version does."""
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[0],
                data={"text": "x"},
                author="agent",
                block_type="paragraph",
            )
        queued = peek_agent_changes(slug)
        produced = queued[0]["version"]

        discard_stale_changes(slug, produced - 1)
        assert peek_agent_changes(slug), "an older ack must not discard newer ops"

        discard_stale_changes(slug, produced)
        assert peek_agent_changes(slug) == []


# ---------------------------------------------------------------------------
# Stage 4: queue and version integrity
# ---------------------------------------------------------------------------


class TestAckCannotOutpaceTheDocument:
    """An ack says "I applied everything at or before V", so V must exist.

    The queue keeps only ops strictly newer than the ack. Acknowledging a version
    far beyond the document therefore discarded every queued op AND every future
    agent op until the document version caught up -- a silent, permanent loss of
    edits the browser never saw, triggered by a single bad number.
    """

    def test_an_ack_beyond_the_document_is_refused(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))
        response = client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 999999999}
        )
        assert response.status_code == 400
        assert "ahead" in response.get_json()["error"]

    def test_the_queue_survives_a_refused_ack(self, client, doc):
        """A refusal must not be a partial application."""
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))
        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 999999999}
        )
        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert len(pending["changes"]) == 1

    def test_an_ack_at_the_document_version_is_accepted(self, client, doc):
        slug, ids = doc
        response = client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 1}
        )
        assert response.status_code == 200

    def test_a_future_agent_op_is_not_lost_to_an_earlier_bad_ack(self, client, doc):
        """The real damage: ops produced AFTER the bad ack were discarded too."""
        slug, ids = doc
        client.post(
            f"{PREFIX}/api/changes/ack", json={"slug": slug, "up_to_version": 999999999}
        )
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=1))
        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert len(pending["changes"]) == 1


class TestDeleteClearsPerSlugState:
    """A deleted document's queued ops must not outlive it.

    The slug is reusable -- converting keeps it, and a new note can take the old
    name -- so a fresh document would be handed edits meant for the one that was
    deleted.
    """

    def test_delete_clears_the_queue(self, client, doc):
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))
        assert client.delete(f"{PREFIX}/api/notes/{slug}").status_code == 200
        assert peek_agent_changes(slug) == []

    def test_a_document_recreated_under_the_same_slug_starts_clean(
        self, client, doc, notes_dir
    ):
        """The whole point: the new document must not inherit the dead one's edits."""
        slug, ids = doc
        queue_agent_change(slug, op(block_id=ids[0], author="agent", version=2))
        client.delete(f"{PREFIX}/api/notes/{slug}")
        create_document("Change Doc", [{"type": "paragraph", "data": {"text": "new"}}])
        pending = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert pending["changes"] == []

    def test_delete_clears_the_injection_bookkeeping(self, client, doc, session):
        """A new document's first notification must not look like a repeat."""
        slug, ids = doc
        post_and_flush(client, slug, [op(block_id=ids[0])])
        assert len(session.root_agent.context.injected) == 1
        client.delete(f"{PREFIX}/api/notes/{slug}")
        create_document("Change Doc", [{"type": "paragraph", "data": {"text": "new"}}])
        post_and_flush(client, slug, [op(block_id=ids[0])])
        assert len(session.root_agent.context.injected) == 2


class TestCollapseInStorage:
    """Storage is bounded by the block count, not by how long a tab stays shut.

    Collapsing used to happen on the RESPONSE copy only, so a browser that never
    acked (a closed tab) left the server accumulating one op per write forever.
    """

    def test_two_writes_to_one_block_store_one_op(self, notes_dir, doc):
        """Each write must genuinely change the text: a no-op queues nothing.

        The seed text is "one", so the values here avoid it -- writing "one" over
        "one" produces an empty diff, and the test would then pass on a broken
        implementation simply because only one op was ever queued.
        """
        slug, ids = doc
        for text in ("first", "second"):
            with locked_document(slug, None, author="agent") as document:
                replace_block(
                    document,
                    ids[0],
                    data={"text": text},
                    author="agent",
                    block_type="paragraph",
                )
        stored = peek_agent_changes(slug)
        assert len(stored) == 1
        assert stored[0]["data"]["text"] == "second"

    def test_the_latest_op_for_a_block_wins(self, notes_dir, doc):
        """Only the final state is ever delivered, so the older one is dead weight."""
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[0],
                data={"text": "a"},
                author="agent",
                block_type="paragraph",
            )
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[1],
                data={"text": "b"},
                author="agent",
                block_type="paragraph",
            )
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[0],
                data={"text": "c"},
                author="agent",
                block_type="paragraph",
            )
        stored = peek_agent_changes(slug)
        assert [entry["block_id"] for entry in stored] == [ids[0], ids[1]]
        assert stored[0]["data"]["text"] == "c"

    def test_first_touch_order_is_preserved(self, notes_dir, doc):
        slug, ids = doc
        for block_id in (ids[1], ids[2], ids[0]):
            with locked_document(slug, None, author="agent") as document:
                replace_block(
                    document,
                    block_id,
                    data={"text": "x"},
                    author="agent",
                    block_type="paragraph",
                )
        stored = peek_agent_changes(slug)
        assert [entry["block_id"] for entry in stored] == [ids[1], ids[2], ids[0]]

    def test_distinct_blocks_are_still_all_kept(self, notes_dir, doc):
        slug, ids = doc
        for block_id in ids:
            with locked_document(slug, None, author="agent") as document:
                replace_block(
                    document,
                    block_id,
                    data={"text": "x"},
                    author="agent",
                    block_type="paragraph",
                )
        assert len(peek_agent_changes(slug)) == len(ids)

    def test_the_pending_response_shape_is_unchanged(self, client, notes_dir, doc):
        """Storage changed; the wire form must not."""
        slug, ids = doc
        with locked_document(slug, None, author="agent") as document:
            replace_block(
                document,
                ids[0],
                data={"text": "x"},
                author="agent",
                block_type="paragraph",
            )
        body = client.get(f"{PREFIX}/api/changes/pending?slug={slug}").get_json()
        assert set(body) == {"changes", "version", "agent_busy", "slug", "conflicted"}
        assert body["changes"][0]["op"] == "update"


class TestInjectionIsIdempotent:
    """A retried notification must not be delivered twice.

    The browser parks its ops on a 503 and retries. A retry after a lost response
    used to append a second, identical summary of the same change to the agent's
    context, making a turn think twice as much had happened.
    """

    def test_a_retry_of_the_same_version_injects_once(self, client, doc, session):
        slug, ids = doc
        payload = [op(block_id=ids[0])]
        post_and_flush(client, slug, payload)
        post_and_flush(client, slug, payload)
        assert len(session.root_agent.context.injected) == 1

    def test_a_retry_arriving_before_the_delivery_merges(self, client, doc, session):
        """The retry is buffered with the original, so there is one notification.

        Checked here rather than at accept time for exactly this case: the browser
        re-sends while the first batch is still waiting out the settle window, and
        the two must become one message rather than two.
        """
        slug, ids = doc
        payload = [op(block_id=ids[0])]
        post_changes(client, slug, payload)
        post_changes(client, slug, payload)
        flush_changes(slug)
        assert len(session.root_agent.context.injected) == 1

    def test_a_newer_version_still_injects(self, client, doc, session):
        slug, ids = doc
        post_and_flush(client, slug, [op(block_id=ids[0])])
        bump_to_version_2(slug, ids[0])
        post_and_flush(client, slug, [op(block_id=ids[1])], version=2)
        assert len(session.root_agent.context.injected) == 2

    def test_an_older_version_does_not_inject(self, client, doc, session):
        """A late retry of a superseded notification is still a duplicate."""
        slug, ids = doc
        bump_to_version_2(slug, ids[0])
        post_and_flush(client, slug, [op(block_id=ids[1])], version=2)
        post_and_flush(client, slug, [op(block_id=ids[0])], version=1)
        assert len(session.root_agent.context.injected) == 1

    def test_a_failed_injection_does_not_suppress_its_retry(self, client, doc, session):
        """Recording before the injection would lose the notification entirely.

        The recording half must happen only after the inject succeeds: record
        first, and the client is told its retry is a duplicate of a notification
        the agent never received.
        """
        slug, ids = doc
        payload = [op(block_id=ids[0])]

        calls = {"n": 0}

        def flaky_add(role, content):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("context unavailable")
            session.root_agent.context.injected.append((role, content))

        # Rebound on the INSTANCE, so the retry below uses the same object. A
        # monkeypatch here would have to be undone between the two requests, and
        # `monkeypatch` is the same fixture the temp-notes-directory fixture
        # uses -- undoing it would move the notes directory out from under the
        # retry as well.
        session.root_agent.context.add = flaky_add

        # The first delivery fails and leaves the ops buffered for a retry; the
        # second delivers. Nothing is lost, and nothing is injected twice.
        post_changes(client, slug, payload)
        flush_changes(slug)
        assert session.root_agent.context.injected == []
        flush_changes(slug)
        assert len(session.root_agent.context.injected) == 1


class TestBoolVersionIsNotAVersion:
    """``True == 1`` is a trap, not a version.

    ``bool`` is a subclass of ``int``, so ``{"version": true}`` passed a bare
    isinstance check and then compared equal to version 1, satisfying the
    stale-write check on a document at version 1.
    """

    @pytest.mark.parametrize(
        "path,method",
        [
            ("/api/notes/{slug}", "put"),
            ("/api/notes/{slug}/blocks/blk-a", "patch"),
            ("/api/notes/{slug}/blocks", "post"),
            ("/api/notes/{slug}/blocks/blk-a/move", "post"),
            # revert is deliberately absent: it validates the revision and the
            # document format before it looks at the version, so a bad version is
            # not the first thing it reports. Its guard is asserted directly in
            # test_a_bool_version_is_rejected_on_revert.
        ],
    )
    def test_a_bool_version_is_rejected_on_every_mutating_route(
        self, client, doc, path, method
    ):
        slug, ids = doc
        url = f"{PREFIX}{path.format(slug=slug)}"
        body = {"version": True, "blocks": [], "block_type": "paragraph", "data": {}}
        response = getattr(client, method)(url, json=body)
        assert response.status_code == 400, f"{method} {path}"
        assert "version" in response.get_json()["error"]

    def test_a_bool_version_is_rejected_on_revert(self, client, doc):
        """Covered separately: revert reads its revision id first by design."""
        slug, ids = doc
        response = client.post(
            f"{PREFIX}/api/notes/{slug}/revisions/1/revert", json={"version": True}
        )
        assert response.status_code == 400
        assert "version" in response.get_json()["error"]

    def test_a_string_version_is_a_400_not_a_409(self, client, doc):
        """A wrong TYPE is the caller's mistake; only a wrong VALUE is a conflict."""
        slug, ids = doc
        response = client.put(
            f"{PREFIX}/api/notes/{slug}", json={"version": "1", "blocks": []}
        )
        assert response.status_code == 400

    def test_a_bool_query_version_is_rejected_on_delete(self, client, doc):
        slug, ids = doc
        response = client.delete(
            f"{PREFIX}/api/notes/{slug}/blocks/{ids[0]}?version=true"
        )
        assert response.status_code == 400

    def test_a_real_version_still_works(self, client, doc):
        """Positive control for the guard above."""
        slug, ids = doc
        response = client.put(
            f"{PREFIX}/api/notes/{slug}", json={"version": 1, "blocks": []}
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Deferred delivery: one notification per burst, not per keystroke batch
# ---------------------------------------------------------------------------


class TestNotificationIsDeferred:
    """A POST buffers; the message is injected once the edits stop.

    The browser posts a batch on every pause longer than its own debounce, so one
    sentence produced several notifications about the SAME block. The server holds
    the burst instead and delivers one message describing the final state.
    """

    def test_a_post_does_not_inject_immediately(self, client, doc, session):
        """Accepting an edit must not also notify: that is what flooded context."""
        slug, ids = doc
        response = post_changes(client, slug, [op(block_id=ids[0])])
        assert response.status_code == 200
        assert response.get_json()["pending"] is True
        assert session.root_agent.context.injected == []

    def test_the_buffered_ops_are_visible_before_delivery(self, client, doc, session):
        from wichy.tools.notes.state import peek_pending_notification

        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        buffered = peek_pending_notification(slug)
        assert [entry["block_id"] for entry in buffered] == [ids[0]]

    def test_flushing_delivers_one_message(self, client, doc, session):
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        flush_changes(slug)
        assert len(session.root_agent.context.injected) == 1

    def test_the_buffer_is_emptied_by_delivery(self, client, doc, session):
        from wichy.tools.notes.state import peek_pending_notification

        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        flush_changes(slug)
        assert peek_pending_notification(slug) == []

    def test_successive_batches_of_one_block_become_one_notification(
        self, client, doc, session
    ):
        """The reported flood: one sentence, several batches, several messages.

        Each batch describes the same block. They must arrive as a single message
        describing what the text finally became.
        """
        slug, ids = doc
        for text in ("Hel", "Hello", "Hello wor", "Hello world"):
            post_changes(
                client,
                slug,
                [op(block_id=ids[0], data={"text": text})],
            )
        flush_changes(slug)
        assert len(session.root_agent.context.injected) == 1
        content = session.root_agent.context.injected[0][1]
        assert content.count(f"id: {ids[0]}") == 1

    def test_a_later_batch_replaces_an_earlier_one_for_the_same_block(
        self, client, doc, session
    ):
        """Only the final state is reported; intermediate keystrokes are not."""
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "draft"})])
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "final"})])
        flush_changes(slug)
        content = session.root_agent.context.injected[0][1]
        assert "final" in content
        assert "draft" not in content

    def test_batches_of_different_blocks_are_all_reported(self, client, doc, session):
        """Merging is per block; a second block is a second entry, not a loss."""
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "one"})])
        post_changes(client, slug, [op(block_id=ids[1], data={"text": "two"})])
        flush_changes(slug)
        content = session.root_agent.context.injected[0][1]
        assert ids[0] in content
        assert ids[1] in content

    def test_first_touch_order_is_preserved(self, client, doc, session):
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[2], data={"text": "c"})])
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "a"})])
        post_changes(client, slug, [op(block_id=ids[2], data={"text": "c2"})])
        flush_changes(slug)
        content = session.root_agent.context.injected[0][1]
        assert content.index(ids[2]) < content.index(ids[0])

    def test_delivery_with_no_session_keeps_the_ops_buffered(
        self, client, doc, session
    ):
        """A notification must not be lost because the agent was unreachable."""
        from wichy.tools.notes.state import peek_pending_notification
        from wichy.wichy_server import api as server_api

        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        server_api.set_active_session(None)
        try:
            flush_changes(slug)
            assert len(peek_pending_notification(slug)) == 1
        finally:
            server_api.set_active_session(session)

    def test_a_later_flush_delivers_what_was_kept(self, client, doc, session):
        from wichy.wichy_server import api as server_api

        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        server_api.set_active_session(None)
        try:
            flush_changes(slug)
        finally:
            server_api.set_active_session(session)
        flush_changes(slug)
        assert len(session.root_agent.context.injected) == 1

    def test_deleting_cancels_a_buffered_notification(self, client, doc, session):
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0])])
        client.delete(f"{PREFIX}/api/notes/{slug}")
        flush_changes(slug)
        assert session.root_agent.context.injected == []

    def test_a_rename_carries_the_buffer_to_the_new_slug(self, client, doc, session):
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "x"})])
        response = client.put(
            f"{PREFIX}/api/notes/{slug}",
            json={"version": 1, "meta": {"title": "Renamed Doc"}},
        )
        new_slug = response.get_json()["slug"]
        flush_changes(new_slug)
        assert len(session.root_agent.context.injected) == 1
        assert "Renamed Doc" in session.root_agent.context.injected[0][1]


class TestTheMessageShowsWhatChanged:
    """The message must say what the note now SAYS, not just that it moved.

    "Updated paragraph block (id: blk-3)" told the agent something changed and
    nothing it could act on, so it had to re-read the document to find out --
    exactly the read the notification exists to save.
    """

    def message(self, client, slug, ops, session):
        post_and_flush(client, slug, ops)
        return session.root_agent.context.injected[-1][1]

    def test_an_update_shows_a_before_and_after_diff(self, client, doc, session):
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [
                op(
                    block_id=ids[0],
                    before={"type": "paragraph", "data": {"text": "one"}},
                    data={"text": "one and a half"},
                )
            ],
            session,
        )
        assert "-one" in content
        assert "+one and a half" in content

    def test_an_update_without_a_before_still_shows_the_new_text(
        self, client, doc, session
    ):
        """A client that sends no previous content is not left with a bare line."""
        slug, ids = doc
        content = self.message(
            client, slug, [op(block_id=ids[0], data={"text": "fresh"})], session
        )
        assert "fresh" in content

    def test_an_add_shows_the_new_text(self, client, doc, session):
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [op("add", ids[1], "todo", data={"text": "buy milk"})],
            session,
        )
        assert "buy milk" in content

    def test_a_remove_shows_the_deleted_text(self, client, doc, session):
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [
                op(
                    "remove",
                    ids[0],
                    "paragraph",
                    before={"type": "paragraph", "data": {"text": "goodbye"}},
                )
            ],
            session,
        )
        assert "goodbye" in content

    def test_a_header_shows_its_level_and_text(self, client, doc, session):
        """Rendered through the same renderer the export uses, so they agree."""
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [op("add", ids[1], "header", data={"text": "Title", "level": 2})],
            session,
        )
        assert "## Title" in content

    def test_a_very_long_block_is_truncated(self, client, doc, session):
        """A summary that pastes a whole document back in saves nothing."""
        slug, ids = doc
        body = "\n".join(f"line {i}" for i in range(200))
        content = self.message(
            client,
            slug,
            [op("add", ids[1], "code", data={"code": body, "language": ""})],
            session,
        )
        assert "more lines" in content
        assert content.count("line ") < 200

    def test_a_move_reports_its_new_position(self, client, doc, session):
        slug, ids = doc
        content = self.message(client, slug, [op("move", ids[0], index=2)], session)
        assert "to position 2" in content

    def test_the_summary_line_precedes_its_content(self, client, doc, session):
        slug, ids = doc
        content = self.message(
            client, slug, [op(block_id=ids[0], data={"text": "after"})], session
        )
        assert content.index(f"id: {ids[0]}") < content.index("after")


class TestMergingWithinABurst:
    """What a merged burst reports, when the op kinds differ.

    The merge is not a plain "keep the last op": the kind decides what the agent
    should be told, and a wrong answer is a diff against the wrong baseline.
    """

    def message(self, client, slug, ops_list, session):
        for ops in ops_list:
            post_changes(client, slug, ops)
        flush_changes(slug)
        return session.root_agent.context.injected[-1][1]

    def test_the_earliest_before_is_kept(self, client, doc, session):
        """A burst's diff must span the whole edit, not just its last keystroke."""
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [
                [
                    op(
                        block_id=ids[0],
                        before={"type": "paragraph", "data": {"text": "Hel"}},
                        data={"text": "Hello"},
                    )
                ],
                [
                    op(
                        block_id=ids[0],
                        before={"type": "paragraph", "data": {"text": "Hello"}},
                        data={"text": "Hello world"},
                    )
                ],
            ],
            session,
        )
        assert "+Hello world" in content
        assert "-Hel" in content

    def test_a_block_added_then_edited_stays_an_addition(self, client, doc, session):
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [
                [op("add", "blk-new", "paragraph", data={"text": "first"})],
                [
                    op(
                        "update",
                        "blk-new",
                        "paragraph",
                        before={"type": "paragraph", "data": {"text": "first"}},
                        data={"text": "second"},
                    )
                ],
            ],
            session,
        )
        assert "Added paragraph block (id: blk-new)" in content
        assert "+ second" in content

    def test_a_block_added_then_removed_is_a_removal(self, client, doc, session):
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [
                [op("add", "blk-new", "paragraph", data={"text": "transient"})],
                [op("remove", "blk-new", "paragraph")],
            ],
            session,
        )
        assert "Deleted paragraph block (id: blk-new)" in content

    def test_a_removed_block_keeps_its_pre_burst_text(self, client, doc, session):
        """The removal must show the text from BEFORE the burst, not a later one."""
        slug, ids = doc
        content = self.message(
            client,
            slug,
            [
                [
                    op(
                        block_id=ids[0],
                        before={"type": "paragraph", "data": {"text": "original"}},
                        data={"text": "edited"},
                    )
                ],
                [
                    op(
                        "remove",
                        ids[0],
                        "paragraph",
                        before={"type": "paragraph", "data": {"text": "edited"}},
                    )
                ],
            ],
            session,
        )
        assert "original" in content


class TestTheSettleTimerItself:
    """The buffer's actual delivery path: a real timer thread, not the flush call.

    Every other test here delivers with `flush_pending_notification`, which
    bypasses the timer. That left the timer's own bookkeeping -- clearing the
    buffer after a delivery -- untested, and it was wrong: the ops were delivered
    but stayed buffered, so the next timer would deliver the same change again.

    The settle window is set INSIDE each test, after the client fixture has built
    the app, because registration installs the value from settings and setting it
    any earlier would be overwritten.
    """

    def test_a_burst_is_delivered_once_and_the_buffer_cleared(
        self, client, doc, session
    ):
        import time

        from wichy.tools.notes.state import (
            peek_pending_notification,
            set_notify_settle_seconds,
        )

        set_notify_settle_seconds(0.05)
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "a"})])
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "ab"})])
        # Comfortably past the settle window, so a second delivery would have
        # happened by now if the buffer were not cleared.
        time.sleep(0.5)
        assert len(session.root_agent.context.injected) == 1
        assert peek_pending_notification(slug) == []

    def test_the_delivered_message_describes_the_merged_burst(
        self, client, doc, session
    ):
        import time

        from wichy.tools.notes.state import set_notify_settle_seconds

        set_notify_settle_seconds(0.05)
        slug, ids = doc
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "a"})])
        post_changes(client, slug, [op(block_id=ids[0], data={"text": "ab"})])
        time.sleep(0.5)
        content = session.root_agent.context.injected[0][1]
        assert content.count(f"id: {ids[0]}") == 1
        assert "ab" in content
