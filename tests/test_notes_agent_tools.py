"""Tests for the agent's block tools.

The tools are the agent's only way into a document, and they act on the pinned
scratchpad alone -- there is no slug parameter, so a tool cannot reach a note the
user has not opened. That, and the two normal refusals (nothing pinned, and a
markdown-format scratchpad), are what most of this file defends.

Grouped by what each group defends:

- the refusal states, which are normal and must be identical across all tools
- read_blocks: rendering, filtering, and the argument combinations it rejects
- the four mutating tools: what each returns, and that it writes what it says
- validation: invalid data is refused with a message naming the schema
- output format: the rendering rules from the appendix
- the registry: the six tools are present and `write_scratchpad` is gone
"""

from __future__ import annotations

import json

import pytest

from wichy.config import settings
from wichy.tools.notes import set_scratchpad_state
from wichy.tools.notes.agent_tools import (
    NO_SCRATCHPAD,
    AnswerQuestionTool,
    DeleteBlockTool,
    InsertBlockTool,
    MoveBlockTool,
    ReadBlocksTool,
    ReadRevisionsTool,
    WriteBlockTool,
    ChangeBlockTypeTool,
    render_data,
)
from wichy.tools.notes.agent_tools import FindBlockIdsTool, GetBlockTool
from wichy.tools.notes.blocks import (
    MARKDOWN_WRITE_REFUSED,
    create_document,
    load_document,
    locked_document,
    replace_block,
    revisions_path,
    save_document,
)
from wichy.tools.notes.state import reset_state
from wichy.tools.read_scratchpad import ReadScratchpadTool

BLOCK_TOOLS = [
    ReadBlocksTool,
    WriteBlockTool,
    ChangeBlockTypeTool,
    InsertBlockTool,
    DeleteBlockTool,
    MoveBlockTool,
    AnswerQuestionTool,
    ReadRevisionsTool,
    GetBlockTool,
    FindBlockIdsTool,
    ReadScratchpadTool,
]


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
def scratchpad(notes_dir):
    """A pinned scratchpad document with three blocks."""
    document = create_document(
        "Scratch",
        [
            {"type": "header", "data": {"text": "Title", "level": 2}},
            {"type": "paragraph", "data": {"text": "some text"}},
            {"type": "todo", "data": {"text": "a task"}},
        ],
    )
    set_scratchpad_state(document.meta.slug)
    return document.meta.slug, [b.id for b in document.blocks]


def run(tool_class, **kwargs):
    """Execute a tool and return its string result."""
    return tool_class().execute(**kwargs)


# ---------------------------------------------------------------------------
# The refusal states
# ---------------------------------------------------------------------------


class TestNoScratchpadPinned:
    """Nothing pinned is the ordinary startup state, not an error."""

    @pytest.mark.parametrize("tool", BLOCK_TOOLS)
    def test_every_tool_refuses_identically(self, notes_dir, tool):
        assert run(tool) == NO_SCRATCHPAD

    def test_read_scratchpad_refuses_the_same_way(self, notes_dir):
        assert run(ReadScratchpadTool) == NO_SCRATCHPAD

    def test_a_read_tool_writes_nothing(self, notes_dir):
        before = sorted(p.name for p in notes_dir.iterdir())
        run(ReadBlocksTool)
        assert sorted(p.name for p in notes_dir.iterdir()) == before

    def test_a_write_tool_writes_nothing(self, notes_dir):
        before = sorted(p.name for p in notes_dir.iterdir())
        run(InsertBlockTool, block_type="todo", new_content="x")
        assert sorted(p.name for p in notes_dir.iterdir()) == before

    def test_the_message_says_what_to_do(self, notes_dir):
        """An error that does not say how to fix it leaves the agent stuck."""
        assert "Pin a note" in NO_SCRATCHPAD

    def test_a_pin_to_a_missing_document_is_reported_not_crashed(self, notes_dir):
        set_scratchpad_state("ghost-doc")
        result = run(ReadBlocksTool)
        assert "ghost-doc" in result
        assert "no longer exists" in result

    def test_an_invalid_pin_name_is_reported(self, notes_dir):
        from wichy.tools.notes import set_scratchpad_state as set_state

        set_state("bad.slug")
        result = run(ReadBlocksTool)
        assert "not a valid note name" in result
        # And it does not pretend the scratchpad is empty.
        assert result != NO_SCRATCHPAD


class TestMarkdownScratchpad:
    """A markdown-format scratchpad has no block write path."""

    @pytest.fixture
    def markdown_pinned(self, notes_dir):
        (notes_dir / "legacy.md").write_text(
            "---\ntitle: Legacy\n---\n# H\n\nbody\n", encoding="utf-8"
        )
        set_scratchpad_state("legacy")
        return "legacy"

    @pytest.mark.parametrize(
        "tool,kwargs",
        [
            (
                WriteBlockTool,
                {"block_id": "blk-x", "new_content": "x"},
            ),
            (InsertBlockTool, {"block_type": "paragraph", "new_content": "x"}),
            (DeleteBlockTool, {"block_id": "blk-x"}),
            (MoveBlockTool, {"block_id": "blk-x"}),
            (AnswerQuestionTool, {"block_id": "blk-x"}),
        ],
    )
    def test_every_write_tool_returns_the_conversion_message(
        self, markdown_pinned, tool, kwargs
    ):
        """A write against a legacy .md would give one slug two documents."""
        assert run(tool, **kwargs) == MARKDOWN_WRITE_REFUSED

    def test_read_blocks_yields_the_content(self, markdown_pinned):
        """A note the agent cannot EDIT is still one it should be able to READ.

        Refusing a read with the write-refusal sentence told the agent nothing
        about a document that is present and readable, while read_scratchpad
        showed the same note's content -- two read tools disagreeing about
        whether the note exists.
        """
        result = run(ReadBlocksTool)
        assert result != MARKDOWN_WRITE_REFUSED
        assert "# H" in result
        assert "body" in result

    def test_read_revisions_reports_that_there_are_none(self, markdown_pinned):
        """A legacy note keeps no revision log, which is the honest answer.

        The write-refusal sentence hid that: it named a conversion instead of the
        real reason, and read like a failed WRITE from a read tool.
        """
        result = run(ReadRevisionsTool)
        assert result != MARKDOWN_WRITE_REFUSED
        assert "No revisions recorded" in result

    def test_the_message_matches_the_one_the_api_uses(self, markdown_pinned):
        """One explanation, not two: the user sees whichever surface they used."""
        assert run(DeleteBlockTool, block_id="x") == MARKDOWN_WRITE_REFUSED

    def test_a_write_tool_makes_no_json(self, notes_dir, markdown_pinned):
        run(InsertBlockTool, block_type="paragraph", new_content="x")
        assert not (notes_dir / "legacy.json").exists()

    def test_read_scratchpad_explains_rather_than_reporting_empty(
        self, markdown_pinned
    ):
        result = run(ReadScratchpadTool)
        assert "markdown" in result.lower()
        # It still shows the content, so the agent is not left blind.
        assert "# H" in result


# ---------------------------------------------------------------------------
# read_blocks
# ---------------------------------------------------------------------------


