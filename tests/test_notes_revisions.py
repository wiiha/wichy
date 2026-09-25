"""Tests for the revision log.

Grouped by the property each group defends:

- appending: one change is one entry, carrying one op per changed block
- id monotonicity: ids never repeat or reset, including across rotation
- rotation and retention: count-based, and it never loses the counter
- reading: newest first, filters, and history that spans rotated files
- diffing: content changes, reorders, and additions are distinguished
- replay and revert: past states are rebuilt forward, and a revert is itself
  recorded rather than rewriting history

Revision numbering: **revision 1 is the document's creation snapshot**. The log
stores diffs, so without a baseline the earliest state could never be rebuilt and
a revert would have nothing to restore. The first edit to a freshly created
document is therefore revision 2.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from wichy.config import settings
from wichy.tools.notes.blocks import (
    StaleVersionError,
    create_document,
    delete_document_files,
    delete_block,
    insert_block,
    load_document,
    locked_document,
    move_block,
    replace_block,
    revisions_path,
)
from wichy.tools.notes.models import BlockDataError
from wichy.tools.notes.revisions import (
    CorruptRevisionLogError,
    HistoryAnchorError,
    IncompleteHistoryError,
    RevisionNotFoundError,
    all_entries,
    append_entry,
    block_snapshot,
    browse_entries,
    count_revisions,
    describe_ops,
    diff_ops,
    get_revision,
    make_entry,
    read_log_entries,
    read_revisions,
    replay,
    replay_matches_document,
    restore_document,
    revert_document,
    rotate_if_needed,
    rotate_now,
    rotated_logs,
    state_before,
)
from wichy.tools.notes.state import reset_state


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
    """A created document, returned as (slug, [block ids])."""
    document = create_document(
        "Log Doc",
        [
            {"type": "paragraph", "data": {"text": "one"}},
            {"type": "paragraph", "data": {"text": "two"}},
            {"type": "paragraph", "data": {"text": "three"}},
        ],
    )
    return document.meta.slug, [b.id for b in document.blocks]


def record(slug, author="user", summary=None, extra_ops=()):
    """Apply one no-op change and return the revision entry it recorded.

    The entry is recorded by ``locked_document`` itself, which is what makes
    "one mutation is one revision" structural rather than a convention every
    caller has to remember.
    """
    with locked_document(
        slug, None, author=author, summary=summary, extra_ops=extra_ops
    ):
        pass
    return all_entries(slug)[-1]


def drop_log(slug):
    """Delete a document's whole history, for tests about a missing log."""
    revisions_path(slug).unlink(missing_ok=True)
    for path in rotated_logs(slug):
        path.unlink(missing_ok=True)


def write_damaged_log(slug, entries):
    """Write a log directly, bypassing the repair append_entry performs.

    For tests about what a READER does with a log that is already missing its
    start. Going through append_entry would anchor it, which is correct
    behaviour and exactly what these tests must avoid.
    """
    revisions_path(slug).write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )


def append_entries(slug, count, start=1):
    """Append ``count`` synthetic entries, bypassing the document entirely."""
    for offset in range(count):
        append_entry(
            slug,
            make_entry(
                revision_id=start + offset,
                author="user",
                version_from=1,
                version_to=2,
                ops=[],
                summary="synthetic",
            ),
        )


def write_legacy_anchor_log(slug, *, anchor_id, keep_ids):
    """Rewrite ``slug``'s log so an anchor restates the state before a survivor.

    Legacy logs from the old build carry such an anchor: it was written where
    earlier revisions had been dropped, restating the state at that point so
    everything onward still replays exactly. Nothing writes one now, so the log
    is written directly -- going through ``append_entry`` never produces it.

    Args:
        slug: The document slug.
        anchor_id: The id the anchor reuses.
        keep_ids: Ids of the surviving entries to keep after the anchor.

    Returns:
        The anchor entry as written.
    """
    entries = all_entries(slug)
    before = replay(slug, upto_id=min(keep_ids))
    anchor_ops = [
        {
            "op": "add",
            "block_id": block.id,
            "block_type": block.type,
            "data": dict(block.data),
            "index": index,
        }
        for index, block in enumerate(before.blocks)
    ]
    anchor = {
        "id": anchor_id,
        "timestamp": "2026-01-01T00:00:00Z",
        "author": "system",
        "baseline": True,
        "version_from": 0,
        "version_to": 0,
        "ops": anchor_ops,
        "summary": (
            "History starts here. Earlier revisions were dropped; the document "
            "can be rebuilt from this point on."
        ),
    }
    survivors = [e for e in entries if e["id"] in keep_ids]
    revisions_path(slug).write_text(
        "".join(json.dumps(e) + "\n" for e in [anchor, *survivors]),
        encoding="utf-8",
    )
    return anchor


# ---------------------------------------------------------------------------
# Appending
# ---------------------------------------------------------------------------


class TestAppend:
    def test_creation_records_a_baseline_snapshot(self, notes_dir, doc):
        """Revision 1 is the content as created, so replay has a starting point."""
        slug, ids = doc
        entries = all_entries(slug)
        assert [e["id"] for e in entries] == [1]
        assert entries[0]["summary"] == "Created document with 3 blocks"
        assert [op["block_type"] for op in entries[0]["ops"]] == ["paragraph"] * 3
        assert [op["index"] for op in entries[0]["ops"]] == [0, 1, 2]
        assert all(op["op"] == "add" for op in entries[0]["ops"])

    def test_one_change_is_one_entry(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "changed"}, author="user")

        entries = all_entries(slug)
        assert [e["id"] for e in entries] == [1, 2]
        edit = entries[1]
        assert edit["author"] == "user"
        assert edit["version_from"] == 1
        assert edit["version_to"] == 2
        assert [op["op"] for op in edit["ops"]] == ["update"]
        assert edit["ops"][0]["block_id"] == ids[0]

    def test_entry_carries_one_op_per_changed_block(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "a"}, author="user")
            replace_block(document, ids[1], data={"text": "b"}, author="user")

        entry = all_entries(slug)[-1]
        assert len(entry["ops"]) == 2
        # Changing two blocks is still ONE version bump and ONE entry.
        assert entry["version_from"] == 1
        assert entry["version_to"] == 2

    def test_an_unchanged_body_records_an_entry_with_no_ops(self, notes_dir, doc):
        """The entry is structural: a write happened, so a revision exists."""
        slug, _ = doc
        entry = record(slug)
        assert entry["id"] == 2
        assert entry["ops"] == []
        assert entry["summary"] == "No changes"

    def test_entry_shape_matches_the_schema(self, notes_dir, doc):
        slug, _ = doc
        entry = record(slug)
        assert set(entry) == {
            "id",
            "timestamp",
            "author",
            "version_from",
            "version_to",
            "ops",
            "summary",
        }

    def test_log_is_append_only_jsonl(self, notes_dir, doc):
        slug, _ = doc
        record(slug)
        record(slug)
        lines = revisions_path(slug).read_text(encoding="utf-8").splitlines()
        # The creation baseline plus the two changes.
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # each line is independently valid JSON

    def test_agent_authored_entry_records_agent(self, notes_dir, doc):
        slug, _ = doc
        entry = record(slug, author="agent")
        assert entry["author"] == "agent"
        assert load_document(slug).meta.last_author == "agent"

    def test_explicit_summary_wins(self, notes_dir, doc):
        slug, _ = doc
        assert record(slug, summary="Custom text")["summary"] == "Custom text"

    def test_extra_ops_are_included(self, notes_dir, doc):
        """A revert marker rides alongside the block diff it produced."""
        slug, _ = doc
        entry = record(slug, extra_ops=[{"op": "revert", "revision_id": 1}])
        assert entry["ops"][0]["op"] == "revert"
        assert entry["ops"][0]["revision_id"] == 1


