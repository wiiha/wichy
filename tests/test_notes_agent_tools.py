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
    DeleteBlockTool,
    InsertBlockTool,
    MoveBlockTool,
    ReadBlocksTool,
    ReadRevisionsTool,
    ReplaceBlockTool,
    render_data,
)
from wichy.tools.notes.blocks import (
    MARKDOWN_WRITE_REFUSED,
    create_document,
    load_document,
    revisions_path,
)
from wichy.tools.notes.state import reset_state
from wichy.tools.read_scratchpad import ReadScratchpadTool

BLOCK_TOOLS = [
    ReadBlocksTool,
    ReplaceBlockTool,
    InsertBlockTool,
    DeleteBlockTool,
    MoveBlockTool,
    ReadRevisionsTool,
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
        run(InsertBlockTool, block_type="todo", data={"text": "x"})
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
            (ReadBlocksTool, {}),
            (
                ReplaceBlockTool,
                {"block_id": "blk-x", "block_type": "paragraph", "data": {"text": "x"}},
            ),
            (InsertBlockTool, {"block_type": "paragraph", "data": {"text": "x"}}),
            (DeleteBlockTool, {"block_id": "blk-x"}),
            (MoveBlockTool, {"block_id": "blk-x"}),
            (ReadRevisionsTool, {}),
        ],
    )
    def test_every_block_tool_returns_the_conversion_message(
        self, markdown_pinned, tool, kwargs
    ):
        """read_revisions included: a markdown note keeps no revision log, and
        "no revisions recorded" would hide the real reason."""
        assert run(tool, **kwargs) == MARKDOWN_WRITE_REFUSED

    def test_the_message_matches_the_one_the_api_uses(self, markdown_pinned):
        """One explanation, not two: the user sees whichever surface they used."""
        assert run(DeleteBlockTool, block_id="x") == MARKDOWN_WRITE_REFUSED

    def test_a_write_tool_makes_no_json(self, notes_dir, markdown_pinned):
        run(InsertBlockTool, block_type="paragraph", data={"text": "x"})
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
        assert f"[Document: {slug} | Version: 1 | Total blocks: 3]" in result
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
        assert "Total blocks: 3" in result

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
        assert "[block id=" not in result
        # The content is still there.
        assert "## Title" in result

    def test_metadata_is_on_by_default(self, scratchpad):
        assert "[block id=" in run(ReadBlocksTool)


# ---------------------------------------------------------------------------
# Mutating tools
# ---------------------------------------------------------------------------


class TestReplaceBlock:
    def test_replaces_and_reports_the_id_and_type(self, scratchpad):
        slug, ids = scratchpad
        result = run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "rewritten"},
        )
        assert ids[1] in result
        assert "paragraph" in result
        assert load_document(slug).get_block(ids[1]).data["text"] == "rewritten"

    def test_the_block_keeps_its_id(self, scratchpad):
        slug, ids = scratchpad
        run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "x"},
        )
        assert load_document(slug).get_block(ids[1]) is not None

    def test_the_version_advances(self, scratchpad):
        slug, ids = scratchpad
        run(
            ReplaceBlockTool,
            block_id=ids[0],
            block_type="header",
            data={"text": "T", "level": 1},
        )
        assert load_document(slug).meta.version == 2

    def test_the_agent_is_recorded_as_the_author(self, scratchpad):
        slug, ids = scratchpad
        run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "x"},
        )
        block = load_document(slug).get_block(ids[1])
        assert "agent" in block.meta.touched_by

    def test_a_type_change_is_allowed(self, scratchpad):
        slug, ids = scratchpad
        run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="todo",
            data={"text": "now a todo"},
        )
        assert load_document(slug).get_block(ids[1]).type == "todo"

    def test_unknown_block_is_reported(self, scratchpad):
        result = run(
            ReplaceBlockTool,
            block_id="blk-nope",
            block_type="paragraph",
            data={"text": "x"},
        )
        assert "No block" in result

    def test_invalid_data_names_the_schema(self, scratchpad):
        slug, ids = scratchpad
        result = run(
            ReplaceBlockTool,
            block_id=ids[0],
            block_type="header",
            data={"text": "no level"},
        )
        assert "HeaderData" in result

    def test_an_invalid_replace_writes_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = load_document(slug)
        result = run(
            ReplaceBlockTool, block_id=ids[0], block_type="header", data={"extra": 1}
        )
        # The refusal names the schema, so the agent learns what to send instead.
        assert "HeaderData" in result
        after = load_document(slug)
        assert after.meta.version == before.meta.version
        assert after.get_block(ids[0]).data == before.get_block(ids[0]).data

    def test_a_non_object_data_is_rejected(self, scratchpad):
        slug, ids = scratchpad
        assert "must be an object" in run(
            ReplaceBlockTool, block_id=ids[0], block_type="paragraph", data="nope"
        )

    def test_an_empty_block_id_is_rejected(self, scratchpad):
        assert "required" in run(
            ReplaceBlockTool, block_id="", block_type="paragraph", data={"text": "x"}
        )