class TestReadBlocks:
    def test_reads_all_blocks_with_metadata(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadBlocksTool)
        assert "[Scratchpad: Scratch | version 1 | 3 blocks]" in result
        for block_id in ids:
            assert block_id in result

    def test_renders_each_block_type(self, scratchpad):
        result = run(ReadBlocksTool)
        assert "## Title" in result
        assert "some text" in result
        assert "[TODO unchecked] a task" in result

    def test_filter_by_type(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadBlocksTool, filter_type="todo")
        assert ids[2] in result
        assert ids[1] not in result

    def test_filter_by_type_still_reports_the_document_total(self, scratchpad):
        """So a filtered read is distinguishable from a small document."""
        result = run(ReadBlocksTool, filter_type="todo")
        assert "3 blocks" in result

    def test_read_one_block_by_id(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadBlocksTool, block_id=ids[1])
        assert "some text" in result
        assert "## Title" not in result

    def test_unknown_block_id(self, scratchpad):
        assert "No block" in run(ReadBlocksTool, block_id="blk-nope")

    def test_index_range_is_inclusive_at_both_ends(self, scratchpad):
        """Both bounds name blocks to INCLUDE, as the schema documents.

        A caller reading a block by index and then editing it should not have to
        remember which end is exclusive.
        """
        slug, ids = scratchpad
        result = run(ReadBlocksTool, start_index=0, end_index=1)
        assert "## Title" in result
        assert "some text" in result
        assert "a task" not in result

    def test_end_only_includes_that_index(self, scratchpad):
        result = run(ReadBlocksTool, end_index=1)
        assert "## Title" in result
        assert "some text" in result
        assert "a task" not in result

    def test_a_single_index_selects_one_block(self, scratchpad):
        result = run(ReadBlocksTool, start_index=1, end_index=1)
        assert "some text" in result
        assert "## Title" not in result

    def test_start_only(self, scratchpad):
        result = run(ReadBlocksTool, start_index=2)
        assert "a task" in result
        assert "## Title" not in result

    def test_no_match_is_reported_clearly(self, scratchpad):
        result = run(ReadBlocksTool, filter_type="decision")
        assert "No blocks matched" in result

    def test_block_id_with_a_filter_is_rejected(self, scratchpad):
        result = run(ReadBlocksTool, block_id="blk-1", filter_type="todo")
        assert "cannot be combined" in result

    def test_block_id_with_a_range_is_rejected(self, scratchpad):
        result = run(ReadBlocksTool, block_id="blk-1", start_index=0)
        assert "cannot be combined" in result

    def test_an_unknown_filter_type_lists_the_valid_ones(self, scratchpad):
        result = run(ReadBlocksTool, filter_type="wat")
        assert "paragraph" in result
        assert "todo" in result

    @pytest.mark.parametrize("bad", ["-1", 1.5, "abc"])
    def test_a_non_integer_index_is_rejected(self, scratchpad, bad):
        result = run(ReadBlocksTool, start_index=bad)
        assert "must be an integer" in result or "must not be negative" in result

    def test_a_negative_index_is_rejected(self, scratchpad):
        assert "must not be negative" in run(ReadBlocksTool, start_index=-1)

    def test_a_reversed_range_is_rejected(self, scratchpad):
        assert "end must not precede start" in run(
            ReadBlocksTool, start_index=2, end_index=1
        )

    def test_metadata_can_be_omitted(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadBlocksTool, include_metadata=False)
        assert "[header]" not in result
        assert "author=" not in result
        # The content is still there.
        assert "## Title" in result

    def test_metadata_is_on_by_default(self, scratchpad):
        assert "author=" in run(ReadBlocksTool)


# ---------------------------------------------------------------------------
# Mutating tools
# ---------------------------------------------------------------------------


class TestWriteBlock:
    def test_replaces_and_reports_the_id_and_type(self, scratchpad):
        slug, ids = scratchpad
        result = run(WriteBlockTool, block_id=ids[1], new_content="rewritten")
        assert ids[1] in result
        assert "paragraph" in result
        assert load_document(slug).get_block(ids[1]).data["text"] == "rewritten"

    def test_the_block_keeps_its_id(self, scratchpad):
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[1], new_content="x")
        assert load_document(slug).get_block(ids[1]) is not None

    def test_the_block_keeps_its_type(self, scratchpad):
        """That is the difference from change_block_type: content, not shape."""
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[0], new_content="new heading")
        assert load_document(slug).get_block(ids[0]).type == "header"

    def test_the_content_is_plain_text_not_json(self, scratchpad):
        """A caller writing JSON by habit must not get JSON stored as the text."""
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[1], new_content='{"text": "nope"}')
        assert load_document(slug).get_block(ids[1]).data["text"] == '{"text": "nope"}'

    def test_a_header_keeps_its_level_from_a_markdown_marker(self, scratchpad):
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[0], new_content="### Deep")
        block = load_document(slug).get_block(ids[0])
        assert block.data["text"] == "Deep"
        assert block.data["level"] == 3

    def test_a_list_block_gets_one_item_per_line(self, scratchpad):
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[2], new_content="a\nb\nc")
        block = load_document(slug).get_block(ids[2])
        assert block.type == "todo"
        # A todo holds one text, so the lines are joined rather than lost.
        assert "a" in block.data["text"]

    def test_the_version_advances(self, scratchpad):
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[0], new_content="T")
        assert load_document(slug).meta.version == 2

    def test_the_agent_is_recorded_as_the_author(self, scratchpad):
        slug, ids = scratchpad
        run(WriteBlockTool, block_id=ids[1], new_content="x")
        block = load_document(slug).get_block(ids[1])
        assert "agent" in block.meta.touched_by

    def test_unknown_block_is_reported(self, scratchpad):
        assert "No block" in run(WriteBlockTool, block_id="blk-nope", new_content="x")

    def test_an_empty_block_id_is_rejected(self, scratchpad):
        assert "required" in run(WriteBlockTool, block_id="", new_content="x")

    def test_empty_content_is_refused_with_a_pointer_to_delete(self, scratchpad):
        """Writing nothing is almost certainly a caller meaning to remove it."""
        result = run(WriteBlockTool, block_id="blk-x", new_content="")
        assert "delete_block" in result

    def test_writing_the_same_content_changes_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = load_document(slug)
        run(WriteBlockTool, block_id=ids[1], new_content="some text")
        after = load_document(slug)
        assert after.meta.version == before.meta.version


class TestChangeBlockType:
    def test_changes_the_type_and_keeps_the_id(self, scratchpad):
        slug, ids = scratchpad
        result = run(ChangeBlockTypeTool, block_id=ids[1], new_type="todo")
        assert ids[1] in result
        assert load_document(slug).get_block(ids[1]).type == "todo"

    def test_a_paragraph_becoming_a_list_becomes_a_one_item_list(self, scratchpad):
        slug, ids = scratchpad
        run(ChangeBlockTypeTool, block_id=ids[1], new_type="list")
        block = load_document(slug).get_block(ids[1])
        assert block.data["items"] == ["some text"]

    def test_a_paragraph_becoming_a_checklist_becomes_one_item(self, scratchpad):
        slug, ids = scratchpad
        run(ChangeBlockTypeTool, block_id=ids[1], new_type="checklist")
        block = load_document(slug).get_block(ids[1])
        assert block.data["items"] == [{"text": "some text", "checked": False}]

    def test_a_header_becoming_a_paragraph_loses_its_marker(self, scratchpad):
        """The content carries over as TEXT, not as its markdown rendering."""
        slug, ids = scratchpad
        run(ChangeBlockTypeTool, block_id=ids[0], new_type="paragraph")
        assert load_document(slug).get_block(ids[0]).data["text"] == "Title"

    def test_a_delimiter_conversion_carries_no_content(self, scratchpad):
        slug, ids = scratchpad
        run(ChangeBlockTypeTool, block_id=ids[1], new_type="delimiter")
        assert load_document(slug).get_block(ids[1]).data == {}

    def test_the_type_is_unchanged_for_an_unknown_block(self, scratchpad):
        assert "No block" in run(
            ChangeBlockTypeTool, block_id="blk-nope", new_type="todo"
        )

    def test_an_unknown_new_type_lists_the_valid_ones(self, scratchpad):
        result = run(ChangeBlockTypeTool, block_id="blk-x", new_type="wat")
        assert "paragraph" in result

    def test_converting_to_the_same_type_changes_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = load_document(slug)
        result = run(ChangeBlockTypeTool, block_id=ids[1], new_type="paragraph")
        assert "already" in result
        assert load_document(slug).meta.version == before.meta.version

    def test_an_empty_new_type_is_rejected(self, scratchpad):
        assert "required" in run(ChangeBlockTypeTool, block_id="blk-x", new_type="")


