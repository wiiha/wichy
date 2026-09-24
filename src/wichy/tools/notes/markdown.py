"""Markdown <-> block conversion, both directions.

This module exists for two features that look similar and are not:

- **Conversion** (``markdown_to_blocks``) turns a legacy note's body into blocks.
  It is a one-off migration a user explicitly asks for, and the original ``.md``
  is kept as a backup.
- **Export** (``blocks_to_markdown``) renders a document as markdown for a human
  or another tool. It is read-only and changes nothing.

Neither direction is a supported round trip, and no import path should be added.
The mapping is lossy in ways that cannot be fixed without making the output
worse: paragraph text that happens to read as ``- item`` re-imports as a list,
``delimiter`` collides with the frontmatter fence, adjacent lists of different
styles merge when re-read, and the editor-only block types (``question``,
``decision``, ``todo``) have no markdown form that reads back as themselves.
Export is therefore deliberately one-way.

Line classification is ordered, and the order is normative. The one rule that
depends on it is that a checklist line must be tested before a list line, because
``- [ ] item`` matches both patterns and would otherwise become a list item whose
text is the literal ``[ ] item``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

#: A heading: one to six hashes followed by a space.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")

#: A checklist item. The `x` may be upper or lower case, and the marker may also
#: be empty or a plain space, all of which mean unchecked except `x`.
_CHECKLIST_RE = re.compile(r"^[-*]\s+\[([ xX])\]\s*(.*)$")

#: An unordered list item.
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")

#: An ordered list item, numbered with any digits. The number itself is not
#: preserved: a list block stores one style for all its items, so the original
#: numbering cannot be represented, and pretending otherwise would imply an
#: exactness the format does not have.
_ORDERED_RE = re.compile(r"^\d+[.)]\s+(.*)$")

#: A blockquote line.
_QUOTE_RE = re.compile(r"^>\s?(.*)$")

#: A fenced code block opener, capturing the info string (the language).
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*([\w+-]*)\s*$")

#: A thematic break, which maps to a delimiter block.
_DELIMITER_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")


def _is_blank(line: str) -> bool:
    """Whether a line is empty or only whitespace."""
    return not line.strip()


def markdown_to_blocks(body: str) -> list[dict[str, Any]]:
    """Convert a markdown body into block mappings.

    Args:
        body: The note body, with any frontmatter already stripped.

    Returns:
        Block mappings of ``{"type", "data"}`` in document order. A blank body
        yields no blocks.

    Classification order (first match wins):
    fenced code, delimiter, header, checklist, list, quote, paragraph.
    """
    lines = body.split("\n")
    blocks: list[dict[str, Any]] = []
    index = 0

    while index < len(lines):
        # Progress is guaranteed structurally rather than by inspection. Every
        # branch below either consumes lines or is the paragraph fallback, and
        # the fallback consults _classify to decide where a paragraph ends. If a
        # future rule is added to one of those two places and not the other, the
        # paragraph branch would break on a line it never consumes and this loop
        # would spin forever -- wedging whatever served the request. Recording
        # the entry position and forcing a step turns that into one wrong block
        # instead of a hang.
        start_of_iteration = index
        line = lines[index]

        # 1. Fenced code consumes everything up to the closing fence, so the
        #    lines inside it are never classified as anything else.
        fence = _FENCE_RE.match(line)
        if fence is not None:
            marker = fence.group(1)[0]
            language = fence.group(2)
            index += 1
            code_lines: list[str] = []
            opener_length = len(fence.group(1))
            while index < len(lines):
                closing = _FENCE_RE.match(lines[index])
                # A closing fence must use the same character AND be at least as
                # long as the opener. Without the length check, a shorter fence
                # inside the body would close the block early and the rest of the
                # code would be classified as ordinary markdown.
                if (
                    closing is not None
                    and closing.group(1)[0] == marker
                    and len(closing.group(1)) >= opener_length
                ):
                    index += 1
                    break
                code_lines.append(lines[index])
                index += 1
            blocks.append(
                {
                    "type": "code",
                    "data": {"code": "\n".join(code_lines), "language": language},
                }
            )
            continue

        # 2. A delimiter: a rule alone on its line. Cannot be confused with a
        #    bullet, which requires a space after its marker.
        if _DELIMITER_RE.match(line.strip()):
            blocks.append({"type": "delimiter", "data": {}})
            index += 1
            continue

        # 3. Header.
        header = _HEADER_RE.match(line)
        if header is not None:
            blocks.append(
                {
                    "type": "header",
                    "data": {"text": header.group(2), "level": len(header.group(1))},
                }
            )
            index += 1
            continue

        # 4. Checklist, BEFORE the list rule. `- [ ] item` matches both, and the
        #    list reading would swallow the marker into the item text.
        checklist = _CHECKLIST_RE.match(line)
        if checklist is not None:
            checked_items: list[dict[str, Any]] = []
            while index < len(lines):
                match = _CHECKLIST_RE.match(lines[index])
                if match is None:
                    break
                checked_items.append(
                    {"text": match.group(2), "checked": match.group(1).lower() == "x"}
                )
                index += 1
            blocks.append({"type": "checklist", "data": {"items": checked_items}})
            continue

        # 5. Lists. One block per run of the same style.
        bullet = _BULLET_RE.match(line)
        ordered = _ORDERED_RE.match(line)
        if bullet is not None or ordered is not None:
            style = "ordered" if ordered is not None else "unordered"
            pattern = _ORDERED_RE if ordered is not None else _BULLET_RE
            list_items: list[str] = []
            while index < len(lines):
                match = pattern.match(lines[index])
                if match is None:
                    break
                list_items.append(match.group(1))
                index += 1
            blocks.append(
                {"type": "list", "data": {"items": list_items, "style": style}}
            )
            continue

        # 6. Quote: one block per run of quoted lines.
        quote = _QUOTE_RE.match(line)
        if quote is not None:
            quoted_lines: list[str] = []
            while index < len(lines):
                match = _QUOTE_RE.match(lines[index])
                if match is None:
                    break
                quoted_lines.append(match.group(1))
                index += 1
            blocks.append(
                {
                    "type": "quote",
                    "data": {"text": "\n".join(quoted_lines), "caption": ""},
                }
            )
            continue

        # Nothing matched. Skip blank lines; otherwise gather a paragraph.
        if _is_blank(line):
            index += 1
            continue

        paragraph_lines: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            if _is_blank(candidate):
                break
            if _classify(candidate) is not None:
                break
            paragraph_lines.append(candidate)
            index += 1
        blocks.append(
            {"type": "paragraph", "data": {"text": "\n".join(paragraph_lines)}}
        )

        if index == start_of_iteration:
            # Nothing was consumed, so a rule and the boundary test disagree
            # about this line. Take it as a one-line paragraph and move on: a
            # single mis-classified line is a bug worth fixing, an infinite loop
            # in a request handler is an outage.
            blocks[-1] = {"type": "paragraph", "data": {"text": line}}
            index += 1

    return blocks


def _classify(line: str) -> str | None:
    """The block type ``line`` would start, or None if it is paragraph text.

    Used to decide where a paragraph ends. Checking the same rules in the same
    order as the main loop is what keeps the two in agreement: a paragraph stops
    exactly at the line that would have started something else.
    """
    if _FENCE_RE.match(line):
        return "code"
    if _DELIMITER_RE.match(line.strip()):
        return "delimiter"
    if _HEADER_RE.match(line):
        return "header"
    if _CHECKLIST_RE.match(line):
        return "checklist"
    if _BULLET_RE.match(line) or _ORDERED_RE.match(line):
        return "list"
    if _QUOTE_RE.match(line):
        return "quote"
    return None


def count_blocks(body: str) -> int:
    """How many blocks ``body`` would convert to, without building them."""
    return len(markdown_to_blocks(body))


def lossy_features(body: str) -> list[str]:
    """Markdown constructs that have no block representation.

    Reported before a conversion so the UI can warn. Only constructs that would
    actually be mangled are named: a table's alignment row would otherwise become
    a paragraph, and a footnote reference would lose its target.

    Detection is deliberately conservative. Claiming a feature is present when it
    is not produces a warning the user cannot act on, which is worse than
    occasionally missing one, because it teaches them to dismiss the dialog.

    Args:
        body: The note body, with frontmatter already stripped.

    Returns:
        Human-readable feature names, de-duplicated and in a stable order.
    """
    found: list[str] = []
    lines = body.split("\n")

    def note(feature: str) -> None:
        if feature not in found:
            found.append(feature)

    for index, line in enumerate(lines):
        stripped = line.strip()

        # A table: a pipe-delimited row followed by a separator row of dashes.
        if "|" in stripped and index + 1 < len(lines):
            follow = lines[index + 1].strip()
            if follow and set(follow) <= set("|-: ") and "-" in follow:
                note("tables")

        # A footnote definition, or a reference to one.
        if re.match(r"^\[\^[^\]]+\]:", stripped) or re.search(
            r"\[\^[^\]]+\]", stripped
        ):
            note("footnotes")

        # Inline images and links lose their target on conversion: a paragraph
        # keeps the raw text, so the URL is preserved but not as a link.
        if re.search(r"!?\[[^\]]*\]\([^)]*\)", stripped):
            note("inline links and images")

        # Raw HTML is kept as literal paragraph text, not rendered.
        if re.match(r"^</?[a-zA-Z][^>]*>", stripped):
            note("raw HTML")

        if re.match(r"^\s*\|", stripped):
            note("tables")

    return found


def block_text(block_type: str, data: Mapping[str, Any] | None) -> str:
    """Render one block's data as plain text, for describing it to the agent.

    The same renderer the markdown export uses, exposed so a change summary can
    show what a block's text actually IS rather than only its type and id. One
    renderer rather than two, because a second one would drift from the export
    and the agent would then be told something the document does not say.

    Args:
        block_type: The block's type.
        data: The block's data, or None for a block that carries none.

    Returns:
        The block's text. Empty for a block with no body, such as a delimiter.
    """
    return _render_block({"type": block_type, "data": dict(data or {})})


def blocks_to_markdown(
    blocks: Iterable[Mapping[str, Any]], *, include_frontmatter: bool = False
) -> str:
    """Render blocks as markdown.

    Args:
        blocks: Block mappings with ``type`` and ``data``.
        include_frontmatter: Unused here; the caller prepends frontmatter. Kept
            so callers can be explicit about which half they are asking for.

    Returns:
        Markdown text, blocks separated by a blank line, without a trailing
        newline.
    """
    rendered = [_render_block(block) for block in blocks]
    # A delimiter already implies surrounding space, so it is joined without the
    # extra blank line that would otherwise produce a double rule.
    pieces = [piece for piece in rendered if piece != ""]
    return "\n\n".join(pieces)


def _render_block(block: Mapping[str, Any]) -> str:
    """Render one block as markdown, or '' if it has no body."""
    block_type = str(block.get("type", ""))
    data = dict(block.get("data") or {})

    if block_type == "paragraph":
        return str(data.get("text", ""))

    if block_type == "header":
        level = int(data.get("level", 1))
        return f"{'#' * level} {data.get('text', '')}"

    if block_type == "list":
        items = list(data.get("items") or [])
        if data.get("style") == "ordered":
            # Numbered from 1. The original numbering is not stored, so it cannot
            # be reproduced; see _ORDERED_RE.
            return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))
        return "\n".join(f"- {item}" for item in items)

    if block_type == "checklist":
        lines = []
        for item in data.get("items") or []:
            mark = "x" if item.get("checked") else " "
            lines.append(f"- [{mark}] {item.get('text', '')}")
        return "\n".join(lines)

    if block_type == "code":
        language = str(data.get("language") or "")
        body = str(data.get("code", ""))
        # The fence must be longer than any run inside the body, or the content
        # would close it early and the rest would leak out as markdown. A bare
        # fence is used when there is no language, per the export rules.
        longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
        fence = "`" * max(3, longest + 1)
        return f"{fence}{language}\n{body}\n{fence}"

    if block_type == "quote":
        text = str(data.get("text", ""))
        lines = [f"> {line}" if line else ">" for line in text.split("\n")]
        caption = str(data.get("caption") or "")
        if caption:
            lines.append(f"> -- {caption}")
        return "\n".join(lines)

    if block_type == "delimiter":
        return "---"

    if block_type == "question":
        answered = " answered" if data.get("answered") else ""
        return f"> [QUESTION{answered}] {data.get('text', '')}"

    if block_type == "decision":
        return f"> [DECISION] {data.get('text', '')}"

    if block_type == "todo":
        mark = "x" if data.get("checked") else " "
        return f"- [{mark}] {data.get('text', '')}"

    # An unknown type would otherwise vanish from the export silently, taking
    # its content with it. Emitting the data keeps the text recoverable.
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def frontmatter(title: str, created: str, updated: str) -> str:
    """Build the YAML frontmatter block the markdown bridge reads.

    The title is emitted as a JSON string, which is valid YAML and round-trips
    quotes, colons and a leading ``#``. Interpolating it raw would produce
    invalid YAML for a title like ``Notes: plan``, and the parser would then
    yield no metadata at all -- silently losing the title and both timestamps.

    Args:
        title: Document title.
        created: ISO-8601 creation time.
        updated: ISO-8601 last-modified time.

    Returns:
        The frontmatter block, including both fences and a trailing newline.
    """
    return (
        "---\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        "---\n"
    )


def export_markdown(document) -> str:
    """Render a block document as a complete markdown file.

    Frontmatter is regenerated from the document's metadata rather than copied
    from any source file, so the timestamps reflect the document rather than
    whatever the original note happened to say. This is what "byte-identical
    modulo the frontmatter block" means for a legacy note's export.

    Args:
        document: The document to export.

    Returns:
        Frontmatter followed by the rendered body.
    """
    meta = document.meta
    body = blocks_to_markdown(
        {"type": block.type, "data": block.data} for block in document.blocks
    )
    return frontmatter(meta.title, meta.created, meta.updated) + body


def export_legacy(raw_body: str, title: str, created: str, updated: str) -> str:
    """Export a markdown-format note.

    The body is returned exactly as stored; only the frontmatter is regenerated.
    Parsing and re-rendering it would not be a no-op, and this direction is
    specified to be byte-identical apart from that block.

    Args:
        raw_body: The note body with frontmatter already stripped.
        title: The note's title.
        created: ISO-8601 creation time.
        updated: ISO-8601 last-modified time.

    Returns:
        Frontmatter followed by the untouched body.
    """
    return frontmatter(title, created, updated) + raw_body


__all__ = [
    "blocks_to_markdown",
    "count_blocks",
    "export_legacy",
    "export_markdown",
    "frontmatter",
    "lossy_features",
    "markdown_to_blocks",
]
