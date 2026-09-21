"""Tests for markdown <-> block conversion.

Grouped by the property each group defends:

- classification: each markdown construct maps to the right block, and the
  ordering rules that make ambiguous lines resolve one way
- paragraph boundaries: where a paragraph starts and stops
- paragraphs and headings: text is preserved exactly, including hard breaks
- export: every block type renders to its specified markdown form
- frontmatter: regenerated, and a title with YAML metacharacters round-trips
- lossy detection: constructs that do not map are reported before converting
- one-way-ness: the mapping is documented as lossy, and the tests pin the
  specific places where a re-read would differ
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wichy.tools.notes.markdown import (
    blocks_to_markdown,
    count_blocks,
    export_legacy,
    export_markdown,
    frontmatter,
    lossy_features,
    markdown_to_blocks,
)


def kinds(body: str) -> list[str]:
    """Block types a markdown body converts to."""
    return [b["type"] for b in markdown_to_blocks(body)]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassification:
    @pytest.mark.parametrize(
        "line,block_type,data",
        [
            ("# One", "header", {"text": "One", "level": 1}),
            ("### Three", "header", {"text": "Three", "level": 3}),
            ("###### Six", "header", {"text": "Six", "level": 6}),
            ("plain", "paragraph", {"text": "plain"}),
        ],
    )
    def test_single_line_constructs(self, line, block_type, data):
        blocks = markdown_to_blocks(line)
        assert len(blocks) == 1
        assert blocks[0]["type"] == block_type
        assert blocks[0]["data"] == data

    def test_seven_hashes_is_not_a_header(self):
        """Only 1-6 hashes are a heading; seven is paragraph text."""
        assert kinds("####### Seven") == ["paragraph"]

    def test_hash_without_a_space_is_not_a_header(self):
        assert kinds("#NotAHeader") == ["paragraph"]

    def test_delimiter(self):
        assert markdown_to_blocks("---") == [{"type": "delimiter", "data": {}}]

    def test_delimiter_variants(self):
        assert kinds("***") == ["delimiter"]
        assert kinds("___") == ["delimiter"]
        assert kinds("-----") == ["delimiter"]

    def test_code_fence_with_language(self):
        blocks = markdown_to_blocks("```python\nx = 1\ny = 2\n```")
        assert blocks == [
            {"type": "code", "data": {"code": "x = 1\ny = 2", "language": "python"}}
        ]

    def test_code_fence_without_language(self):
        blocks = markdown_to_blocks("```\nplain\n```")
        assert blocks[0]["data"] == {"code": "plain", "language": ""}

    def test_code_fence_preserves_blank_lines_inside(self):
        blocks = markdown_to_blocks("```\na\n\nb\n```")
        assert blocks[0]["data"]["code"] == "a\n\nb"

    def test_unterminated_fence_consumes_to_the_end(self):
        """A truncated fence must not leak its content out as markdown."""
        blocks = markdown_to_blocks("```python\nx = 1")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"
        assert blocks[0]["data"]["code"] == "x = 1"

    def test_content_inside_a_fence_is_not_reclassified(self):
        """A heading or list inside code stays code."""
        blocks = markdown_to_blocks("```\n# not a header\n- not a list\n```")
        assert [b["type"] for b in blocks] == ["code"]
        assert "# not a header" in blocks[0]["data"]["code"]

    def test_quote(self):
        blocks = markdown_to_blocks("> quoted text")
        assert blocks == [
            {"type": "quote", "data": {"text": "quoted text", "caption": ""}}
        ]

    def test_consecutive_quote_lines_join(self):
        blocks = markdown_to_blocks("> one\n> two")
        assert len(blocks) == 1
        assert blocks[0]["data"]["text"] == "one\ntwo"

    def test_a_blank_quote_line_is_allowed_inside(self):
        blocks = markdown_to_blocks("> one\n>\n> two")
        assert len(blocks) == 1
        assert blocks[0]["data"]["text"] == "one\n\ntwo"

    def test_checklist(self):
        blocks = markdown_to_blocks("- [ ] todo\n- [x] done")
        assert blocks == [
            {
                "type": "checklist",
                "data": {
                    "items": [
                        {"text": "todo", "checked": False},
                        {"text": "done", "checked": True},
                    ]
                },
            }
        ]

    def test_checklist_accepts_uppercase_x(self):
        blocks = markdown_to_blocks("- [X] done")
        assert blocks[0]["data"]["items"][0]["checked"] is True

    def test_checklist_accepts_a_star_marker(self):
        assert kinds("* [ ] item") == ["checklist"]

    def test_unordered_list(self):
        blocks = markdown_to_blocks("- one\n- two")
        assert blocks == [
            {"type": "list", "data": {"items": ["one", "two"], "style": "unordered"}}
        ]

    def test_ordered_list(self):
        blocks = markdown_to_blocks("1. one\n2. two")
        assert blocks == [
            {"type": "list", "data": {"items": ["one", "two"], "style": "ordered"}}
        ]

    def test_ordered_list_accepts_a_paren_delimiter(self):
        assert kinds("1) one") == ["list"]

    def test_ordered_list_accepts_any_starting_number(self):
        blocks = markdown_to_blocks("7. seven")
        assert blocks[0]["data"]["items"] == ["seven"]

    def test_list_style_change_starts_a_new_block(self):
        """A different style is a different block, not a continuation."""
        blocks = markdown_to_blocks("- a\n1. b")
        assert [b["data"]["style"] for b in blocks] == ["unordered", "ordered"]

    def test_blank_body_has_no_blocks(self):
        assert markdown_to_blocks("") == []
        assert markdown_to_blocks("\n\n  \n") == []


class TestClassificationOrder:
    """The order of the rules is normative, not incidental."""

    def test_checklist_is_tested_before_list(self):
        """`- [ ] item` matches both; the list reading would swallow the marker."""
        blocks = markdown_to_blocks("- [ ] item")
        assert blocks[0]["type"] == "checklist"
        # The marker is not part of the item text.
        assert blocks[0]["data"]["items"][0]["text"] == "item"

    def test_a_checklist_run_is_one_block_not_many(self):
        assert kinds("- [ ] a\n- [x] b\n- [ ] c") == ["checklist"]

    def test_checklist_then_list_are_separate_blocks(self):
        assert kinds("- [ ] a\n- b") == ["checklist", "list"]

    def test_delimiter_is_not_read_as_a_bullet(self):
        assert kinds("---") == ["delimiter"]

    def test_a_bullet_containing_dashes_is_a_list(self):
        assert kinds("- --") == ["list"]

    def test_fence_wins_over_everything_inside_it(self):
        body = "```\n---\n# h\n- [ ] c\n```"
        assert kinds(body) == ["code"]


# ---------------------------------------------------------------------------
# Paragraphs
# ---------------------------------------------------------------------------


class TestParagraphs:
    def test_a_blank_line_ends_a_paragraph(self):
        assert kinds("one\n\ntwo") == ["paragraph", "paragraph"]

    def test_several_blank_lines_still_one_break(self):
        assert kinds("one\n\n\n\ntwo") == ["paragraph", "paragraph"]

    def test_consecutive_lines_join_with_a_newline(self):
        """Hard breaks are preserved; the text is not re-wrapped."""
        blocks = markdown_to_blocks("one\ntwo\nthree")
        assert len(blocks) == 1
        assert blocks[0]["data"]["text"] == "one\ntwo\nthree"

    def test_a_paragraph_stops_at_a_heading(self):
        blocks = markdown_to_blocks("text\n# heading")
        assert [b["type"] for b in blocks] == ["paragraph", "header"]

    def test_a_paragraph_stops_at_a_list(self):
        blocks = markdown_to_blocks("text\n- item")
        assert [b["type"] for b in blocks] == ["paragraph", "list"]

    def test_a_paragraph_stops_at_a_quote(self):
        assert kinds("text\n> quoted") == ["paragraph", "quote"]

    def test_a_paragraph_stops_at_a_fence(self):
        assert kinds("text\n```\ncode\n```") == ["paragraph", "code"]

    def test_a_paragraph_stops_at_a_delimiter(self):
        assert kinds("text\n---") == ["paragraph", "delimiter"]

    def test_leading_blank_lines_are_skipped(self):
        assert kinds("\n\n\ntext") == ["paragraph"]

    def test_trailing_blank_lines_are_skipped(self):
        assert kinds("text\n\n\n") == ["paragraph"]

    def test_whitespace_only_line_ends_a_paragraph(self):
        assert kinds("one\n   \ntwo") == ["paragraph", "paragraph"]

    def test_text_is_not_trimmed_inside_a_paragraph(self):
        blocks = markdown_to_blocks("one\ntwo")
        assert blocks[0]["data"]["text"] == "one\ntwo"


class TestRoundTripLimits:
    """Pin the specific places a re-read differs, so they stay documented.

    Export is one-way by design; these tests exist so that if someone later
    tries to make it round-trip, they can see exactly what they are up against.
    """

    def test_indented_text_becomes_a_paragraph(self):
        """Leading indentation is preserved as paragraph text, not as structure."""
        blocks = markdown_to_blocks("    indented")
        assert blocks[0]["type"] == "paragraph"
        assert blocks[0]["data"]["text"] == "    indented"

    def test_a_numbered_list_loses_its_original_numbering(self):
        """A list block stores one style, so the starting number cannot survive."""
        blocks = markdown_to_blocks("5. five\n6. six")
        assert blocks[0]["data"]["items"] == ["five", "six"]
        # Exporting renumbers from 1, so the original numbers are gone.
        assert blocks_to_markdown(blocks) == "1. five\n2. six"

    def test_paragraph_text_that_looks_like_a_list_reimports_as_one(self):
        """The documented hazard: a paragraph's own text can become structure."""
        blocks = [{"type": "paragraph", "data": {"text": "- not really a list"}}]
        exported = blocks_to_markdown(blocks)
        assert kinds(exported) == ["list"]

    def test_adjacent_lists_of_different_styles_merge_when_re_read(self):
        """Two blocks export with no blank line, so a re-read sees one list."""
        blocks = [
            {"type": "list", "data": {"items": ["a"], "style": "unordered"}},
            {"type": "list", "data": {"items": ["b"], "style": "ordered"}},
        ]
        exported = blocks_to_markdown(blocks)
        assert kinds(exported) == ["list", "list"]
        # And the style boundary is where the reader puts it, not where the
        # blocks had it.
        assert [b["data"]["style"] for b in markdown_to_blocks(exported)] == [
            "unordered",
            "ordered",
        ]

    def test_editor_only_types_do_not_read_back_as_themselves(self):
        """question/decision/todo have no markdown form that round-trips."""
        blocks = [
            {"type": "question", "data": {"text": "why?", "answered": False}},
            {"type": "decision", "data": {"text": "we chose this"}},
            {"type": "todo", "data": {"text": "do it", "checked": False}},
        ]
        exported = blocks_to_markdown(blocks)
        assert kinds(exported) == ["quote", "quote", "checklist"]

    def test_delimiter_and_frontmatter_fence_collide(self):
        """A delimiter exports as `---`, the same marker frontmatter uses."""
        assert blocks_to_markdown([{"type": "delimiter", "data": {}}]) == "---"
        assert frontmatter("t", "c", "u").startswith("---\n")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExportBlocks:
    @pytest.mark.parametrize(
        "block,expected",
        [
            ({"type": "paragraph", "data": {"text": "hello"}}, "hello"),
            (
                {"type": "header", "data": {"text": "Title", "level": 3}},
                "### Title",
            ),
            ({"type": "delimiter", "data": {}}, "---"),
            (
                {"type": "list", "data": {"items": ["a", "b"], "style": "unordered"}},
                "- a\n- b",
            ),
            (
                {"type": "list", "data": {"items": ["a", "b"], "style": "ordered"}},
                "1. a\n2. b",
            ),
            (
                {
                    "type": "checklist",
                    "data": {"items": [{"text": "a", "checked": True}]},
                },
                "- [x] a",
            ),
            (
                {
                    "type": "checklist",
                    "data": {"items": [{"text": "a", "checked": False}]},
                },
                "- [ ] a",
            ),
            (
                {"type": "code", "data": {"code": "x", "language": "python"}},
                "```python\nx\n```",
            ),
            ({"type": "code", "data": {"code": "x", "language": ""}}, "```\nx\n```"),
            ({"type": "quote", "data": {"text": "q", "caption": ""}}, "> q"),
            (
                {"type": "quote", "data": {"text": "q", "caption": "someone"}},
                "> q\n> -- someone",
            ),
            (
                {"type": "question", "data": {"text": "why?", "answered": False}},
                "> [QUESTION] why?",
            ),
            (
                {"type": "question", "data": {"text": "why?", "answered": True}},
                "> [QUESTION answered] why?",
            ),
            ({"type": "decision", "data": {"text": "x"}}, "> [DECISION] x"),
            ({"type": "todo", "data": {"text": "t", "checked": False}}, "- [ ] t"),
            ({"type": "todo", "data": {"text": "t", "checked": True}}, "- [x] t"),
        ],
    )
    def test_render_forms(self, block, expected):
        assert blocks_to_markdown([block]) == expected

    def test_headers_repeat_the_hash_by_level(self):
        for level in range(1, 7):
            block = {"type": "header", "data": {"text": "T", "level": level}}
            assert blocks_to_markdown([block]) == f"{'#' * level} T"

    def test_multiline_quote_is_prefixed_per_line(self):
        block = {"type": "quote", "data": {"text": "one\ntwo", "caption": ""}}
        assert blocks_to_markdown([block]) == "> one\n> two"

    def test_a_code_body_containing_a_fence_gets_a_longer_fence(self):
        """Otherwise the body would close the fence early and leak as markdown."""
        block = {"type": "code", "data": {"code": "```\ninner\n```", "language": ""}}
        exported = blocks_to_markdown([block])
        assert exported.startswith("````")
        # And a re-read recovers the whole body.
        assert markdown_to_blocks(exported)[0]["data"]["code"] == "```\ninner\n```"

    def test_blocks_are_separated_by_a_blank_line(self):
        blocks = [
            {"type": "paragraph", "data": {"text": "a"}},
            {"type": "paragraph", "data": {"text": "b"}},
        ]
        assert blocks_to_markdown(blocks) == "a\n\nb"

    def test_no_trailing_newline(self):
        assert not blocks_to_markdown(
            [{"type": "paragraph", "data": {"text": "a"}}]
        ).endswith("\n")

    def test_empty_block_list(self):
        assert blocks_to_markdown([]) == ""

    def test_an_unknown_type_keeps_its_data_visible(self):
        """Silently dropping it would lose the content with no trace."""
        block = {"type": "mystery", "data": {"text": "keep me"}}
        assert "keep me" in blocks_to_markdown([block])


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