class TestInsertBlock:
    def test_appends_at_the_end_by_default(self, scratchpad):
        slug, ids = scratchpad
        result = run(InsertBlockTool, block_type="todo", new_content="new task")
        assert "at the end" in result
        blocks = load_document(slug).blocks
        assert len(blocks) == 4
        assert blocks[-1].data["text"] == "new task"

    def test_the_result_names_the_new_block(self, scratchpad):
        slug, ids = scratchpad
        result = run(InsertBlockTool, block_type="todo", new_content="new task")
        new_id = load_document(slug).blocks[-1].id
        assert new_id in result

    def test_inserts_after_an_anchor(self, scratchpad):
        slug, ids = scratchpad
        run(
            InsertBlockTool,
            block_type="paragraph",
            new_content="middle",
            after_block_id=ids[0],
        )
        blocks = load_document(slug).blocks
        assert [b.type for b in blocks] == ["header", "paragraph", "paragraph", "todo"]

    def test_a_new_id_is_generated(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", new_content="n")
        new_id = load_document(slug).blocks[-1].id
        assert new_id not in ids

    def test_the_version_advances_once(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", new_content="n")
        assert load_document(slug).meta.version == 2

    def test_a_missing_anchor_is_reported(self, scratchpad):
        result = run(
            InsertBlockTool,
            block_type="todo",
            new_content="n",
            after_block_id="blk-nope",
        )
        assert "no such block" in result

    def test_an_unknown_type_lists_the_valid_ones(self, scratchpad):
        result = run(InsertBlockTool, block_type="wat", new_content="x")
        assert "paragraph" in result

    def test_a_missing_type_is_rejected(self, scratchpad):
        assert "required" in run(InsertBlockTool, block_type="", new_content="x")

    def test_missing_content_is_rejected(self, scratchpad):
        assert "required" in run(InsertBlockTool, block_type="paragraph")

    def test_every_block_type_can_be_inserted_from_plain_text(self, scratchpad):
        """The point of plain-text content: no type needs a JSON object."""
        slug, ids = scratchpad
        samples = {
            "paragraph": "p",
            "header": "## h",
            "list": "- a\n- b",
            "code": "```py\nx = 1\n```",
            "quote": "q | cite",
            "checklist": "- [x] c",
            "delimiter": "",
            "question": "q",
            "decision": "d",
            "todo": "t",
        }
        for block_type, text in samples.items():
            result = run(InsertBlockTool, block_type=block_type, new_content=text)
            assert "Inserted block" in result, f"{block_type}: {result}"

    def test_the_plain_text_is_interpreted_per_type(self, scratchpad):
        slug, ids = scratchpad
        run(
            InsertBlockTool,
            block_type="checklist",
            new_content="- [x] done\n- [ ] todo",
        )
        block = load_document(slug).blocks[-1]
        assert block.data["items"] == [
            {"text": "done", "checked": True},
            {"text": "todo", "checked": False},
        ]


class TestDeleteBlock:
    def test_deletes_and_reports(self, scratchpad):
        slug, ids = scratchpad
        result = run(DeleteBlockTool, block_id=ids[1])
        assert ids[1] in result
        assert len(load_document(slug).blocks) == 2

    def test_the_deleted_block_is_gone(self, scratchpad):
        slug, ids = scratchpad
        run(DeleteBlockTool, block_id=ids[1])
        assert load_document(slug).get_block(ids[1]) is None

    def test_an_unknown_block_is_reported(self, scratchpad):
        assert "No block" in run(DeleteBlockTool, block_id="blk-nope")

    def test_a_missing_id_is_rejected(self, scratchpad):
        assert "required" in run(DeleteBlockTool, block_id="")

    def test_the_version_advances_once(self, scratchpad):
        slug, ids = scratchpad
        run(DeleteBlockTool, block_id=ids[0])
        assert load_document(slug).meta.version == 2


class TestMoveBlock:
    def test_moves_to_the_end(self, scratchpad):
        slug, ids = scratchpad
        run(MoveBlockTool, block_id=ids[0])
        assert [b.id for b in load_document(slug).blocks] == [ids[1], ids[2], ids[0]]

    def test_moves_after_an_anchor(self, scratchpad):
        slug, ids = scratchpad
        run(MoveBlockTool, block_id=ids[0], after_block_id=ids[2])
        assert [b.id for b in load_document(slug).blocks] == [ids[1], ids[2], ids[0]]

    def test_reports_where_it_went(self, scratchpad):
        slug, ids = scratchpad
        assert "to the end" in run(MoveBlockTool, block_id=ids[0])
        assert f"after {ids[2]}" in run(
            MoveBlockTool, block_id=ids[0], after_block_id=ids[2]
        )

    def test_an_unknown_block_is_reported(self, scratchpad):
        assert "No block" in run(MoveBlockTool, block_id="blk-nope")

    def test_a_missing_anchor_is_reported(self, scratchpad):
        result = run(
            MoveBlockTool, block_id=scratchpad[1][0], after_block_id="blk-nope"
        )
        assert "no such block" in result

    def test_moving_after_itself_is_rejected(self, scratchpad):
        slug, ids = scratchpad
        result = run(MoveBlockTool, block_id=ids[0], after_block_id=ids[0])
        assert "cannot be moved after itself" in result


class TestReadRevisions:
    def test_reports_the_creation_baseline(self, scratchpad):
        result = run(ReadRevisionsTool)
        assert "[rev 1]" in result
        assert "Created document" in result

    def test_shows_an_agents_edit(self, scratchpad):
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="x",
        )
        result = run(ReadRevisionsTool)
        assert "agent" in result
        assert ids[1] in result

    def test_newest_first(self, scratchpad):
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="a",
        )
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="b",
        )
        result = run(ReadRevisionsTool)
        assert result.index("[rev 3]") < result.index("[rev 1]")

    def test_limit_caps_the_output(self, scratchpad):
        slug, ids = scratchpad
        for index in range(3):
            run(
                WriteBlockTool,
                block_id=ids[1],
                new_content=str(index),
            )
        result = run(ReadRevisionsTool, limit=2)
        assert result.count("[rev ") == 2

    def test_a_limit_above_the_maximum_is_capped_not_refused(self, scratchpad):
        """Asking for too much history is not a mistake worth failing."""
        result = run(ReadRevisionsTool, limit=1000)
        assert "revision(s)" in result

    def test_a_zero_limit_is_rejected(self, scratchpad):
        assert "at least 1" in run(ReadRevisionsTool, limit=0)

    def test_a_non_integer_limit_is_rejected(self, scratchpad):
        assert "must be an integer" in run(ReadRevisionsTool, limit="ten")

    def test_filter_by_author(self, scratchpad):
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="x",
        )
        result = run(ReadRevisionsTool, author="agent")
        assert "agent" in result
        assert "Created document" not in result

    def test_an_unknown_author_is_rejected(self, scratchpad):
        assert "author must be" in run(ReadRevisionsTool, author="nobody")

    def test_since_id_is_exclusive(self, scratchpad):
        """Only entries with an id strictly greater are returned."""
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="x",
        )

        result = run(ReadRevisionsTool, since_id=1)
        assert "[rev 2]" in result
        assert "[rev 1]" not in result

        # Nothing is newer than the newest, so this is empty rather than stale.
        assert "No revisions recorded" in run(ReadRevisionsTool, since_id=2)

    def test_no_history_is_reported(self, notes_dir):
        document = create_document(
            "Empty Log", [{"type": "paragraph", "data": {"text": "x"}}]
        )
        revisions_path(document.meta.slug).unlink(missing_ok=True)
        set_scratchpad_state(document.meta.slug)
        assert "No revisions recorded" in run(ReadRevisionsTool)