class TestDescribeOps:
    @pytest.mark.parametrize(
        "ops,expected",
        [
            ([], "No changes"),
            ([{"op": "update"}], "Updated 1 block"),
            ([{"op": "update"}, {"op": "update"}], "Updated 2 blocks"),
            ([{"op": "add"}, {"op": "remove"}], "Added 1 block and Deleted 1 block"),
            ([{"op": "revert"}], "Reverted 1 document"),
        ],
    )
    def test_summaries(self, ops, expected):
        assert describe_ops(ops) == expected


# ---------------------------------------------------------------------------
# Id monotonicity
# ---------------------------------------------------------------------------


class TestRevisionIds:
    def test_ids_increase_by_one(self, notes_dir, doc):
        slug, _ = doc
        ids = [record(slug)["id"] for _ in range(5)]
        # The baseline took id 1, so the first recorded change is id 2.
        assert ids == [2, 3, 4, 5, 6]

    def test_counter_is_persisted_in_document_meta(self, notes_dir, doc):
        slug, _ = doc
        record(slug)
        record(slug)
        # Baseline + two changes consumed ids 1-3, so the next is 4. Persisted,
        # not just held in memory: it has to survive a restart.
        assert load_document(slug).meta.next_revision_id == 4

    def test_the_counter_is_not_advanced_by_a_failed_change(self, notes_dir, doc):
        """A rejected change must not consume an id, or the log would show a gap."""
        slug, ids = doc
        with pytest.raises(BlockDataError):
            with locked_document(slug, 1, author="user") as document:
                replace_block(document, ids[0], data={"nope": 1}, author="user")

        assert load_document(slug).meta.next_revision_id == 2
        assert [e["id"] for e in all_entries(slug)] == [1]
        assert load_document(slug).meta.version == 1

    def test_rejected_stale_write_records_nothing(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "x"}, author="user")

        with pytest.raises(StaleVersionError):
            with locked_document(slug, 1, author="user") as document:
                replace_block(document, ids[0], data={"text": "y"}, author="user")

        # The baseline plus the one successful edit; the stale attempt added none.
        assert count_revisions(slug) == 2
        assert load_document(slug).meta.version == 2

    def test_ids_do_not_reset_across_rotation(self, notes_dir, doc, monkeypatch):
        """The counter lives in meta, not in the log, so rotation cannot reset it."""
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        drop_log(slug)
        append_entries(slug, ROTATE_AT_ENTRIES + 1)

        rotated = rotated_logs(slug)
        assert rotated, "the log should have rotated"
        live_ids = [e["id"] for e in read_log_entries(revisions_path(slug))]
        prior_ids = [e["id"] for e in read_log_entries(rotated[0])]
        # Numbering continued rather than restarting at 1.
        assert set(live_ids).isdisjoint(prior_ids)
        assert min(live_ids) > max(prior_ids)

    def test_ids_stay_unique_under_concurrent_appends(self, notes_dir, doc):
        """The counter is allocated under the document lock, so no two share an id."""
        slug, ids = doc
        results: list[int] = []
        barrier = threading.Barrier(4, timeout=5)
        errors: list[str] = []

        def append_one() -> None:
            try:
                barrier.wait()
                with locked_document(slug, None, author="user") as document:
                    replace_block(document, ids[0], data={"text": "x"}, author="user")
                results.append(all_entries(slug)[-1]["id"])
            except Exception as e:  # surfacing the failure beats a swallowed thread
                errors.append(repr(e))

        threads = [threading.Thread(target=append_one) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        for thread in threads:
            assert not thread.is_alive()

        assert errors == []
        assert len(set(results)) == 4
        # Every id is distinct and present exactly once across the whole log.
        logged = [e["id"] for e in all_entries(slug)]
        assert sorted(logged) == [1, 2, 3, 4, 5]
        assert len(logged) == len(set(logged))


# ---------------------------------------------------------------------------
# Rotation and retention
# ---------------------------------------------------------------------------


class TestRotation:
    def test_no_rotation_below_the_threshold(self, notes_dir, doc):
        slug, _ = doc
        drop_log(slug)
        append_entries(slug, 4)
        assert rotated_logs(slug) == []

    def test_rotation_fires_once_the_log_has_reached_the_threshold(
        self, notes_dir, doc
    ):
        """Rotation is checked before each append, not after."""
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        drop_log(slug)

        append_entries(slug, ROTATE_AT_ENTRIES)  # exactly the threshold
        assert rotated_logs(slug) == []

        append_entries(slug, 1, start=ROTATE_AT_ENTRIES + 1)  # rotates first
        assert len(rotated_logs(slug)) == 1
        assert len(read_log_entries(revisions_path(slug))) == 1

    def test_rotated_name_contains_a_utc_timestamp(self, notes_dir, doc, monkeypatch):
        slug, _ = doc
        drop_log(slug)
        append_entries(slug, 3)
        rotated = rotate_now(slug)
        assert rotated is not None
        assert rotated.name.startswith(f"{slug}.revisions.")
        assert rotated.name.endswith(".jsonl")
        stamp = rotated.name[len(f"{slug}.revisions.") : -len(".jsonl")]
        assert len(stamp) >= 15  # YYYYMMDDTHHMMSS

    def test_rotation_can_happen_twice_in_one_second(self, notes_dir, doc):
        """The stamp has one-second granularity, so names must not collide."""
        slug, _ = doc
        drop_log(slug)
        append_entries(slug, 1)
        first = rotate_now(slug)
        append_entries(slug, 1, start=2)
        second = rotate_now(slug)

        assert first is not None and second is not None
        assert first != second
        # Both survive: neither rotation destroyed the other's history.
        assert first.exists() and second.exists()

    def test_rotation_preserves_every_entry(self, notes_dir, doc):
        """Nothing is lost by ROTATING: every entry remains readable.

        Rotation is a storage detail, so the entries in the rotated file and the
        live one together still account for the whole history.
        """
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        drop_log(slug)
        append_entries(slug, ROTATE_AT_ENTRIES + 3)
        assert count_revisions(slug) == ROTATE_AT_ENTRIES + 3
        assert sorted(e["id"] for e in all_entries(slug)) == list(
            range(1, ROTATE_AT_ENTRIES + 4)
        )
        assert len(rotated_logs(slug)) == 1

    def test_rotate_if_needed_is_a_noop_for_a_missing_log(self, notes_dir, doc):
        assert rotate_if_needed("nothing-here") is None
        assert rotate_now("nothing-here") is None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class TestReading:
    def test_newest_first(self, notes_dir, doc):
        slug, _ = doc
        for _ in range(3):
            record(slug)
        assert [e["id"] for e in read_revisions(slug)] == [4, 3, 2, 1]

    def test_limit(self, notes_dir, doc):
        slug, _ = doc
        for _ in range(5):
            record(slug)
        assert [e["id"] for e in read_revisions(slug, limit=2)] == [6, 5]

    def test_limit_of_zero_returns_nothing(self, notes_dir, doc):
        slug, _ = doc
        record(slug)
        assert read_revisions(slug, limit=0) == []

    def test_since_id_is_exclusive(self, notes_dir, doc):
        slug, _ = doc
        for _ in range(4):
            record(slug)
        assert [e["id"] for e in read_revisions(slug, since_id=2)] == [5, 4, 3]

    def test_filter_by_author(self, notes_dir, doc):
        slug, _ = doc
        record(slug, author="user")
        record(slug, author="agent")
        record(slug, author="user")
        assert [e["id"] for e in read_revisions(slug, author="agent")] == [3]

    def test_filters_combine(self, notes_dir, doc):
        slug, _ = doc
        record(slug, author="user")
        record(slug, author="agent")
        record(slug, author="agent")
        assert [e["id"] for e in read_revisions(slug, author="agent", since_id=3)] == [
            4
        ]

    def test_reading_spans_rotated_logs(self, notes_dir, doc):
        """History must not appear to vanish the moment it rotates."""
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        drop_log(slug)
        append_entries(slug, ROTATE_AT_ENTRIES + 2)
        ids = sorted(e["id"] for e in read_revisions(slug))
        assert ids == list(range(1, ROTATE_AT_ENTRIES + 3))
        assert len(rotated_logs(slug)) >= 1

    def test_get_revision_finds_a_rotated_entry(self, notes_dir, doc):
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        drop_log(slug)
        append_entries(slug, ROTATE_AT_ENTRIES + 1)
        # Revision 1 is in a rotated file, and still addressable by id.
        assert get_revision(slug, 1)["id"] == 1
        assert get_revision(slug, ROTATE_AT_ENTRIES + 1)["id"] == ROTATE_AT_ENTRIES + 1

    def test_get_revision_missing_raises_with_guidance(self, notes_dir, doc):
        slug, _ = doc
        with pytest.raises(RevisionNotFoundError) as err:
            get_revision(slug, 99)
        assert "read_revisions" in str(err.value)

    def test_a_document_with_no_recorded_history_reads_as_empty(self, notes_dir, doc):
        slug, _ = doc
        drop_log(slug)
        assert read_revisions(slug) == []
        assert count_revisions(slug) == 0
        assert all_entries(slug) == []

    def test_reader_skips_blank_and_malformed_lines(self, notes_dir, doc):
        """A crash mid-append leaves a partial line; the rest must stay readable."""
        slug, _ = doc
        path = revisions_path(slug)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write("{not json\n")
            handle.write('{"id": 99, "author": "user", "ops": []}\n')

        entries = read_log_entries(path)
        # The baseline, and the valid appended line: blank and malformed skipped.
        assert [e["id"] for e in entries] == [1, 99]

    def test_reader_on_a_missing_file(self, tmp_path):
        assert read_log_entries(tmp_path / "nope.jsonl") == []

    def test_entries_with_a_malformed_id_do_not_break_ordering(self, notes_dir, doc):
        slug, _ = doc
        with open(revisions_path(slug), "a", encoding="utf-8") as handle:
            handle.write('{"id": "wat", "author": "user", "ops": []}\n')
        # Still readable, and the bad id sorts first rather than raising.
        assert len(all_entries(slug)) == 2
        assert all_entries(slug)[0]["id"] == "wat"

    def test_invalid_slug_is_refused(self, notes_dir):
        with pytest.raises(Exception):
            read_revisions("bad.slug")


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


class TestDiffOps:
    def _blocks(self, pairs):
        return [{"id": i, "type": t, "data": d} for i, t, d in pairs]

    def test_no_change_produces_no_ops(self):
        blocks = self._blocks([("a", "paragraph", {"text": "x"})])
        assert diff_ops(blocks, blocks) == []

    def test_add_is_detected_with_its_index(self):
        before = self._blocks([("a", "paragraph", {"text": "x"})])
        after = self._blocks(
            [("a", "paragraph", {"text": "x"}), ("b", "todo", {"text": "new"})]
        )
        ops = diff_ops(before, after)
        assert len(ops) == 1
        assert ops[0]["op"] == "add"
        assert ops[0]["block_id"] == "b"
        assert ops[0]["index"] == 1

    def test_remove_is_detected(self):
        before = self._blocks(
            [("a", "paragraph", {"text": "x"}), ("b", "paragraph", {"text": "y"})]
        )
        after = self._blocks([("a", "paragraph", {"text": "x"})])
        ops = diff_ops(before, after)
        assert [o["op"] for o in ops] == ["remove"]
        assert ops[0]["block_id"] == "b"

    def test_update_carries_the_new_data(self):
        before = self._blocks([("a", "paragraph", {"text": "x"})])
        after = self._blocks([("a", "paragraph", {"text": "y"})])
        ops = diff_ops(before, after)
        assert [o["op"] for o in ops] == ["update"]
        assert ops[0]["data"] == {"text": "y"}

    def test_type_change_counts_as_an_update(self):
        before = self._blocks([("a", "paragraph", {"text": "x"})])
        after = self._blocks([("a", "todo", {"text": "x"})])
        ops = diff_ops(before, after)
        assert ops[0]["op"] == "update"
        assert ops[0]["block_type"] == "todo"

    def test_reorder_is_a_single_move_not_a_delete_plus_add(self):
        """A pure reorder must not look like content changed."""
        before = self._blocks(
            [("a", "paragraph", {"text": "x"}), ("b", "paragraph", {"text": "y"})]
        )
        after = self._blocks(
            [("b", "paragraph", {"text": "y"}), ("a", "paragraph", {"text": "x"})]
        )
        ops = diff_ops(before, after)
        assert [o["op"] for o in ops] == ["move"]
        assert ops[0]["block_id"] == "b"

    def test_moving_the_last_block_to_the_front_is_one_move(self):
        before = self._blocks(
            [
                ("a", "paragraph", {"text": "1"}),
                ("b", "paragraph", {"text": "2"}),
                ("c", "paragraph", {"text": "3"}),
            ]
        )
        after = self._blocks(
            [
                ("c", "paragraph", {"text": "3"}),
                ("a", "paragraph", {"text": "1"}),
                ("b", "paragraph", {"text": "2"}),
            ]
        )
        ops = diff_ops(before, after)
        assert [o["op"] for o in ops] == ["move"]
        assert ops[0]["index"] == 0

    def test_every_op_that_positions_a_block_carries_an_index(self):
        """Without an index a replay could not rebuild order."""
        before = self._blocks([("a", "paragraph", {"text": "x"})])
        after = self._blocks(
            [("b", "todo", {"text": "n"}), ("a", "paragraph", {"text": "x"})]
        )
        for op in diff_ops(before, after):
            if op["op"] in ("add", "update", "move"):
                assert isinstance(op["index"], int)

    def test_block_snapshot_is_ordered_and_plain(self, notes_dir, doc):
        slug, _ = doc
        snapshot = block_snapshot(load_document(slug))
        assert [b["type"] for b in snapshot] == ["paragraph"] * 3
        assert [b["data"]["text"] for b in snapshot] == ["one", "two", "three"]
        assert all(set(b) == {"id", "type", "data"} for b in snapshot)


# ---------------------------------------------------------------------------
# Replay and revert
# ---------------------------------------------------------------------------


class TestReplay:
    def test_replay_rebuilds_the_current_document(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "edited"}, author="user")

        state = replay(slug)
        assert state.complete
        assert [b.id for b in state.blocks] == ids
        assert [b.data["text"] for b in state.blocks] == ["edited", "two", "three"]

    def test_replay_preserves_order_across_a_move(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            move_block(document, ids[2], after_block_id=None)

        state = replay(slug)
        assert [b.id for b in state.blocks] == [ids[0], ids[1], ids[2]]
        # And it agrees with what is actually on disk.
        assert [b.id for b in state.blocks] == [
            b.id for b in load_document(slug).blocks
        ]

    def test_replay_upto_excludes_the_target(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "edited"}, author="user")

        # Excluding revision 2 yields the state BEFORE that edit.
        state = replay(slug, upto_id=2)
        assert [b.data["text"] for b in state.blocks] == ["one", "two", "three"]

    def test_replay_of_an_empty_log_is_incomplete_not_empty(self, notes_dir, doc):
        """No history means unknown, which must not be presented as 'no blocks'."""
        slug, _ = doc
        drop_log(slug)
        state = replay(slug)
        assert state.blocks == []
        assert not state.complete
        assert state.reason

    def test_a_log_starting_after_revision_one_is_incomplete(self, notes_dir, doc):
        slug, _ = doc
        drop_log(slug)
        # Written directly: an anchor is what makes a gap recoverable, so going
        # through append_entry would repair the very state under test.
        write_damaged_log(
            slug,
            [
                make_entry(
                    revision_id=5,
                    author="user",
                    version_from=1,
                    version_to=2,
                    ops=[],
                    summary="synthetic",
                )
            ],
        )
        state = replay(slug)
        assert not state.complete
        # Names the boundary and says what IS recoverable, rather than a bare
        # "cannot be rebuilt".
        assert "earliest surviving revision is 5" in (state.reason or "")
        assert "anchor" in (state.reason or "")

    def test_state_before_raises_for_an_unknown_revision(self, notes_dir, doc):
        slug, _ = doc
        with pytest.raises(RevisionNotFoundError):
            state_before(slug, 42)

    def test_state_before_a_missing_log_is_incomplete(self, notes_dir, doc):
        slug, _ = doc
        drop_log(slug)
        write_damaged_log(
            slug,
            [
                make_entry(
                    revision_id=offset,
                    author="user",
                    version_from=1,
                    version_to=2,
                    ops=[],
                    summary="synthetic",
                )
                for offset in (3, 4)
            ],
        )
        state = state_before(slug, 4)
        assert not state.complete