class TestFrontmatter:
    def test_shape(self):
        text = frontmatter(
            "Title", "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
        )
        assert text == (
            "---\n"
            'title: "Title"\n'
            "created: 2026-01-01T00:00:00+00:00\n"
            "updated: 2026-01-02T00:00:00+00:00\n"
            "---\n"
        )

    @pytest.mark.parametrize(
        "title",
        [
            "Notes: plan",  # a colon would break a raw interpolation
            "# draft",  # a leading hash would be read as a comment
            'Has "quotes"',
            "Trailing space ",
            "a: b: c",
            "- looks like a list",
        ],
    )
    def test_a_title_with_yaml_metacharacters_round_trips(self, title):
        """The whole point of JSON-quoting: the parser gets the title back."""
        from wichy.skills.skill import parse_markdown_frontmatter

        text = frontmatter(
            title, "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
        )
        metadata, body = parse_markdown_frontmatter(text + "body")
        assert metadata["title"] == title
        assert body == "body"

    def test_timestamps_are_written_unquoted_iso8601(self):
        """YAML reads an unquoted ISO-8601 timestamp back as a datetime.

        That is the existing bridge's behavior, unchanged here: the value is
        written so a human and any other tool reads a plain timestamp. Asserting
        the written form rather than the parsed type keeps this test about what
        this module produces.
        """
        text = frontmatter(
            "T", "2026-01-01T00:00:00+00:00", "2026-02-03T04:05:06+00:00"
        )
        assert "created: 2026-01-01T00:00:00+00:00" in text
        assert "updated: 2026-02-03T04:05:06+00:00" in text

        from wichy.skills.skill import parse_markdown_frontmatter

        metadata, _ = parse_markdown_frontmatter(text + "x")
        # The parser resolves unquoted timestamps to datetime objects, so compare
        # the instant rather than the text.
        assert metadata["created"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert metadata["updated"] == datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)

    def test_a_raw_interpolation_would_have_broken(self):
        """Records the defect the JSON-quoting exists to avoid."""
        from wichy.skills.skill import parse_markdown_frontmatter

        naive = "---\ntitle: Notes: plan\ncreated: c\nupdated: u\n---\nbody"
        metadata, _ = parse_markdown_frontmatter(naive)
        # The naive form silently yields no metadata at all.
        assert metadata == {}