# ---------------------------------------------------------------------------
# The tools and the HTTP API agree
# ---------------------------------------------------------------------------


class TestToolsAndApiAgree:
    def test_a_tool_edit_is_visible_through_the_loaded_document(self, scratchpad):
        """The tools write the same format the API serves."""
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="quote", new_content="q | c")
        block = load_document(slug).blocks[-1]
        assert block.type == "quote"
        assert block.data == {"text": "q", "caption": "c"}

    def test_each_mutating_tool_appends_exactly_one_revision(self, scratchpad):
        slug, ids = scratchpad

        def revision_count():
            return len(
                [
                    line
                    for line in revisions_path(slug).read_text().splitlines()
                    if line.strip()
                ]
            )

        before = revision_count()
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="a",
        )
        assert revision_count() == before + 1

        run(InsertBlockTool, block_type="todo", new_content="b")
        assert revision_count() == before + 2

        run(MoveBlockTool, block_id=ids[2])
        assert revision_count() == before + 3

        run(DeleteBlockTool, block_id=ids[0])
        assert revision_count() == before + 4

    def test_the_version_advances_once_per_tool_call(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", new_content="a")
        run(InsertBlockTool, block_type="todo", new_content="b")
        run(InsertBlockTool, block_type="todo", new_content="c")
        assert load_document(slug).meta.version == 4


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


class TestRendering:
    @pytest.mark.parametrize(
        "block_type,data,expected",
        [
            ("header", {"text": "T", "level": 3}, "### T"),
            ("paragraph", {"text": "body"}, "body"),
            ("list", {"items": ["a", "b"], "style": "unordered"}, "- a\n- b"),
            ("list", {"items": ["a", "b"], "style": "ordered"}, "1. a\n2. b"),
            ("code", {"code": "x", "language": "python"}, "```python\nx\n```"),
            ("code", {"code": "x", "language": ""}, "```\nx\n```"),
            ("quote", {"text": "q", "caption": ""}, "> q"),
            ("quote", {"text": "q", "caption": "me"}, "> q\n> -- me"),
            ("delimiter", {}, "---"),
            ("question", {"text": "why", "answered": False}, "[QUESTION] why"),
            ("question", {"text": "why", "answered": True}, "[QUESTION answered] why"),
            ("decision", {"text": "we chose"}, "[DECISION] we chose"),
            ("todo", {"text": "do", "checked": False}, "[TODO unchecked] do"),
            ("todo", {"text": "done", "checked": True}, "[TODO checked] done"),
            (
                "checklist",
                {
                    "items": [
                        {"text": "a", "checked": True},
                        {"text": "b", "checked": False},
                    ]
                },
                "- [x] a\n- [ ] b",
            ),
        ],
    )
    def test_render_forms(self, block_type, data, expected):
        assert render_data(block_type, data) == expected

    def test_a_multiline_quote_is_prefixed_per_line(self):
        assert (
            render_data("quote", {"text": "one\ntwo", "caption": ""}) == "> one\n> two"
        )

    def test_the_header_line_names_id_type_and_both_authorship_fields(self, scratchpad):
        """`author` alone told the agent the wrong thing.

        It is the CREATOR and nothing updates it, so after the agent edited a
        block, re-reading it still said `author=user`. The header now carries the
        last writer too, under the same label read_scratchpad uses.
        """
        slug, ids = scratchpad
        result = run(ReadBlocksTool, block_id=ids[0])
        assert f"[header] id: {ids[0]} author=user last-touched-by=user" in result


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_the_block_tools_are_registered(self):
        from wichy.tools.registry import get_all_tools

        names = {tool.name for tool in get_all_tools()}
        assert {
            "read_blocks",
            "write_block",
            "change_block_type",
            "insert_block",
            "delete_block",
            "move_block",
            "notes_answer_question",
            "read_revisions",
        } <= names

    def test_write_scratchpad_is_gone(self):
        from wichy.tools.registry import get_all_tools

        names = {tool.name for tool in get_all_tools()}
        assert "write_scratchpad" not in names

    def test_read_scratchpad_is_retained(self):
        from wichy.tools.registry import get_all_tools

        assert "read_scratchpad" in {tool.name for tool in get_all_tools()}

    def test_the_write_scratchpad_module_is_deleted(self):
        import importlib

        with pytest.raises(ImportError):
            importlib.import_module("wichy.tools.write_scratchpad")

    def test_no_tool_takes_a_slug(self):
        """The tools edit the pinned scratchpad only; a slug would reach past it."""
        for tool_class in BLOCK_TOOLS:
            fields = set(tool_class.parameters_model.model_fields)
            assert "slug" not in fields, tool_class.__name__

    def test_every_tool_has_a_description(self):
        for tool_class in [*BLOCK_TOOLS, ReadScratchpadTool]:
            instance = tool_class()
            assert instance.description
            assert instance.needs_verification_in_api is False

    def test_every_tool_produces_a_usable_function_schema(self):
        """The schema is what the model sees; an unusable one makes the tool invisible."""
        for tool_class in [*BLOCK_TOOLS, ReadScratchpadTool]:
            schema = tool_class().to_function_definition()
            assert schema["type"] == "function"
            props = schema["function"]["parameters"]["properties"]
            # Every property needs a concrete type: a `$ref` or an `anyOf`
            # member without one would break the caller.
            for name, prop in props.items():
                assert "type" in prop, f"{tool_class.__name__}.{name} has no type"


# ---------------------------------------------------------------------------
# Defects and gaps found in review
# ---------------------------------------------------------------------------


class TestSuccessMessagesReportThePersistedVersion:
    """The version a tool reports must be the one it produced.

    The bump happens as ``locked_document`` exits, so a tool that reads the
    version inside the block reports the version it REPLACED. The agent would
    then send a stale expected version on its next call and be told its own edit
    conflicted with itself.
    """

    @pytest.mark.parametrize(
        "tool,make_kwargs",
        [
            (
                WriteBlockTool,
                lambda ids: {"block_id": ids[1], "new_content": "x"},
            ),
            (
                InsertBlockTool,
                lambda ids: {"block_type": "todo", "new_content": "x"},
            ),
            (DeleteBlockTool, lambda ids: {"block_id": ids[0]}),
            (MoveBlockTool, lambda ids: {"block_id": ids[0]}),
        ],
    )
    def test_the_reported_version_is_the_persisted_one(
        self, scratchpad, tool, make_kwargs
    ):
        slug, ids = scratchpad
        assert load_document(slug).meta.version == 1

        result = run(tool, **make_kwargs(ids))

        persisted = load_document(slug).meta.version
        assert persisted == 2
        assert f"(version {persisted})" in result
        # And it is not quietly reporting the version the call replaced.
        assert f"(version {persisted - 1})" not in result

    def test_the_reported_version_matches_after_several_calls(self, scratchpad):
        slug, ids = scratchpad
        for expected in (2, 3, 4):
            result = run(InsertBlockTool, block_type="todo", new_content="x")
            assert f"(version {expected})" in result
            assert load_document(slug).meta.version == expected


class TestRefusalsChangeNothing:
    """A tool that refuses must leave the document and its log untouched."""

    def _state(self, slug):
        document = load_document(slug)
        return (
            document.meta.version,
            [b.id for b in document.blocks],
            revisions_path(slug).read_text(encoding="utf-8"),
        )

    def test_a_rejected_insert_persists_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        result = run(
            InsertBlockTool,
            block_type="list",
            new_content="a",
            after_block_id="blk-nope",
        )

        assert "no such block" in result
        assert self._state(slug) == before

    def test_a_rejected_write_persists_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        result = run(WriteBlockTool, block_id="blk-nope", new_content="x")

        assert "No block" in result
        assert self._state(slug) == before

    def test_an_unknown_block_does_not_advance_the_version(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        assert "No block" in run(DeleteBlockTool, block_id="blk-nope")
        assert "No block" in run(
            WriteBlockTool,
            block_id="blk-nope",
            new_content="x",
        )
        assert "No block" in run(MoveBlockTool, block_id="blk-nope")

        assert self._state(slug) == before

    def test_a_missing_anchor_does_not_advance_the_version(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        assert "no such block" in run(
            InsertBlockTool,
            block_type="todo",
            new_content="x",
            after_block_id="blk-nope",
        )

        assert self._state(slug) == before

    def test_the_error_names_the_id_that_was_wrong(self, scratchpad):
        """The agent needs to know which id it got wrong to retry usefully."""
        result = run(DeleteBlockTool, block_id="blk-wrong1")
        assert "blk-wrong1" in result


class TestInsertPersistsEveryType:
    @pytest.mark.parametrize(
        "block_type,text",
        [
            ("paragraph", "p"),
            ("header", "## h"),
            ("list", "- a\n- b"),
            ("code", "```py\nx = 1\n```"),
            ("quote", "q | cite"),
            ("checklist", "- [x] c"),
            ("delimiter", ""),
            ("question", "q"),
            ("decision", "d"),
            ("todo", "t"),
        ],
    )
    def test_the_block_is_really_stored_with_its_own_type_and_data(
        self, scratchpad, block_type, text
    ):
        """A message that echoes the requested type proves nothing on its own.

        The expected data is recomputed from the plain text through the same
        coercion the tool uses, then validated against the type's model, so the
        assertion is about the STORED shape rather than about a literal this test
        wrote by hand.
        """
        slug, ids = scratchpad

        result = run(InsertBlockTool, block_type=block_type, new_content=text)

        blocks = load_document(slug).blocks
        stored = blocks[-1]
        assert stored.id in result
        assert stored.type == block_type
        from wichy.tools.notes.agent_tools import text_to_data
        from wichy.tools.notes.models import validate_block_data

        expected = validate_block_data(block_type, text_to_data(block_type, text))
        assert stored.data == expected.model_dump(mode="json")


class TestReplaceKeepsExactlyTheSameBlocks:
    def test_replace_does_not_add_or_remove_a_block(self, scratchpad):
        """`is not None` would miss an implementation that duplicates the block."""
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="x",
        )
        assert [b.id for b in load_document(slug).blocks] == ids

    def test_insert_adds_exactly_one(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", new_content="x")
        assert len(load_document(slug).blocks) == len(ids) + 1


class TestLimitCap:
    def test_the_cap_actually_bites(self, scratchpad, monkeypatch):
        """With a cap above the entry count the cap is unobservable."""
        slug, ids = scratchpad
        for index in range(4):
            run(
                WriteBlockTool,
                block_id=ids[1],
                new_content=str(index),
            )
        # Five entries exist; a cap of 2 must return two of them.
        monkeypatch.setattr(ReadRevisionsTool, "MAX_LIMIT", 2)
        result = run(ReadRevisionsTool, limit=1000)
        assert result.count("[rev ") == 2

    def test_the_cap_is_not_applied_below_the_maximum(self, scratchpad, monkeypatch):
        slug, ids = scratchpad
        for index in range(3):
            run(
                WriteBlockTool,
                block_id=ids[1],
                new_content=str(index),
            )
        monkeypatch.setattr(ReadRevisionsTool, "MAX_LIMIT", 100)
        assert run(ReadRevisionsTool, limit=2).count("[rev ") == 2


class TestNoMatchMessageIsSpecific:
    def test_it_names_the_slug_and_the_total(self, scratchpad):
        """The total is what distinguishes 'filter matched nothing' from 'empty'."""
        slug, ids = scratchpad
        result = run(ReadBlocksTool, filter_type="decision")
        assert f"'{slug}'" in result
        assert "3 total" in result


class TestRenderSurvivesHandEditedData:
    """Stored data is not re-validated on read, so it can violate its type."""

    @pytest.mark.parametrize(
        "block_type,data",
        [
            ("header", {"text": "T", "level": "two"}),
            ("header", {}),
            ("list", {"items": 5, "style": "unordered"}),
            ("list", {}),
            ("checklist", {"items": ["not a dict"]}),
            ("checklist", {"items": None}),
            ("quote", {"text": None}),
            ("code", {}),
            ("paragraph", {}),
            ("mystery", {"anything": 1}),
        ],
    )
    def test_render_never_raises(self, block_type, data):
        assert isinstance(render_data(block_type, data), str)

    def test_non_dict_data_renders(self):
        assert isinstance(render_data("paragraph", None), str)
        assert isinstance(render_data("paragraph", "raw text"), str)

    def test_a_malformed_block_does_not_make_the_document_unreadable(
        self, scratchpad, notes_dir
    ):
        """One bad block must not take out the agent's primary read tool."""
        slug, ids = scratchpad
        path = notes_dir / f"{slug}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["blocks"][0]["data"] = {"text": "T", "level": "two"}
        path.write_text(json.dumps(payload), encoding="utf-8")

        result = run(ReadBlocksTool)

        # The document is still readable, and the odd block shows its content.
        assert "some text" in result
        assert "two" in result


class TestUnreadableScratchpad:
    def test_a_non_utf8_document_is_reported_not_crashed(self, notes_dir):
        (notes_dir / "binary.json").write_bytes(b"\xff\xfe\x00\x01")
        set_scratchpad_state("binary")
        result = run(ReadBlocksTool)
        assert "could not be read" in result.lower()

    def test_a_non_utf8_document_is_reported_by_write_tools_too(self, notes_dir):
        (notes_dir / "binary.json").write_bytes(b"\xff\xfe\x00\x01")
        set_scratchpad_state("binary")
        for tool, kwargs in [
            (
                WriteBlockTool,
                {"block_id": "b", "new_content": "x"},
            ),
            (InsertBlockTool, {"block_type": "paragraph", "new_content": "x"}),
            (DeleteBlockTool, {"block_id": "b"}),
            (MoveBlockTool, {"block_id": "b"}),
        ]:
            result = run(tool, **kwargs)
            # The read path says "could not be read"; the write path says
            # "Could not read the scratchpad". Either way the failure is
            # reported rather than raised.
            assert "could not read" in result.lower(), f"{tool.__name__}: {result}"


class TestChangeBlockTypeRequiresAValidType:
    def test_an_empty_new_type_is_rejected(self, scratchpad):
        """Claiming to convert to a type it did not use would mislead the agent."""
        slug, ids = scratchpad
        before = load_document(slug).meta.version
        result = run(ChangeBlockTypeTool, block_id=ids[0], new_type="")
        assert "new_type is required" in result
        assert load_document(slug).meta.version == before

    def test_the_reported_type_matches_the_stored_type_on_a_change(self, scratchpad):
        slug, ids = scratchpad
        result = run(ChangeBlockTypeTool, block_id=ids[1], new_type="todo")
        assert "todo" in result
        assert load_document(slug).get_block(ids[1]).type == "todo"


class TestToolOutputMatchesTheApi:
    def test_a_tool_edit_is_readable_through_the_http_api(self, scratchpad):
        """The tools and the API write one on-disk format."""
        from flask import Blueprint, Flask

        from wichy.tools.notes import api

        slug, ids = scratchpad
        run(InsertBlockTool, block_type="decision", new_content="we chose this")

        app = Flask(__name__)
        app.config["TESTING"] = True
        bp = Blueprint("notes", __name__, url_prefix="/tools/notes")
        api.register_routes(bp)
        app.register_blueprint(bp)

        with app.test_client() as client:
            body = client.get(f"/tools/notes/api/notes/{slug}").get_json()

        assert body["format"] == "editorjs"
        assert body["blocks"][-1]["type"] == "decision"
        assert body["blocks"][-1]["data"]["text"] == "we chose this"
        assert body["meta"]["version"] == load_document(slug).meta.version


class TestAnswerQuestion:
    """Marking a question answered must not touch the question's text.

    Nothing in the codebase ever set ``answered``: it was read and rendered in
    five places and written in none, so a question block the agent had already
    dealt with kept reading as open and kept prompting for the same answer. The
    obvious workaround -- resending the whole block -- is also the clobbering
    one, because QuestionData forbids extra fields and the agent would have to
    reproduce the user's text exactly.
    """

    @pytest.fixture
    def with_question(self, notes_dir):
        document = create_document(
            "With a question",
            [
                {"type": "paragraph", "data": {"text": "context"}},
                {"type": "question", "data": {"text": "Which one?", "answered": False}},
                {"type": "todo", "data": {"text": "a task"}},
            ],
        )
        set_scratchpad_state(document.meta.slug)
        return document.meta.slug, [b.id for b in document.blocks]

    def test_it_flips_the_flag(self, with_question):
        slug, ids = with_question
        result = run(AnswerQuestionTool, block_id=ids[1])
        assert "answered" in result
        stored = load_document(slug)
        assert stored.get_block(ids[1]).data["answered"] is True

    def test_the_question_text_is_untouched(self, with_question):
        """The whole point of a narrow tool: the user's words survive."""
        slug, ids = with_question
        run(AnswerQuestionTool, block_id=ids[1])
        assert load_document(slug).get_block(ids[1]).data["text"] == "Which one?"

    def test_it_records_the_agent_as_the_last_writer(self, with_question):
        slug, ids = with_question
        run(AnswerQuestionTool, block_id=ids[1])
        meta = load_document(slug).get_block(ids[1]).meta
        assert meta.touched_by[-1] == "agent"
        # The creator is still the user; only the last writer moved.
        assert meta.author == "user"

    def test_it_advances_the_version_once(self, with_question):
        slug, ids = with_question
        before = load_document(slug).meta.version
        run(AnswerQuestionTool, block_id=ids[1])
        assert load_document(slug).meta.version == before + 1

    def test_the_reported_version_is_the_persisted_one(self, with_question):
        """Reading it inside the block reports the version it replaced."""
        slug, ids = with_question
        result = run(AnswerQuestionTool, block_id=ids[1])
        assert f"version {load_document(slug).meta.version}" in result

    def test_it_is_idempotent_and_does_not_bump_again(self, with_question):
        slug, ids = with_question
        run(AnswerQuestionTool, block_id=ids[1])
        after_first = load_document(slug).meta.version
        result = run(AnswerQuestionTool, block_id=ids[1])
        assert "already" in result
        assert load_document(slug).meta.version == after_first

    def test_a_non_question_block_is_refused_by_type(self, with_question):
        slug, ids = with_question
        result = run(AnswerQuestionTool, block_id=ids[2])
        assert "todo" in result
        assert "not a question" in result

    def test_a_refused_type_change_writes_nothing(self, with_question):
        slug, ids = with_question
        before = load_document(slug).meta.version
        run(AnswerQuestionTool, block_id=ids[2])
        assert load_document(slug).meta.version == before

    def test_an_unknown_block_is_reported(self, with_question):
        assert "No block" in run(AnswerQuestionTool, block_id="blk-nope")

    def test_an_empty_block_id_is_rejected(self, with_question):
        assert "block_id is required" in run(AnswerQuestionTool, block_id="")

    def test_the_change_is_queued_for_the_browser(self, with_question):
        """The browser learns about it through the ordinary write path."""
        from wichy.tools.notes.state import describe_pending

        slug, ids = with_question
        run(AnswerQuestionTool, block_id=ids[1])
        pending = describe_pending(slug)
        assert len(pending["changes"]) == 1
        assert pending["changes"][0]["block_id"] == ids[1]
        assert pending["changes"][0]["author"] == "agent"

    def test_the_revision_records_the_flip(self, with_question):
        slug, ids = with_question
        run(AnswerQuestionTool, block_id=ids[1])
        result = run(ReadRevisionsTool)
        assert f"blocks: {ids[1]}" in result


# ---------------------------------------------------------------------------
# Stage 6: the tool layer
# ---------------------------------------------------------------------------


class TestExpectedVersion:
    """A tool write can quote the version it read against.

    Without it every tool write was last-writer-wins: an agent that read at v5
    could apply its edit after the user's v6 write with no signal at all, and the
    user's newer content was silently discarded. The lock prevented torn files,
    never a stale-read clobber.
    """

    def test_quoting_the_current_version_is_accepted(self, scratchpad):
        slug, ids = scratchpad
        current = load_document(slug).meta.version
        result = run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="quoted",
            expected_version=current,
        )
        assert "Updated" in result
        assert load_document(slug).get_block(ids[1]).data["text"] == "quoted"

    def test_quoting_a_stale_version_refuses_and_names_both(self, scratchpad):
        slug, ids = scratchpad
        stale = load_document(slug).meta.version
        # Someone else writes first: this is the user's edit the tool must not
        # silently discard. ids[1] and ids[2] are the paragraph and the todo.
        with locked_document(slug, None, author="user") as document:
            replace_block(document, ids[1], data={"text": "user edit"}, author="user")

        result = run(
            WriteBlockTool,
            block_id=ids[2],
            new_content="agent edit",
            expected_version=stale,
        )
        assert "changed since you read it" in result
        assert f"v{stale}" in result
        assert f"v{stale + 1}" in result
        assert "re-read" in result.lower()

    def test_a_refused_stale_write_changes_nothing(self, scratchpad):
        slug, ids = scratchpad
        stale = load_document(slug).meta.version
        with locked_document(slug, None, author="user") as document:
            replace_block(document, ids[1], data={"text": "user edit"}, author="user")
        before = load_document(slug).meta.version

        run(
            WriteBlockTool,
            block_id=ids[2],
            new_content="agent edit",
            expected_version=stale,
        )
        assert load_document(slug).meta.version == before
        assert load_document(slug).get_block(ids[2]).data["text"] == "a task"

    def test_omitting_the_version_still_writes_over_the_current_state(self, scratchpad):
        """The documented last-writer-wins mode must keep working."""
        slug, ids = scratchpad
        with locked_document(slug, None, author="user") as document:
            replace_block(document, ids[1], data={"text": "user edit"}, author="user")
        result = run(
            WriteBlockTool,
            block_id=ids[2],
            new_content="agent edit",
        )
        assert "Updated" in result
        assert load_document(slug).get_block(ids[2]).data["text"] == "agent edit"

    @pytest.mark.parametrize(
        "tool,kwargs",
        [
            (
                WriteBlockTool,
                {"block_id": "blk-a", "new_content": "x"},
            ),
            (InsertBlockTool, {"block_type": "paragraph", "new_content": "x"}),
            (DeleteBlockTool, {"block_id": "blk-a"}),
            (MoveBlockTool, {"block_id": "blk-a"}),
            (AnswerQuestionTool, {"block_id": "blk-a"}),
        ],
    )
    def test_every_write_tool_accepts_the_parameter(self, scratchpad, tool, kwargs):
        assert "expected_version" in tool.parameters_model.model_fields

    def test_a_bool_expected_version_is_refused(self, scratchpad):
        slug, ids = scratchpad
        assert "must be an integer" in run(
            DeleteBlockTool, block_id=ids[0], expected_version=True
        )

    def test_a_string_expected_version_is_refused(self, scratchpad):
        slug, ids = scratchpad
        assert "must be an integer" in run(
            DeleteBlockTool, block_id=ids[0], expected_version="1"
        )

    def test_a_refused_type_check_writes_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = load_document(slug).meta.version
        run(DeleteBlockTool, block_id=ids[0], expected_version=True)
        assert load_document(slug).meta.version == before


class TestWriteErrorsAreNotReadErrors:
    """An OSError after the read is a WRITE failure, and must say so.

    The old tail reported every OSError as "Could not read the scratchpad",
    because read and write shared one clause. In the append-fails case the
    document version had already advanced with no revision entry and no queued
    op, so the message invited a blind retry of a half-applied write.
    """

    @pytest.mark.parametrize(
        "tool,kwargs_for",
        [
            (
                WriteBlockTool,
                lambda ids: {"block_id": ids[1], "new_content": "x"},
            ),
            (
                InsertBlockTool,
                lambda ids: {"block_type": "paragraph", "new_content": "x"},
            ),
            (DeleteBlockTool, lambda ids: {"block_id": ids[1]}),
            (MoveBlockTool, lambda ids: {"block_id": ids[1]}),
        ],
    )
    def test_a_save_failure_mentions_the_write_and_re_reading(
        self, scratchpad, monkeypatch, tool, kwargs_for
    ):
        from wichy.tools.notes import blocks as blocks_mod

        slug, ids = scratchpad
        # An empty id names no block, and the refusal would arrive before the
        # write this test is about.
        kwargs = kwargs_for(ids)

        def exploding_save(document):
            raise OSError("disk on fire")

        monkeypatch.setattr(blocks_mod, "save_document", exploding_save)
        result = run(tool, **kwargs)
        assert "write" in result.lower()
        assert "uncertain" in result.lower()
        assert "read the scratchpad" in result.lower()
        # The old wording is gone: this was NOT a read failure.
        assert "Could not read the scratchpad" not in result

    def test_the_document_is_unchanged_after_a_refused_write(self, scratchpad):
        slug, ids = scratchpad
        before = load_document(slug).meta.version
        run(
            WriteBlockTool,
            block_id="blk-nope",
            new_content="x",
        )
        assert load_document(slug).meta.version == before


class TestBothAuthorshipFieldsAreReported:
    """The two read tools must agree about who wrote a block."""

    def test_read_blocks_reports_author_and_last_writer(self, scratchpad):
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="agent wrote this",
        )
        result = run(ReadBlocksTool, block_id=ids[1])
        assert "author=user" in result
        assert "last-touched-by=agent" in result

    def test_read_scratchpad_reports_the_same_two_fields(self, scratchpad):
        """In block style, which is where metadata lives."""
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="agent wrote this",
        )
        result = run(ReadScratchpadTool, style="block")
        assert "author=user" in result
        assert "last-touched-by=agent" in result

    def test_the_labels_are_the_same_in_both_tools(self, scratchpad):
        """Two vocabularies for one fact make the agent guess."""
        slug, ids = scratchpad
        from_block_tool = run(ReadBlocksTool, block_id=ids[1])
        from_scratchpad = run(ReadScratchpadTool, style="block")
        for label in ("author=", "last-touched-by="):
            assert label in from_block_tool
            assert label in from_scratchpad