class TestInsertBlock:
    def test_appends_at_the_end_by_default(self, scratchpad):
        slug, ids = scratchpad
        result = run(InsertBlockTool, block_type="todo", data={"text": "new task"})
        assert "at the end" in result
        blocks = load_document(slug).blocks
        assert len(blocks) == 4
        assert blocks[-1].data["text"] == "new task"

    def test_the_result_names_the_new_block(self, scratchpad):
        slug, ids = scratchpad
        result = run(InsertBlockTool, block_type="todo", data={"text": "new task"})
        new_id = load_document(slug).blocks[-1].id
        assert new_id in result

    def test_inserts_after_an_anchor(self, scratchpad):
        slug, ids = scratchpad
        run(
            InsertBlockTool,
            block_type="paragraph",
            data={"text": "middle"},
            after_block_id=ids[0],
        )
        blocks = load_document(slug).blocks
        assert [b.type for b in blocks] == ["header", "paragraph", "paragraph", "todo"]

    def test_a_new_id_is_generated(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", data={"text": "n"})
        new_id = load_document(slug).blocks[-1].id
        assert new_id not in ids

    def test_the_version_advances_once(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", data={"text": "n"})
        assert load_document(slug).meta.version == 2

    def test_a_missing_anchor_is_reported(self, scratchpad):
        result = run(
            InsertBlockTool,
            block_type="todo",
            data={"text": "n"},
            after_block_id="blk-nope",
        )
        assert "no such block" in result

    def test_invalid_data_is_rejected(self, scratchpad):
        slug, ids = scratchpad
        assert "ListData" in run(
            InsertBlockTool, block_type="list", data={"items": ["a"]}
        )

    def test_an_unknown_type_lists_the_valid_ones(self, scratchpad):
        result = run(InsertBlockTool, block_type="wat", data={})
        assert "paragraph" in result

    def test_a_missing_type_is_rejected(self, scratchpad):
        assert "required" in run(InsertBlockTool, block_type="", data={})

    def test_every_block_type_can_be_inserted(self, scratchpad):
        slug, ids = scratchpad
        samples = {
            "paragraph": {"text": "p"},
            "header": {"text": "h", "level": 1},
            "list": {"items": ["a"], "style": "unordered"},
            "code": {"code": "x", "language": ""},
            "quote": {"text": "q", "caption": ""},
            "checklist": {"items": [{"text": "c", "checked": False}]},
            "delimiter": {},
            "question": {"text": "q"},
            "decision": {"text": "d"},
            "todo": {"text": "t"},
        }
        for block_type, data in samples.items():
            result = run(InsertBlockTool, block_type=block_type, data=data)
            assert "Inserted block" in result, f"{block_type}: {result}"


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
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "x"},
        )
        result = run(ReadRevisionsTool)
        assert "agent" in result
        assert ids[1] in result

    def test_newest_first(self, scratchpad):
        slug, ids = scratchpad
        run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "a"},
        )
        run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "b"},
        )
        result = run(ReadRevisionsTool)
        assert result.index("[rev 3]") < result.index("[rev 1]")

    def test_limit_caps_the_output(self, scratchpad):
        slug, ids = scratchpad
        for index in range(3):
            run(
                ReplaceBlockTool,
                block_id=ids[1],
                block_type="paragraph",
                data={"text": str(index)},
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
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "x"},
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
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "x"},
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
        run(InsertBlockTool, block_type="quote", data={"text": "q", "caption": "c"})
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
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "a"},
        )
        assert revision_count() == before + 1

        run(InsertBlockTool, block_type="todo", data={"text": "b"})
        assert revision_count() == before + 2

        run(MoveBlockTool, block_id=ids[2])
        assert revision_count() == before + 3

        run(DeleteBlockTool, block_id=ids[0])
        assert revision_count() == before + 4

    def test_the_version_advances_once_per_tool_call(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", data={"text": "a"})
        run(InsertBlockTool, block_type="todo", data={"text": "b"})
        run(InsertBlockTool, block_type="todo", data={"text": "c"})
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

    def test_the_header_line_names_id_type_and_author(self, scratchpad):
        slug, ids = scratchpad
        result = run(ReadBlocksTool, block_id=ids[0])
        assert f"[block id={ids[0]} type=header author=user]" in result


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_the_six_block_tools_are_registered(self):
        from wichy.tools.registry import get_all_tools

        names = {tool.name for tool in get_all_tools()}
        assert {
            "read_blocks",
            "replace_block",
            "insert_block",
            "delete_block",
            "move_block",
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
                ReplaceBlockTool,
                lambda ids: {
                    "block_id": ids[1],
                    "block_type": "paragraph",
                    "data": {"text": "x"},
                },
            ),
            (
                InsertBlockTool,
                lambda ids: {"block_type": "todo", "data": {"text": "x"}},
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
            result = run(InsertBlockTool, block_type="todo", data={"text": "x"})
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

        result = run(InsertBlockTool, block_type="list", data={"items": ["a"]})

        assert "ListData" in result
        assert self._state(slug) == before

    def test_a_rejected_replace_persists_nothing(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        result = run(
            ReplaceBlockTool,
            block_id=ids[0],
            block_type="header",
            data={"text": "no level"},
        )

        assert "HeaderData" in result
        assert self._state(slug) == before

    def test_an_unknown_block_does_not_advance_the_version(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        assert "No block" in run(DeleteBlockTool, block_id="blk-nope")
        assert "No block" in run(
            ReplaceBlockTool,
            block_id="blk-nope",
            block_type="paragraph",
            data={"text": "x"},
        )
        assert "No block" in run(MoveBlockTool, block_id="blk-nope")

        assert self._state(slug) == before

    def test_a_missing_anchor_does_not_advance_the_version(self, scratchpad):
        slug, ids = scratchpad
        before = self._state(slug)

        assert "no such block" in run(
            InsertBlockTool,
            block_type="todo",
            data={"text": "x"},
            after_block_id="blk-nope",
        )

        assert self._state(slug) == before

    def test_the_error_names_the_id_that_was_wrong(self, scratchpad):
        """The agent needs to know which id it got wrong to retry usefully."""
        result = run(DeleteBlockTool, block_id="blk-wrong1")
        assert "blk-wrong1" in result


class TestInsertPersistsEveryType:
    @pytest.mark.parametrize(
        "block_type,data",
        [
            ("paragraph", {"text": "p"}),
            ("header", {"text": "h", "level": 1}),
            ("list", {"items": ["a"], "style": "unordered"}),
            ("code", {"code": "x", "language": ""}),
            ("quote", {"text": "q", "caption": ""}),
            ("checklist", {"items": [{"text": "c", "checked": False}]}),
            ("delimiter", {}),
            ("question", {"text": "q"}),
            ("decision", {"text": "d"}),
            ("todo", {"text": "t"}),
        ],
    )
    def test_the_block_is_really_stored_with_its_own_type_and_data(
        self, scratchpad, block_type, data
    ):
        """A message that echoes the requested type proves nothing on its own."""
        slug, ids = scratchpad

        result = run(InsertBlockTool, block_type=block_type, data=data)

        blocks = load_document(slug).blocks
        stored = blocks[-1]
        assert stored.id in result
        assert stored.type == block_type
        # Compared against the validated model, so a defaulted field is expected
        # rather than merely tolerated.
        from wichy.tools.notes.models import validate_block_data

        assert stored.data == validate_block_data(block_type, data).model_dump(
            mode="json"
        )


class TestReplaceKeepsExactlyTheSameBlocks:
    def test_replace_does_not_add_or_remove_a_block(self, scratchpad):
        """`is not None` would miss an implementation that duplicates the block."""
        slug, ids = scratchpad
        run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="paragraph",
            data={"text": "x"},
        )
        assert [b.id for b in load_document(slug).blocks] == ids

    def test_insert_adds_exactly_one(self, scratchpad):
        slug, ids = scratchpad
        run(InsertBlockTool, block_type="todo", data={"text": "x"})
        assert len(load_document(slug).blocks) == len(ids) + 1


class TestLimitCap:
    def test_the_cap_actually_bites(self, scratchpad, monkeypatch):
        """With a cap above the entry count the cap is unobservable."""
        slug, ids = scratchpad
        for index in range(4):
            run(
                ReplaceBlockTool,
                block_id=ids[1],
                block_type="paragraph",
                data={"text": str(index)},
            )
        # Five entries exist; a cap of 2 must return two of them.
        monkeypatch.setattr(ReadRevisionsTool, "MAX_LIMIT", 2)
        result = run(ReadRevisionsTool, limit=1000)
        assert result.count("[rev ") == 2

    def test_the_cap_is_not_applied_below_the_maximum(self, scratchpad, monkeypatch):
        slug, ids = scratchpad
        for index in range(3):
            run(
                ReplaceBlockTool,
                block_id=ids[1],
                block_type="paragraph",
                data={"text": str(index)},
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
                ReplaceBlockTool,
                {"block_id": "b", "block_type": "paragraph", "data": {}},
            ),
            (InsertBlockTool, {"block_type": "paragraph", "data": {}}),
            (DeleteBlockTool, {"block_id": "b"}),
            (MoveBlockTool, {"block_id": "b"}),
        ]:
            result = run(tool, **kwargs)
            # The read path says "could not be read"; the write path says
            # "Could not read the scratchpad". Either way the failure is
            # reported rather than raised.
            assert "could not read" in result.lower(), f"{tool.__name__}: {result}"


class TestReplaceRequiresBlockType:
    def test_an_empty_block_type_is_rejected(self, scratchpad):
        """Claiming to replace with a type it did not use would mislead the agent."""
        slug, ids = scratchpad
        before = load_document(slug).meta.version
        result = run(
            ReplaceBlockTool, block_id=ids[0], block_type="", data={"text": "x"}
        )
        assert "block_type is required" in result
        assert load_document(slug).meta.version == before

    def test_the_reported_type_matches_the_stored_type_on_a_change(self, scratchpad):
        slug, ids = scratchpad
        result = run(
            ReplaceBlockTool,
            block_id=ids[1],
            block_type="todo",
            data={"text": "converted"},
        )
        assert "todo" in result
        assert load_document(slug).get_block(ids[1]).type == "todo"


class TestToolOutputMatchesTheApi:
    def test_a_tool_edit_is_readable_through_the_http_api(self, scratchpad):
        """The tools and the API write one on-disk format."""
        from flask import Blueprint, Flask

        from wichy.tools.notes import api

        slug, ids = scratchpad
        run(InsertBlockTool, block_type="decision", data={"text": "we chose this"})

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