class TestExportDocument:
    def _document(self, **meta_overrides):
        from wichy.tools.notes.models import Block, BlockDocument, DocumentMeta

        meta = {
            "title": "Exported",
            "slug": "exported",
            "created": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-02T00:00:00+00:00",
        }
        meta.update(meta_overrides)
        return BlockDocument(
            meta=DocumentMeta(**meta),
            blocks=[
                Block(id="blk-a", type="header", data={"text": "H", "level": 2}),
                Block(id="blk-b", type="paragraph", data={"text": "body"}),
            ],
        )

    def test_frontmatter_then_body(self):
        exported = export_markdown(self._document())
        assert exported.startswith("---\n")
        assert 'title: "Exported"' in exported
        assert exported.endswith("## H\n\nbody")

    def test_frontmatter_comes_from_the_document_not_the_source_file(self):
        exported = export_markdown(
            self._document(title="Other", updated="2030-01-01T00:00:00+00:00")
        )
        assert 'title: "Other"' in exported
        assert "2030-01-01T00:00:00+00:00" in exported

    def test_exporting_does_not_mutate_the_document(self):
        document = self._document()
        before = document.model_dump()
        export_markdown(document)
        assert document.model_dump() == before

    def test_exporting_a_markdown_document_returns_the_body_unchanged(self):
        """Byte-identical modulo the regenerated frontmatter block."""
        raw = "# Title\n\n- item\n\n| table |\n|---|\n"
        exported = export_legacy(
            raw, "Legacy", "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
        )
        assert exported.endswith(raw)
        assert exported == (
            frontmatter(
                "Legacy", "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
            )
            + raw
        )

    def test_a_legacy_body_that_looks_like_frontmatter_is_not_reparsed(self):
        body = "---\nnot: frontmatter\ntext: here\n---\n"
        exported = export_legacy(body, "T", "c", "u")
        assert exported.endswith(body)