class TestNoOpWritesDoNotBump:
    """A write that changes nothing must not move the version.

    The browser never learns about a version it did not cause: the next save
    sends the version it loaded, and the bump makes it stale. An empty-op
    revision is also a lie in the history.
    """

    def test_a_repeated_write_reports_nothing_changed(self, scratchpad):
        slug, ids = scratchpad
        first = run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        assert "Updated" in first
        after_first = load_document(slug).meta.version

        second = run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        assert "nothing changed" in second
        assert load_document(slug).meta.version == after_first

    def test_a_noop_revision_is_not_recorded(self, scratchpad):
        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        before = run(ReadRevisionsTool)
        count_before = before.count("[rev ")

        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        assert run(ReadRevisionsTool).count("[rev ") == count_before

    def test_the_browser_is_not_told_about_a_noop(self, scratchpad):
        """The queued-op side of the same rule."""
        from wichy.tools.notes.state import describe_pending

        slug, ids = scratchpad
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        describe_pending(slug)  # drain the first write's op by reading it

        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        # Nothing NEW was queued by the second call: the first op is still the
        # only one, and it describes the first (real) change.
        from wichy.tools.notes.state import clear_agent_changes, peek_agent_changes

        clear_agent_changes(slug)
        run(
            WriteBlockTool,
            block_id=ids[1],
            new_content="same",
        )
        assert peek_agent_changes(slug) == []