class TestRevert:
    def _edit(self, slug, block_id, text):
        with locked_document(slug, None, author="user") as document:
            replace_block(document, block_id, data={"text": text}, author="user")

    def test_revert_restores_the_prior_state(self, notes_dir, doc):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")
        assert [b.data["text"] for b in load_document(slug).blocks][0] == "edited"

        revert_document(slug, 2, expected_version=2)

        assert [b.data["text"] for b in load_document(slug).blocks] == [
            "one",
            "two",
            "three",
        ]

    def test_revert_appends_rather_than_rewriting_history(self, notes_dir, doc):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")
        before = [dict(e) for e in all_entries(slug)]

        revert_document(slug, 2, expected_version=2)

        entries = all_entries(slug)
        assert len(entries) == len(before) + 1
        # Every prior entry is byte-for-byte unchanged.
        assert entries[: len(before)] == before

    def test_revert_entry_is_authored_system_and_marked(self, notes_dir, doc):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")

        _, entry = revert_document(slug, 2, expected_version=2)

        assert entry["author"] == "system"
        assert entry["ops"][0]["op"] == "revert"
        assert entry["ops"][0]["revision_id"] == 2

    def test_revert_bumps_the_version_exactly_once(self, notes_dir, doc):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")
        assert load_document(slug).meta.version == 2

        document, _ = revert_document(slug, 2, expected_version=2)
        assert document.meta.version == 3

    def test_revert_entry_records_the_block_ops_too(self, notes_dir, doc):
        """A replay must be able to follow a revert, so its ops are recorded."""
        slug, ids = doc
        self._edit(slug, ids[0], "edited")

        revert_document(slug, 2, expected_version=2)

        kinds = [op["op"] for op in all_entries(slug)[-1]["ops"]]
        assert "revert" in kinds
        assert "update" in kinds

    def test_a_revert_can_itself_be_replayed(self, notes_dir, doc):
        """Replaying the whole log must agree with the document on disk."""
        slug, ids = doc
        self._edit(slug, ids[0], "edited")
        revert_document(slug, 2, expected_version=2)

        state = replay(slug)
        assert [b.id for b in state.blocks] == ids
        assert [b.data["text"] for b in state.blocks] == ["one", "two", "three"]
        assert [b.id for b in state.blocks] == [
            b.id for b in load_document(slug).blocks
        ]

    def test_revert_restores_a_deleted_block_at_its_old_position(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            delete_block(document, ids[1])

        assert len(load_document(slug).blocks) == 2
        revert_document(slug, 2, expected_version=2)

        restored = load_document(slug)
        assert [b.id for b in restored.blocks] == ids
        assert [b.data["text"] for b in restored.blocks] == ["one", "two", "three"]

    def test_revert_removes_a_block_added_after_the_target(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            inserted = insert_block(
                document, block_type="todo", data={"text": "added"}, author="user"
            )

        assert len(load_document(slug).blocks) == 4
        revert_document(slug, 2, expected_version=2)

        restored = load_document(slug)
        assert [b.id for b in restored.blocks] == ids
        assert inserted.id not in {b.id for b in restored.blocks}

    def test_revert_refuses_a_stale_version(self, notes_dir, doc):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")

        with pytest.raises(StaleVersionError):
            revert_document(slug, 2, expected_version=1)
        # Nothing was written.
        assert load_document(slug).meta.version == 2

    def test_revert_of_an_unknown_revision(self, notes_dir, doc):
        slug, _ = doc
        with pytest.raises(RevisionNotFoundError):
            revert_document(slug, 99, expected_version=1)

    def test_revert_refuses_incomplete_history_by_default(self, notes_dir, doc):
        """A partial history must not silently produce a wrong revert."""
        slug, ids = doc
        drop_log(slug)
        write_damaged_log(
            slug,
            [
                make_entry(
                    revision_id=7,
                    author="user",
                    version_from=1,
                    version_to=2,
                    ops=[],
                    summary="synthetic",
                )
            ],
        )
        with pytest.raises(IncompleteHistoryError) as err:
            revert_document(slug, 7, expected_version=1)
        assert "earliest surviving revision is 7" in str(err.value)

    def test_revert_allows_partial_when_explicitly_permitted(self, notes_dir, doc):
        slug, ids = doc
        drop_log(slug)
        append_entries(slug, 1, start=7)
        document, entry = revert_document(
            slug, 7, expected_version=1, allow_partial=True
        )
        assert entry is not None
        assert document.meta.version == 2

    def test_revert_marks_the_document_last_author_system(self, notes_dir, doc):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")
        document, _ = revert_document(slug, 2, expected_version=2)
        assert document.meta.last_author == "system"

    def test_reverting_twice_to_the_same_point_is_idempotent_for_content(
        self, notes_dir, doc
    ):
        slug, ids = doc
        self._edit(slug, ids[0], "edited")
        revert_document(slug, 2, expected_version=2)
        revert_document(slug, 2, expected_version=3)

        restored = load_document(slug)
        assert [b.data["text"] for b in restored.blocks] == ["one", "two", "three"]
        # Each revert is its own recorded change: baseline, edit, two reverts.
        assert count_revisions(slug) == 4


# ---------------------------------------------------------------------------
# Defects and coverage gaps found in review
# ---------------------------------------------------------------------------


class TestReplayMatchesDiskForMixedChanges:
    """Replay must reproduce the document exactly, for EVERY shape of change.

    The recorded ops are the only description of a change, so if replay can
    disagree with what is on disk then a revert built on it writes the wrong
    order back. Each case below combines a structural change (add/remove/move)
    with another, because a single-kind change alone does not expose the
    ordering bug these exist to catch.
    """

    def _replay_matches_disk(self, slug):
        disk = [b.id for b in load_document(slug).blocks]
        state = replay(slug)
        assert state.complete
        assert [b.id for b in state.blocks] == disk
        return disk

    def test_delete_then_append(self, notes_dir, doc):
        """A removal makes every later add's index too high unless it is applied first."""
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            inserted = insert_block(
                document, block_type="paragraph", data={"text": "n"}, author="user"
            )
            delete_block(document, ids[0])

        assert self._replay_matches_disk(slug) == [ids[1], ids[2], inserted.id]

    def test_move_then_insert(self, notes_dir, doc):
        """A move's index is measured against the surviving list, not the old one."""
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            move_block(document, ids[2], after_block_id=None)
            insert_block(
                document,
                block_type="paragraph",
                data={"text": "n"},
                author="user",
                after_block_id=ids[1],
            )

        self._replay_matches_disk(slug)

    def test_delete_move_and_insert_together(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            delete_block(document, ids[1])
            move_block(document, ids[2], after_block_id=None)
            insert_block(
                document, block_type="paragraph", data={"text": "n"}, author="user"
            )

        self._replay_matches_disk(slug)

    def test_replace_and_move_and_delete_together(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[2], data={"text": "edited"}, author="user")
            delete_block(document, ids[0])
            move_block(document, ids[2], after_block_id=ids[1])

        disk = self._replay_matches_disk(slug)
        # And the content, not just the order, came back.
        replayed = replay(slug)
        texts = {b.id: b.data["text"] for b in replayed.blocks}
        on_disk = {b.id: b.data["text"] for b in load_document(slug).blocks}
        assert texts == on_disk
        assert len(disk) == 2

    def test_replay_is_correct_across_several_mixed_transactions(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            delete_block(document, ids[0])
            insert_block(
                document, block_type="paragraph", data={"text": "n1"}, author="user"
            )
        with locked_document(slug, 2, author="user") as document:
            move_block(document, ids[2], after_block_id=None)
            delete_block(document, ids[1])
        with locked_document(slug, 3, author="user") as document:
            insert_block(
                document, block_type="todo", data={"text": "n2"}, author="user"
            )

        self._replay_matches_disk(slug)

    def test_a_revert_after_a_mixed_change_restores_the_right_order(
        self, notes_dir, doc
    ):
        """The corruption this guards against: a revert writing the wrong order."""
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            delete_block(document, ids[0])
            insert_block(
                document, block_type="paragraph", data={"text": "n"}, author="user"
            )

        # Revert to the state before revision 2 -- the original three blocks.
        revert_document(slug, 2, expected_version=2)

        restored = load_document(slug)
        assert [b.id for b in restored.blocks] == ids
        assert [b.data["text"] for b in restored.blocks] == ["one", "two", "three"]
        # And replay agrees with what was actually written.
        assert [b.id for b in replay(slug).blocks] == ids


class TestEntryOpValues:
    """Ops must carry the right ids, data and indices, not merely the right shape."""

    def test_baseline_snapshot_carries_every_blocks_id_and_data(self, notes_dir, doc):
        slug, ids = doc
        ops = all_entries(slug)[0]["ops"]
        assert [op["block_id"] for op in ops] == ids
        assert [op["data"] for op in ops] == [
            {"text": "one"},
            {"text": "two"},
            {"text": "three"},
        ]

    def test_recorded_ops_name_the_blocks_that_actually_changed(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "a"}, author="user")
            replace_block(document, ids[2], data={"text": "c"}, author="user")

        entry = all_entries(slug)[-1]
        assert [op["block_id"] for op in entry["ops"]] == [ids[0], ids[2]]
        assert [op["data"] for op in entry["ops"]] == [{"text": "a"}, {"text": "c"}]
        # The untouched block produces no op.
        assert ids[1] not in [op["block_id"] for op in entry["ops"]]

    def test_an_add_op_carries_its_final_index_and_payload(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            insert_block(
                document,
                block_type="todo",
                data={"text": "fresh"},
                author="user",
                after_block_id=ids[0],
            )

        adds = [op for op in all_entries(slug)[-1]["ops"] if op["op"] == "add"]
        assert len(adds) == 1
        assert adds[0]["index"] == 1
        assert adds[0]["block_type"] == "todo"
        # The stored data is the validated model's, so a defaulted field is
        # present rather than absent.
        assert adds[0]["data"] == {"text": "fresh", "checked": False}

    def test_a_single_drag_is_recorded_as_one_move(self, notes_dir, doc):
        """The common case is described by ONE op, not a delete plus an add.

        The op names the block that moved INTO the vacated position rather than
        the block that left it. Both descriptions reach the same result, and
        which is reported is not observable to a user; that it is a single move
        rather than a removal and an insertion is.
        """
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            move_block(document, ids[1], after_block_id=None)

        ops = all_entries(slug)[-1]["ops"]
        assert [op["op"] for op in ops] == ["move"]
        assert ops[0]["block_id"] == ids[2]
        assert ops[0]["index"] == 1
        assert [b.id for b in replay(slug).blocks] == [ids[0], ids[2], ids[1]]

    def test_moving_the_first_block_to_the_end_may_report_more_than_one_move(
        self, notes_dir, doc
    ):
        """Correctness first: the ops describe the change, which may be several moves.

        Rotating three blocks can be expressed as either two moves or one, and
        this implementation picks the former. What must hold is that a replay
        reproduces the document exactly; the op count is a presentation detail.
        """
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            move_block(document, ids[0], after_block_id=None)

        ops = all_entries(slug)[-1]["ops"]
        assert all(op["op"] == "move" for op in ops)
        assert [b.id for b in replay(slug).blocks] == [ids[1], ids[2], ids[0]]
        assert [b.id for b in replay(slug).blocks] == [
            b.id for b in load_document(slug).blocks
        ]

    def test_a_remove_op_names_the_removed_block(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            delete_block(document, ids[1])

        ops = all_entries(slug)[-1]["ops"]
        assert [op["op"] for op in ops] == ["remove"]
        assert ops[0]["block_id"] == ids[1]

    def test_revert_entry_carries_the_restored_content(self, notes_dir, doc):
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "edited"}, author="user")

        revert_document(slug, 2, expected_version=2)

        updates = [op for op in all_entries(slug)[-1]["ops"] if op["op"] == "update"]
        assert len(updates) == 1
        assert updates[0]["block_id"] == ids[0]
        assert updates[0]["data"] == {"text": "one"}
        assert updates[0]["index"] == 0


class TestCounterSurvivesRotation:
    def test_ids_stay_monotonic_across_a_rotation_driven_by_real_mutations(
        self, notes_dir, doc
    ):
        """The counter lives in meta, so rotating the log cannot reset it.

        Driven through real mutations, not by appending synthetic entries: only
        record_revision allocates ids, so a test that supplies its own ids proves
        nothing about the counter.
        """
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        for _ in range(ROTATE_AT_ENTRIES + 2):
            record(slug)

        assert rotated_logs(slug), "the log should have rotated"
        ids = [e["id"] for e in all_entries(slug)]
        # The fixture's creation entry plus every change, strictly increasing,
        # with no reset and no gap.
        assert ids == list(range(1, ROTATE_AT_ENTRIES + 4))
        assert load_document(slug).meta.next_revision_id == ROTATE_AT_ENTRIES + 4

    def test_counter_in_meta_is_never_behind_the_log(self, notes_dir, doc):
        """The document (and its counter) is written before the entry is appended."""
        slug, _ = doc
        for _ in range(6):
            record(slug)

        next_id = load_document(slug).meta.next_revision_id
        max_logged = max(e["id"] for e in all_entries(slug))
        # The counter is ahead of (or level with) the log, never behind it. An
        # entry whose id the counter would hand out again is the failure this
        # ordering exists to prevent.
        assert next_id > max_logged


class TestRotationCollisionHandling:
    def test_two_rotations_in_the_same_instant_do_not_collide(
        self, notes_dir, doc, monkeypatch
    ):
        """Force the stamp to repeat, so the collision branch is actually taken."""
        from wichy.tools.notes import revisions as rev

        slug, _ = doc
        drop_log(slug)
        append_entries(slug, 1)
        monkeypatch.setattr(rev, "_rotation_stamp", lambda: "20260101T000000000000")
        first = rotate_now(slug)

        append_entries(slug, 1, start=2)
        second = rotate_now(slug)

        assert first is not None and second is not None
        assert first != second
        assert second.name.endswith("-1.jsonl")
        # Neither rotation destroyed the other's history.
        assert sorted(e["id"] for e in all_entries(slug)) == [1, 2]


class TestReaderTolerance:
    def test_a_valid_json_non_object_line_is_skipped(self, notes_dir, doc):
        """A bare array or number is valid JSON but cannot be a revision."""
        slug, _ = doc
        with open(revisions_path(slug), "a", encoding="utf-8") as handle:
            handle.write("[1, 2]\n")
            handle.write("5\n")
            handle.write('"a string"\n')

        # Still readable, rather than raising on the first non-object line.
        assert [e["id"] for e in all_entries(slug)] == [1]

    def test_an_unreadable_log_reports_rather_than_reading_as_empty(
        self, notes_dir, doc, monkeypatch
    ):
        """'No history' and 'history I could not open' are different answers."""
        slug, _ = doc
        path = revisions_path(slug)

        def boom(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(type(path), "read_text", boom)
        with pytest.raises(OSError):
            read_log_entries(path)

    def test_blank_lines_only_reads_as_empty(self, notes_dir, doc):
        slug, _ = doc
        path = revisions_path(slug)
        path.write_text("\n\n\n", encoding="utf-8")
        assert read_log_entries(path) == []


class TestRotationSizing:
    def test_rotation_counts_only_the_live_log(self, notes_dir, doc):
        """Counting rotated entries too would rotate on nearly every append."""
        from wichy.tools.notes.revisions import ROTATE_AT_ENTRIES

        slug, _ = doc
        drop_log(slug)
        append_entries(slug, ROTATE_AT_ENTRIES + 3)

        # One rotated file for the first ROTATE_AT_ENTRIES, then the remainder
        # live. Counting rotated entries would rotate after every append.
        assert len(rotated_logs(slug)) == 1
        assert len(read_log_entries(revisions_path(slug))) == 3


class TestRevertReturnsItsOwnEntry:
    def test_the_returned_entry_is_the_revert_not_a_stale_one(self, notes_dir, doc):
        """Reading the log back after the lock would race; the entry is captured inside."""
        slug, ids = doc
        with locked_document(slug, 1, author="user") as document:
            replace_block(document, ids[0], data={"text": "edited"}, author="user")

        document, entry = revert_document(slug, 2, expected_version=2)

        assert entry["author"] == "system"
        assert entry["ops"][0]["op"] == "revert"
        assert entry["id"] == all_entries(slug)[-1]["id"]
        assert document.meta.version == 3

    def test_partial_revert_actually_empties_the_document(self, notes_dir, doc):
        slug, ids = doc
        drop_log(slug)
        append_entries(slug, 1, start=7)

        document, entry = revert_document(
            slug, 7, expected_version=1, allow_partial=True
        )

        # The state before revision 7 is not recorded, so the target is empty and
        # the revert really does empty the document -- not a no-op.
        assert [b.id for b in document.blocks] == []
        assert entry["author"] == "system"


class TestInvalidSlugIsRefused:
    def test_reader_refuses_an_invalid_slug_by_type(self, notes_dir):
        from wichy.tools.notes.blocks import InvalidSlugError

        with pytest.raises(InvalidSlugError):
            read_revisions("bad.slug")
        with pytest.raises(InvalidSlugError):
            all_entries("bad.slug")
        with pytest.raises(InvalidSlugError):
            count_revisions("bad.slug")
        with pytest.raises(InvalidSlugError):
            rotated_logs("bad.slug")


class TestWriteOrderPreventsDuplicateIds:
    """The document (carrying the counter) must be written BEFORE the entry.

    The two orders fail differently, and only one is recoverable:

    - Entry first: a crash in between leaves an entry whose id the counter will
      hand out again, so the next change writes a SECOND entry with the same id.
      Duplicate ids make get_revision and since_id ambiguous and make a replay
      apply one change twice.
    - Document first: a crash leaves a gap. Ids stay unique; the log is merely
      missing one record.

    Simulated here by failing the DOCUMENT write once. That is the case that
    distinguishes the two orders: if the entry has already been appended when
    the document write fails, the counter on disk is still the old value, so the
    next change allocates the same id again.
    """

    def test_a_failed_document_write_does_not_produce_a_reused_id(
        self, notes_dir, doc, monkeypatch
    ):
        slug, ids = doc
        from wichy.tools.notes import blocks as blocks_mod

        real_save = blocks_mod.save_document
        calls = {"n": 0}

        def flaky_save(document):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("simulated failure writing the document")
            return real_save(document)

        monkeypatch.setattr(blocks_mod, "save_document", flaky_save)

        with pytest.raises(OSError):
            with locked_document(slug, 1, author="user") as document:
                replace_block(document, ids[0], data={"text": "first"}, author="user")

        # The document still holds the pre-change counter, because its write
        # failed. Any id that reached the log is now unreachable by the counter.
        counter = load_document(slug).meta.next_revision_id
        logged_after_failure = [e["id"] for e in all_entries(slug)]

        # Now make the next change with the counter's own value.
        with locked_document(slug, None, author="user") as document:
            replace_block(document, ids[1], data={"text": "second"}, author="user")

        ids_logged = [e["id"] for e in all_entries(slug)]
        assert len(ids_logged) == len(set(ids_logged)), (
            f"an id was reused: counter was {counter}, "
            f"log after the failed write was {logged_after_failure}"
        )

    def test_the_counter_is_never_behind_the_log(self, notes_dir, doc, monkeypatch):
        """Whatever the write order, a reissued id is the thing to forbid."""
        slug, ids = doc
        for _ in range(3):
            with locked_document(slug, None, author="user") as document:
                replace_block(document, ids[0], data={"text": "x"}, author="user")

        logged = [e["id"] for e in all_entries(slug)]
        assert logged == sorted(set(logged))
        assert load_document(slug).meta.next_revision_id > max(logged)


class TestAppendOnlyHistory:
    """History is never trimmed: the only removal is deleting the note."""

    def test_history_is_append_only(self, notes_dir, doc):
        """120 states is past any former cap, and rotation happens on the way."""
        slug, _ = doc
        drop_log(slug)
        append_entries(slug, 120)

        entries = all_entries(slug)
        assert [e["id"] for e in entries] == list(range(1, 121))
        assert rotated_logs(slug), "the log should have rotated"

    def test_rotation_preserves_history(self, notes_dir, doc):
        """Rotating the file aside must not change what is readable."""
        slug, _ = doc
        drop_log(slug)
        append_entries(slug, 3)
        before_ids = [e["id"] for e in all_entries(slug)]

        assert rotate_now(slug) is not None
        append_entries(slug, 1, start=4)
        assert rotate_now(slug) is not None

        # Every id recorded before the rotations is still readable, in order.
        after_ids = [e["id"] for e in all_entries(slug)]
        assert after_ids[: len(before_ids)] == before_ids
        assert len(rotated_logs(slug)) >= 2

    def test_reverts_survive_indefinitely(self, notes_dir):
        """A revert is part of the audit trail and is never trimmed away."""
        document = create_document(
            "Survives",
            [
                {"type": "paragraph", "data": {"text": "one"}},
                {"type": "paragraph", "data": {"text": "two"}},
            ],
        )
        slug = document.meta.slug
        target = document.blocks[0].id
        with locked_document(slug, None, author="user") as open_document:
            replace_block(open_document, target, data={"text": "v1"}, author="user")

        revert_document(slug, 2, expected_version=load_document(slug).meta.version)

        for _ in range(60):
            record(slug)

        reverts = [
            e
            for e in all_entries(slug)
            if e.get("author") == "system"
            and not e.get("baseline")
            and any(op.get("op") == "revert" for op in e.get("ops", []))
        ]
        assert reverts, "the revert record must survive later edits"
        assert replay(slug).complete is True
        assert replay_matches_document(slug) is True

    def test_delete_is_the_only_history_remover(self, notes_dir):
        """Revision files, live and rotated, go only when the note does."""
        document = create_document(
            "Gone", [{"type": "paragraph", "data": {"text": "x"}}]
        )
        slug = document.meta.slug
        append_entries(slug, 60)
        assert revisions_path(slug).exists()
        assert rotated_logs(slug), "the note should have rotated logs"

        delete_document_files(slug)

        assert not revisions_path(slug).exists()
        assert rotated_logs(slug) == []

    def test_append_is_durable(self, notes_dir, doc, monkeypatch):
        """An acknowledged append is fsynced, so a crash cannot lose it."""
        slug, _ = doc
        calls = {"n": 0}
        real_fsync = os.fsync

        def counting_fsync(fd):
            calls["n"] += 1
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", counting_fsync)
        append_entry(
            slug,
            make_entry(
                revision_id=99,
                author="user",
                version_from=1,
                version_to=2,
                ops=[],
                summary="durable",
            ),
        )
        assert calls["n"] >= 1, "an acknowledged append must be fsynced"

    def test_legacy_anchor_still_recognised_in_replay(self, notes_dir):
        """An older build may have written an anchor; replay must still trust it."""
        document = create_document(
            "Legacy", [{"type": "paragraph", "data": {"text": "v0"}}]
        )
        slug = document.meta.slug
        block_id = document.blocks[0].id
        for index in range(2):
            with locked_document(slug, None, author="user") as open_document:
                replace_block(
                    open_document,
                    block_id,
                    data={"text": f"v{index + 1}"},
                    author="user",
                )

        # State as it stood BEFORE revision 3, restated as an anchor with id 2.
        before_three = replay(slug, upto_id=3)
        anchor_ops = [
            {
                "op": "add",
                "block_id": block.id,
                "block_type": block.type,
                "data": dict(block.data),
                "index": index,
            }
            for index, block in enumerate(before_three.blocks)
        ]
        anchor = {
            "id": 2,
            "timestamp": "2026-01-01T00:00:00Z",
            "author": "system",
            "baseline": True,
            "version_from": 0,
            "version_to": 0,
            "ops": anchor_ops,
            "summary": (
                "History starts here. Earlier revisions were dropped; the "
                "document can be rebuilt from this point on."
            ),
        }
        survivor = [e for e in all_entries(slug) if e["id"] == 3]
        revisions_path(slug).write_text(
            "".join(json.dumps(e) + "\n" for e in [anchor, *survivor]),
            encoding="utf-8",
        )

        # Without anchor recognition this log starts at id 2, not 1, and would
        # be reported incomplete.
        state = replay(slug)
        assert state.complete is True
        assert all_entries(slug)[0].get("baseline") is True

        # A real append after the hand-written anchor, as a later build would do.
        record(slug)
        state = replay(slug)
        assert state.complete is True
        assert [(b.id, b.data) for b in state.blocks] == [
            (b.id, b.data) for b in load_document(slug).blocks
        ]


class TestRevertToALegacyAnchorIsRefused:
    """A legacy anchor's preceding state was never recorded, so it is refused.

    An anchor reuses a real id and restates the state where the old build's
    recorded history begins. Reverting to that id asks for the state BEFORE it,
    which the log never held: replay breaks at the anchor as its first entry and
    reports an empty document. Writing that back would silently destroy the note.
    """

    def _legacy_anchor(self, notes_dir):
        document = create_document(
            "Anchored", [{"type": "paragraph", "data": {"text": "v0"}}]
        )
        slug = document.meta.slug
        block_id = document.blocks[0].id
        for index in range(2):
            with locked_document(slug, None, author="user") as open_document:
                replace_block(
                    open_document,
                    block_id,
                    data={"text": f"v{index + 1}"},
                    author="user",
                )
        # Anchor at id 2 restating the state before revision 3; revision 3 stays.
        anchor = write_legacy_anchor_log(slug, anchor_id=2, keep_ids=[3])
        return slug, anchor

    def test_reverting_to_a_legacy_anchor_is_refused(self, notes_dir):
        slug, anchor = self._legacy_anchor(notes_dir)
        before = load_document(slug)
        log_before = len(all_entries(slug))
        assert replay_matches_document(slug) is True

        with pytest.raises(HistoryAnchorError) as raised:
            revert_document(slug, anchor["id"], expected_version=before.meta.version)

        # The message names the anchor and points at the supported alternative.
        message = str(raised.value)
        assert "history anchor" in message
        assert "at=True" in message

        # Nothing changed: same blocks, same version, no revert entry written.
        after = load_document(slug)
        assert [(b.id, b.data) for b in after.blocks] == [
            (b.id, b.data) for b in before.blocks
        ]
        assert after.meta.version == before.meta.version
        assert len(all_entries(slug)) == log_before
        assert replay_matches_document(slug) is True

    def test_restoring_at_a_legacy_anchor_still_works(self, notes_dir):
        """Restoring AT the anchor is a different request, and it is supported."""
        slug, anchor = self._legacy_anchor(notes_dir)
        before = load_document(slug)

        restored, entry = restore_document(
            slug, anchor["id"], expected_version=before.meta.version, at=True
        )

        # The anchor restates the state before revision 3, so restoring at it
        # rebuilds that state and records a new system revision.
        assert [(b.id, b.data) for b in restored.blocks] == [
            (b.id, b.data) for b in replay(slug, upto_id=3).blocks
        ]
        assert entry["author"] == "system"
        assert replay_matches_document(slug) is True


class TestAMiddleGapIsIncomplete:
    """An entry lost in the MIDDLE breaks every state rebuilt after it.

    Completeness used to be positional only: ``first_id == 1`` was enough, so a
    log whose ids ran [1, 2, 4, 5] -- entry 3 deleted by hand or by an older
    build -- still replayed as authoritative. The replay silently omitted
    revision 3's change, and the rebuilt state was served as the document's past.
    A missing id between surviving ones is what a first-id check cannot see.
    """

    def _gap_log(self, notes_dir, doc):
        """A five-revision log with the middle entry (id 3) removed by hand."""
        slug, ids = doc
        for text in ("a", "b", "c", "d"):
            with locked_document(slug, None, author="user") as document:
                replace_block(document, ids[0], data={"text": text}, author="user")

        path = revisions_path(slug)
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [row["id"] for row in rows] == [1, 2, 3, 4, 5]
        rows = [row for row in rows if row["id"] != 3]
        write_damaged_log(slug, rows)
        assert [e["id"] for e in all_entries(slug)] == [1, 2, 4, 5]
        return slug

    def test_a_gap_in_the_middle_is_reported_incomplete(self, notes_dir, doc):
        slug = self._gap_log(notes_dir, doc)

        # A revision BEFORE the gap replays everything up to that point, which is
        # untouched history, so it is still complete.
        before = replay(slug, upto_id=3)
        assert before.complete is True
        assert before.reason is None
        assert before.has_gap is False

        # A revision AFTER the gap was rebuilt without revision 3's change.
        after = replay(slug, upto_id=5)
        assert after.complete is False
        assert after.has_gap is True
        # The reason names the gap in plain words rather than a bare "incomplete".
        assert "missing revision 3" in (after.reason or "")
        assert "between 2 and 4" in (after.reason or "")

    def test_restore_from_a_gap_is_refused(self, notes_dir, doc):
        slug = self._gap_log(notes_dir, doc)
        version = load_document(slug).meta.version
        log_before = len(all_entries(slug))
        blocks_before = [(b.id, b.data) for b in load_document(slug).blocks]

        with pytest.raises(IncompleteHistoryError) as raised:
            restore_document(slug, 5, expected_version=version, at=True)

        # The message names the missing revision and says it cannot be rebuilt.
        message = str(raised.value)
        assert "missing revision 3" in message
        assert "cannot be rebuilt exactly" in message

        # The refusal wrote NOTHING: no revert entry, live document untouched.
        assert len(all_entries(slug)) == log_before
        assert [(b.id, b.data) for b in load_document(slug).blocks] == blocks_before
        assert load_document(slug).meta.version == version

    def test_restore_from_a_gap_succeeds_when_partial_is_permitted(
        self, notes_dir, doc
    ):
        slug = self._gap_log(notes_dir, doc)
        version = load_document(slug).meta.version
        log_before = len(all_entries(slug))

        document, entry = restore_document(
            slug, 5, expected_version=version, at=True, allow_partial=True
        )

        assert entry is not None
        assert document.meta.version == version + 1
        # The explicit opt-in is the only path that writes the incomplete past.
        assert len(all_entries(slug)) == log_before + 1

    def test_an_anchor_is_not_a_gap(self, notes_dir):
        """A legacy anchor restates the state there, so contiguous ids are whole.

        The Stage-1 semantics: an anchor is the rebuilt starting point, not a
        missing entry, so ids continuing A, A+1, A+2 ... from it are contiguous
        and the state replays exactly. A missing entry AFTER an anchor would
        still be a gap; an anchor itself is not.
        """
        document = create_document(
            "Anchored", [{"type": "paragraph", "data": {"text": "v0"}}]
        )
        slug = document.meta.slug
        block_id = document.blocks[0].id
        for index in range(3):
            with locked_document(slug, None, author="user") as open_document:
                replace_block(
                    open_document,
                    block_id,
                    data={"text": f"v{index + 1}"},
                    author="user",
                )
        # Anchor at id 2 restating the state before revision 3; ids 3 and 4 stay,
        # so the walk from the anchor is 2, 3, 4 -- contiguous, no gap.
        anchor = write_legacy_anchor_log(slug, anchor_id=2, keep_ids=[3, 4])
        assert [e["id"] for e in all_entries(slug)] == [2, 3, 4]

        state = replay(slug)
        assert state.complete is True
        assert state.has_gap is False
        assert state.reason is None
        assert all_entries(slug)[0]["id"] == anchor["id"]

        # A real append after the anchor keeps the walk contiguous.
        record(slug)
        after = replay(slug)
        assert after.complete is True
        assert after.has_gap is False


class TestTornLogTailDisclosure:
    """A torn FINAL line of the LIVE log is disclosed, not silently dropped.

    Tolerant parsing made history simply end earlier when a crash or a full disk
    left the last append half-written: no missing id in the middle, so the gap
    check could not see it either. The READER now refuses, naming the file and
    line, while rotated logs stay tolerant.
    """

    def test_a_torn_live_log_tail_is_disclosed(self, notes_dir, doc):
        slug, _ = doc
        path = revisions_path(slug)
        # A real baseline entry exists; then a crash mid-append leaves a half
        # line with NO trailing newline.
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"id": 99, "ops": [')

        with pytest.raises(CorruptRevisionLogError) as excinfo:
            all_entries(slug)
        message = str(excinfo.value)
        assert path.name in message
        # Line 2: the baseline is line 1, the torn half-line is line 2.
        assert "line 2" in message
        assert "crash" in message

        # The note file itself is untouched and still readable.
        assert load_document(slug).meta.slug == slug

    def test_a_missing_trailing_newline_is_also_disclosed(self, notes_dir, doc):
        """A last line that still parses can be torn too: only its tail is gone."""
        slug, _ = doc
        path = revisions_path(slug)
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.rstrip("\n"), encoding="utf-8")

        with pytest.raises(CorruptRevisionLogError):
            read_log_entries(path, live=True)

    def test_rotated_logs_stay_tolerant(self, notes_dir, doc):
        """Immutable once rotated: a torn line there predates the rotation."""
        slug, _ = doc
        append_entries(slug, 3, start=2)
        rotated = rotate_now(slug)
        assert rotated is not None
        # Corrupt the FIRST (middle) line of the rotated file, by hand.
        rows = rotated.read_text(encoding="utf-8").splitlines()
        rows[0] = "{not json"
        rotated.write_text("\n".join(rows) + "\n", encoding="utf-8")

        # Reading the live path still returns the rotated survivors, no raise.
        ids = [e["id"] for e in all_entries(slug)]
        assert ids == [2, 3, 4]


class TestBrowsingSurvivesATornTail:
    """A torn live-log tail must not cost the user their readable history.

    Browsing and restoring are different jobs. Restoring a state that cannot
    be rebuilt exactly must refuse; BROWSING must still show the entries that
    are intact, with the reason the newest ones are absent, because a browse
    that blanks on one bad line destroys the very history it exists to show.
    """

    def test_browse_keeps_the_readable_history_with_a_warning(self, notes_dir, doc):
        slug, _ = doc
        append_entries(slug, 3, start=2)
        rotated = rotate_now(slug)
        assert rotated is not None
        append_entries(slug, 2, start=5)
        # A crash mid-append leaves a half line with no trailing newline.
        with open(revisions_path(slug), "a", encoding="utf-8") as handle:
            handle.write('{"id": 99, "ops": [')

        entries, warning = browse_entries(slug)

        # The rotated history survives; only the torn live tail is missing.
        assert [e["id"] for e in entries] == [1, 2, 3, 4, 5, 6]
        assert warning is not None
        assert "mid-entry" in warning
        assert "crash" in warning

        # The strict read still refuses: restoring must never see a torn log
        # as ordinary history.
        with pytest.raises(CorruptRevisionLogError):
            all_entries(slug)

    def test_browse_is_silent_when_the_log_is_intact(self, notes_dir, doc):
        slug, _ = doc
        append_entries(slug, 3, start=2)

        entries, warning = browse_entries(slug)

        assert [e["id"] for e in entries] == [1, 2, 3, 4]
        assert warning is None


class TestReplayDoesNotInventGaps:
    """The contiguity walk checks the log, never the caller's reach.

    An explicit target past the newest surviving entry is not evidence of
    missing history: the walk must stop at what exists, or replaying a
    perfectly intact log would report a gap out of ids the log never had.
    """

    def test_replaying_past_the_newest_entry_reports_no_gap(self, notes_dir, doc):
        slug, _ = doc
        append_entries(slug, 3, start=2)

        state = replay(slug, upto_id=999)

        assert state.has_gap is False
        assert state.complete is True
        assert state.reason is None

    def test_a_real_gap_is_still_reported(self, notes_dir, doc):
        slug, _ = doc
        append_entries(slug, 3, start=2)
        # Delete the middle entry by hand: damage an older build could leave.
        rows = revisions_path(slug).read_text(encoding="utf-8").splitlines()
        keep = [row for row in rows if '"id": 3' not in row]
        revisions_path(slug).write_text("\n".join(keep) + "\n", encoding="utf-8")

        state = replay(slug, upto_id=4)

        assert state.has_gap is True
        assert state.complete is False
        assert "revision 3" in (state.reason or "")