# ---------------------------------------------------------------------------
# Lossy feature detection
# ---------------------------------------------------------------------------


class TestLossyFeatures:
    def test_a_clean_document_reports_nothing(self):
        """A warning the user cannot act on is worse than no warning."""
        assert lossy_features("# Title\n\nSome text.\n\n- a\n") == []

    def test_detects_a_table(self):
        body = "| a | b |\n|---|---|\n| 1 | 2 |\n"
        assert "tables" in lossy_features(body)

    def test_detects_a_leading_pipe_row(self):
        assert "tables" in lossy_features("| a | b |\n")

    def test_does_not_call_every_pipe_a_table(self):
        assert lossy_features("use a | b as a separator\n") == []

    def test_detects_a_footnote_definition(self):
        assert "footnotes" in lossy_features("[^1]: the note\n")

    def test_detects_a_footnote_reference(self):
        assert "footnotes" in lossy_features("text[^1]\n")

    def test_detects_inline_links(self):
        assert "inline links and images" in lossy_features("see [x](http://y)\n")

    def test_detects_images(self):
        assert "inline links and images" in lossy_features("![alt](http://y)\n")

    def test_detects_raw_html(self):
        assert "raw HTML" in lossy_features("<div>hi</div>\n")

    def test_reports_multiple_features(self):
        body = "| a |\n|---|\n\n[^1]\n\n[x](http://y)\n"
        found = lossy_features(body)
        assert set(found) >= {"tables", "footnotes", "inline links and images"}

    def test_features_are_not_repeated(self):
        body = "[a](http://x)\n[b](http://y)\n[c](http://z)\n"
        assert lossy_features(body) == ["inline links and images"]

    def test_empty_body_reports_nothing(self):
        assert lossy_features("") == []