class TestSinceIdIsTyped:
    """`since_id` accepted `true` while `limit` refused it, and read it as id 1."""

    def test_a_bool_since_id_is_refused(self, scratchpad):
        assert "must be an integer" in run(ReadRevisionsTool, since_id=True)

    def test_a_string_since_id_is_refused(self, scratchpad):
        assert "must be an integer" in run(ReadRevisionsTool, since_id="3")

    def test_an_integer_since_id_is_accepted(self, scratchpad):
        assert "revision(s)" in run(ReadRevisionsTool, since_id=0)


class TestIndexRangeDescriptions:
    def test_the_range_bounds_are_described_as_inclusive(self):
        """The agent sees only the schema, so the docstring is not enough."""
        schema = ReadBlocksTool().to_function_definition()
        props = schema["function"]["parameters"]["properties"]
        assert "inclusive" in props["start_index"]["description"]
        assert "inclusive" in props["end_index"]["description"]


# ---------------------------------------------------------------------------
# Read styles
# ---------------------------------------------------------------------------


class TestReadScratchpadStyles:
    """The default read is content, not metadata.

    Every block's author and raw JSON around its text is a lot of noise for a read
    whose purpose is usually "what does the scratchpad say". The ids still have to
    be there, because they are what a write targets.
    """

    def test_markdown_is_the_default(self, scratchpad):
        result = run(ReadScratchpadTool)
        assert "md" not in result.split("\n")[0]
        # Content, rendered as markdown.
        assert "## Title" in result
        assert "some text" in result

    def test_each_block_is_wrapped_in_a_tag_naming_its_id(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadScratchpadTool)
        for block_id in ids:
            assert f"<{block_id}>" in result
            assert f"</{block_id}>" in result

    def test_the_default_omits_the_raw_data_object(self, scratchpad):
        result = run(ReadScratchpadTool)
        assert '"text"' not in result
        assert "author=" not in result

    def test_block_style_shows_the_metadata_and_raw_data(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadScratchpadTool, style="block")
        assert "author=" in result
        assert "last-touched-by=" in result
        assert '"text": "some text"' in result

    def test_md_is_an_alias_for_markdown(self, scratchpad):
        assert run(ReadScratchpadTool, style="md") == run(ReadScratchpadTool)

    def test_the_style_is_case_insensitive(self, scratchpad):
        assert run(ReadScratchpadTool, style="BLOCK") == run(
            ReadScratchpadTool, style="block"
        )

    def test_an_unknown_style_names_the_valid_ones(self, scratchpad):
        result = run(ReadScratchpadTool, style="fancy")
        assert "markdown" in result
        assert "block" in result

    def test_an_empty_style_is_the_default(self, scratchpad):
        assert run(ReadScratchpadTool, style="") == run(ReadScratchpadTool)

    def test_a_delimiter_block_still_has_an_addressable_id(self, notes_dir):
        """A block with no text of its own must not lose its id to an empty body."""
        document = create_document(
            "Delims",
            [
                {"type": "paragraph", "data": {"text": "above"}},
                {"type": "delimiter", "data": {}},
            ],
        )
        set_scratchpad_state(document.meta.slug)
        delimiter_id = document.blocks[1].id
        result = run(ReadScratchpadTool)
        assert f"<{delimiter_id}>" in result

    def test_the_header_reports_the_version(self, scratchpad):
        assert "version 1" in run(ReadScratchpadTool)

    def test_the_header_names_the_title(self, scratchpad):
        result = run(ReadScratchpadTool)
        assert "[Scratchpad: Scratch | version 1 | 3 blocks]" in result

    def test_an_untitled_note_degrades_to_the_unnamed_header(self, notes_dir):
        # ``create_document`` refuses an empty title (the sidebar must be able
        # to label a note), so an untitled document is one whose title was
        # blanked after creation. Loading it back gives meta.title == "".
        untitled = create_document(
            "Provisional", [{"type": "paragraph", "data": {"text": "x"}}]
        )
        untitled.meta.title = ""
        save_document(untitled)
        set_scratchpad_state(untitled.meta.slug)
        result = run(ReadScratchpadTool)
        assert "[Scratchpad | version 1 | 1 blocks]" in result
        assert "Scratchpad: " not in result


class TestGetBlock:
    def test_returns_one_block_in_full_detail(self, scratchpad):
        slug, ids = scratchpad
        result = run(GetBlockTool, block_id=ids[1])
        assert "some text" in result
        assert "author=" in result
        assert "## Title" not in result

    def test_an_unknown_id_is_reported(self, scratchpad):
        assert "No block" in run(GetBlockTool, block_id="blk-nope")

    def test_a_missing_id_is_reported(self, scratchpad):
        assert "required" in run(GetBlockTool)

    def test_it_matches_read_scratchpad_block_style_for_the_same_block(
        self, scratchpad
    ):
        """The agent should not have to learn two formats for one block."""
        slug, ids = scratchpad
        from_all = run(ReadScratchpadTool, style="block")
        one = run(GetBlockTool, block_id=ids[1])
        # The header differs (document header vs one block), the block does not.
        assert one.split("\n", 2)[2] in from_all


class TestFindBlockIds:
    def test_finds_a_block_by_its_text(self, scratchpad):
        slug, ids = scratchpad
        result = run(FindBlockIdsTool, search_str="some text")
        assert ids[1] in result

    def test_matches_case_insensitively(self, scratchpad):
        slug, ids = scratchpad
        result = run(FindBlockIdsTool, search_str="SOME TEXT")
        assert ids[1] in result

    def test_matches_a_substring(self, scratchpad):
        slug, ids = scratchpad
        assert ids[1] in run(FindBlockIdsTool, search_str="ome tex")

    def test_it_returns_the_full_blocks_not_just_ids(self, scratchpad):
        """Otherwise the agent issues one get_block per hit to learn anything.

        The body is the stored data object, as in get_block and the block style of
        read_scratchpad, so a hit carries everything a follow-up write needs.
        """
        result = run(FindBlockIdsTool, search_str="a task")
        assert "author=" in result
        assert '"text": "a task"' in result

    def test_it_can_match_several_blocks_at_once(self, notes_dir):
        document = create_document(
            "Multi",
            [
                {"type": "paragraph", "data": {"text": "alpha one"}},
                {"type": "paragraph", "data": {"text": "alpha two"}},
                {"type": "paragraph", "data": {"text": "beta"}},
            ],
        )
        set_scratchpad_state(document.meta.slug)
        result = run(FindBlockIdsTool, search_str="alpha")
        assert document.blocks[0].id in result
        assert document.blocks[1].id in result
        assert document.blocks[2].id not in result

    def test_no_match_is_reported_with_the_search_and_the_total(self, scratchpad):
        result = run(FindBlockIdsTool, search_str="zzzz")
        assert "zzzz" in result
        assert "3" in result

    def test_a_missing_search_string_is_reported(self, scratchpad):
        assert "required" in run(FindBlockIdsTool)

    def test_it_searches_the_rendered_text_not_the_raw_data(self, notes_dir):
        """A field NAME is not content.

        Searching the raw JSON would report every checklist block for "checked",
        which is a schema key the agent never saw as text.
        """
        document = create_document(
            "Checklists",
            [
                {
                    "type": "checklist",
                    "data": {"items": [{"text": "milk", "checked": False}]},
                },
            ],
        )
        set_scratchpad_state(document.meta.slug)
        assert "No block" in run(FindBlockIdsTool, search_str="checked")
        # And the visible text does match.
        assert document.blocks[0].id in run(FindBlockIdsTool, search_str="milk")