class TestCountBlocks:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ("", 0),
            ("one", 1),
            ("one\n\ntwo", 2),
            ("# a\n\n- b\n\n- [ ] c", 3),
            ("```\nx\n```\n\ntext", 2),
        ],
    )
    def test_counts(self, body, expected):
        assert count_blocks(body) == expected

    def test_count_agrees_with_conversion(self):
        body = "# h\n\ntext\n\n- a\n- b\n\n> q\n\n---\n\n```\nc\n```\n"
        assert count_blocks(body) == len(markdown_to_blocks(body))


class TestConversionAlwaysTerminates:
    """A converter that can hang is an outage in a request handler.

    The main loop and ``_classify`` decide separately whether a line starts a
    construct. If a rule is ever added to one and not the other, a paragraph
    would break on a line it never consumes and the loop would spin forever.
    The converter defends itself by forcing a step when an iteration consumed
    nothing, so the worst case is one mis-classified line.
    """

    def test_a_disagreeing_boundary_test_cannot_hang(self, monkeypatch):
        """Simulate the disagreement directly and require termination.

        The hang needs the two halves to disagree in one specific direction: the
        boundary test claims a line starts a construct, while the main loop has
        no rule that accepts it. The paragraph branch then breaks on its first
        line, collects nothing, and leaves the index where it was -- every pass
        producing an empty paragraph and no progress.
        """
        from wichy.tools.notes import markdown as md

        # No main-loop rule matches "zzz" -- it falls through to the paragraph
        # branch -- while the boundary test insists it starts a code block.
        monkeypatch.setattr(
            md, "_classify", lambda line: "code" if line.strip() == "zzz" else None
        )

        blocks = md.markdown_to_blocks("zzz\n")
        assert isinstance(blocks, list)
        # The guard turned the disagreement into a wrong block, not a hang.
        assert blocks == [{"type": "paragraph", "data": {"text": "zzz"}}]

    @pytest.mark.parametrize(
        "body",
        [
            "---",
            "\n\n\n",
            "   ",
            "\t",
            "---\n---\n---",
            "```\n```",
            ">",
            "-",
            "#",
            "|",
            "[^]",
            "- [ ]",
            "1. ",
        ],
    )
    def test_odd_inputs_terminate_and_produce_blocks(self, body):
        """Each of these is a line the classifier might mishandle."""
        blocks = markdown_to_blocks(body)
        assert isinstance(blocks, list)
        # Every block is valid enough to persist.
        assert all(b["type"] and isinstance(b["data"], dict) for b in blocks)

    def test_every_line_of_a_large_body_is_accounted_for(self):
        """No input is silently dropped: the text survives in some block."""
        body = "\n".join(f"line {i}" for i in range(200))
        blocks = markdown_to_blocks(body)
        assert len(blocks) == 1
        assert blocks[0]["data"]["text"].count("\n") == 199