class TestReadRevisionsMarksAnchors:
    """An anchor row is marked distinctly; ordinary rows keep their exact shape.

    An anchor is the marker for where the recorded history of an older build
    begins -- not a state the agent can browse or revert to -- so the agent must
    not read it as an ordinary revision.
    """

    def _write_log_with_anchor(self, slug):
        anchor = {
            "id": 1,
            "timestamp": "2026-01-01T00:00:00Z",
            "author": "system",
            "baseline": True,
            "version_from": 0,
            "version_to": 0,
            "ops": [],
            "summary": "History starts here.",
        }
        normal = {
            "id": 2,
            "timestamp": "2026-01-02T00:00:00Z",
            "author": "agent",
            "version_from": 1,
            "version_to": 2,
            "ops": [],
            "summary": "An edit",
        }
        revisions_path(slug).write_text(
            "".join(json.dumps(e) + "\n" for e in [anchor, normal]),
            encoding="utf-8",
        )

    def test_read_revisions_marks_anchors(self, scratchpad):
        slug, _ = scratchpad
        self._write_log_with_anchor(slug)

        result = run(ReadRevisionsTool)
        lines = result.splitlines()

        # Newest first: the ordinary row is byte-identical to today's shape.
        assert "[rev 2] 2026-01-02T00:00:00Z agent v1->v2 -- An edit" in lines
        # The anchor row carries the distinct marker as a suffix.
        assert (
            "[rev 1] 2026-01-01T00:00:00Z system v0->v0 -- History starts here."
            " [history anchor -- the start of recorded history; not a browsable"
            " state]" in lines
        )
