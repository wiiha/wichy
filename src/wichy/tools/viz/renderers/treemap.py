"""Treemap renderer (matplotlib).

Hierarchical area-based rectangles. Uses the squarify algorithm to produce
rectangles with good aspect ratios.

When a ``parent`` column is provided, a two-level hierarchy is rendered:
parent rectangles are laid out first, then children are squarified within
each parent's rectangle. When no ``parent`` is provided, a flat single-level
treemap is produced.

Labels are placed inside each rectangle with automatic truncation and
font-size reduction so they fit within the available space. The numeric
value is shown beneath the label.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

from wichy.tools.viz.config_models import TreemapChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    DEFAULT_COLORS,
    apply_theme,
    create_figure,
    extract_column,
    mpl_to_png,
)

# ---------------------------------------------------------------------------
# Squarify algorithm
# ---------------------------------------------------------------------------


def _squarify(
    values: list[float],
    x: float,
    y: float,
    dx: float,
    dy: float,
) -> list[tuple[float, float, float, float]]:
    """Compute treemap rectangles using the squarify algorithm.

    Produces rectangles with aspect ratios as close to 1 as possible.

    Args:
        values: Normalized values (fractions of total, summing to ~1.0).
        x, y: Top-left corner of the available area.
        dx, dy: Width and height of the available area.

    Returns:
        List of (x, y, width, height) tuples.
    """
    if not values:
        return []

    total = sum(values)
    if total <= 0:
        return [
            (x + i * dx / len(values), y, dx / len(values), dy)
            for i in range(len(values))
        ]

    # Scale values to fill the area
    areas = [v * dx * dy / total for v in values]

    rects: list[tuple[float, float, float, float]] = []
    remaining = list(areas)
    cur_x, cur_y, cur_dx, cur_dy = x, y, dx, dy

    while remaining:
        if len(remaining) == 1:
            rects.append((cur_x, cur_y, cur_dx, cur_dy))
            break

        # Determine layout direction: slice along the shorter dimension
        # Layout a "row" of rectangles stacked along the shorter side
        short_side = min(cur_dx, cur_dy)
        row: list[float] = []

        def _worst_ratio(row_areas: list[float], side: float) -> float:
            """Worst aspect ratio in a row of rectangles laid along ``side``."""
            if not row_areas:
                return float("inf")
            total_area = sum(row_areas)
            max_area = max(row_areas)
            min_area = min(row_areas)
            return max(
                (side**2 * max_area) / total_area**2,
                total_area**2 / (side**2 * min_area),
            )

        # Greedily build the row: keep adding rectangles while it improves
        # the worst aspect ratio
        while remaining:
            if not row:
                row.append(remaining[0])
                remaining = remaining[1:]
            else:
                new_row = row + [remaining[0]]
                if _worst_ratio(new_row, short_side) <= _worst_ratio(row, short_side):
                    row = new_row
                    remaining = remaining[1:]
                else:
                    break

        # Layout this row
        row_total = sum(row)
        if cur_dx >= cur_dy:
            # Layout row as a vertical strip on the left (full height cur_dy)
            strip_width = row_total / cur_dy
            cy = cur_y
            for area in row:
                h = area / strip_width
                rects.append((cur_x, cy, strip_width, h))
                cy += h
            cur_x += strip_width
            cur_dx -= strip_width
        else:
            # Layout row as a horizontal strip on top (full width cur_dx)
            strip_height = row_total / cur_dx
            cx = cur_x
            for area in row:
                w = area / strip_height
                rects.append((cx, cur_y, w, strip_height))
                cx += w
            cur_y += strip_height
            cur_dy -= strip_height

    return rects


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

# Padding inside each rectangle (fraction of rect dimension)
_LABEL_PAD = 0.01


def _truncate_label(text: str, max_chars: int) -> str:
    """Truncate *text* to *max_chars*, appending ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "\u2026"


def _fit_fontsize(
    fig: plt.Figure,
    text: str,
    box_w: float,
    box_h: float,
    target_fontsize: float,
) -> float:
    """Reduce font size until *text* fits within *box_w* x *box_h* (in data units).

    Uses a heuristic: estimate rendered text width from figure DPI and
    average character width. Never goes below 5pt.
    """
    if box_w <= 0 or box_h <= 0:
        return 0.0  # signal "don't draw"

    min_fs = 5.0
    fs = target_fontsize
    # Approximate char width in points: ~0.55 * fontsize for bold sans
    char_w_pt = 0.55
    # Convert data-units to points — width and height differ because
    # the figure may not be square. Data range is 0..1 in both axes.
    fig_w_pt = fig.get_size_inches()[0] * 72.0  # 1 inch = 72 pt
    fig_h_pt = fig.get_size_inches()[1] * 72.0
    avail_w_pt = (box_w - 2 * _LABEL_PAD) * fig_w_pt
    avail_h_pt = (box_h - 2 * _LABEL_PAD) * fig_h_pt

    while fs >= min_fs:
        text_w_pt = len(text) * char_w_pt * fs
        text_h_pt = fs * 1.3  # line height
        if text_w_pt <= avail_w_pt and text_h_pt <= avail_h_pt:
            return fs
        fs -= 1.0

    # Even min font size doesn't fit — check if it's at least somewhat reasonable
    if avail_w_pt > 15 and avail_h_pt > 8:
        return min_fs
    return 0.0


def _place_label(
    fig: plt.Figure,
    ax: plt.Axes,
    cx: float,
    cy: float,
    rw: float,
    rh: float,
    label: str,
    value: float | None,
    target_fontsize: float,
    text_color: str = "white",
) -> None:
    """Place a label (and optional value) centered in a rectangle.

    If the rectangle is too small for the label even at minimum font size,
    the label is skipped entirely.
    """
    # Truncate label to a reasonable max length first
    max_len = 25
    truncated = _truncate_label(label, max_len)

    # Build display text: label on first line, value on second
    value_str = f"{value:,.0f}" if value is not None else None

    # Check if label fits
    label_fs = _fit_fontsize(fig, truncated, rw, rh, target_fontsize)
    if label_fs == 0.0:
        # Rectangle too small for any text — skip
        return

    # If we also need a value line, reserve vertical space for it
    if value_str is not None:
        value_text = value_str
        value_fs_target = label_fs - 1
        # Two lines need more height; check if it fits with both lines
        two_line_height = label_fs * 1.3 + value_fs_target * 1.3
        fig_h_pt = fig.get_size_inches()[1] * 72.0
        avail_h_pt = (rh - 2 * _LABEL_PAD) * fig_h_pt
        if two_line_height > avail_h_pt:
            # Not enough room for two lines — show label only
            ax.text(
                cx,
                cy,
                truncated,
                ha="center",
                va="center",
                fontsize=label_fs,
                color=text_color,
                fontweight="bold",
            )
            return

        # Check value line width
        value_fs = _fit_fontsize(fig, value_text, rw, rh * 0.4, value_fs_target)
        if value_fs == 0.0:
            # Value doesn't fit on its own — show label only
            ax.text(
                cx,
                cy,
                truncated,
                ha="center",
                va="center",
                fontsize=label_fs,
                color=text_color,
                fontweight="bold",
            )
            return

        # Draw both lines — gap in data units uses fig height for conversion
        line_gap = label_fs * 0.7  # points
        line_gap_data = line_gap / fig_h_pt
        ax.text(
            cx,
            cy + line_gap_data / 2,
            truncated,
            ha="center",
            va="center",
            fontsize=label_fs,
            color=text_color,
            fontweight="bold",
        )
        ax.text(
            cx,
            cy - line_gap_data / 2,
            value_text,
            ha="center",
            va="center",
            fontsize=value_fs,
            color=text_color,
            alpha=0.85,
        )
    else:
        ax.text(
            cx,
            cy,
            truncated,
            ha="center",
            va="center",
            fontsize=label_fs,
            color=text_color,
            fontweight="bold",
        )


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------


def render_treemap(
    data_rows: list[dict[str, Any]],
    config: TreemapChartConfig,
    output_path: Path,
) -> None:
    """Render a treemap and save it to *output_path*.

    Each row provides a label and a value. The value determines the area
    of the rectangle. If a parent column is provided, a two-level hierarchy
    is rendered (parent rectangles containing child rectangles).

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Treemap config (labels, values, parent).
        output_path: Destination PNG file path.
    """
    labels = [
        str(lbl) if lbl is not None else ""
        for lbl in extract_column(data_rows, config.labels)
    ]
    raw_values = extract_column(data_rows, config.values)
    values = [float(v) if v is not None else 0.0 for v in raw_values]

    if config.parent:
        parents = [
            str(p) if p is not None else ""
            for p in extract_column(data_rows, config.parent)
        ]
    else:
        parents = [""] * len(labels)

    # Separate root nodes from child nodes
    # A root node: parent == "" or parent not present in labels
    label_set = set(labels)
    root_entries: list[tuple[str, float, int]] = []  # (label, value, orig_index)
    child_entries: list[tuple[str, float, str, int]] = []  # (label, value, parent, idx)

    for i, (lbl, val, par) in enumerate(zip(labels, values, parents)):
        if par == "" or par not in label_set:
            root_entries.append((lbl, val, i))
        else:
            child_entries.append((lbl, val, par, i))

    # For hierarchical treemaps: compute effective root values
    # If a root has children, its area = max(own value, sum of children)
    # so aggregate-only roots (value=0) still get space.
    if child_entries:
        for ri, (rlbl, rval, ridx) in enumerate(root_entries):
            child_sum = sum(v for _, v, p, _ in child_entries if p == rlbl)
            effective = max(rval, child_sum)
            root_entries[ri] = (rlbl, effective, ridx)

    # If no root entries but we have child entries, promote all to flat
    if not root_entries and child_entries:
        root_entries = [(lbl, val, i) for lbl, val, _, i in child_entries]
        child_entries = []

    # Filter out zero/negative root values
    root_entries = [(lbl, v, i) for lbl, v, i in root_entries if v > 0]
    if not root_entries:
        # Fallback: give everything equal weight
        root_entries = (
            [
                (lbl, 1.0, i)
                for lbl, _, i in [
                    (lbl, val, i)
                    for lbl, val, i in zip(labels, values, range(len(labels)))
                ]
            ]
            if labels
            else []
        )

    if not root_entries:
        fig, ax = create_figure(config)
        apply_theme(fig, ax, config)
        mpl_to_png(fig, config, output_path)
        return

    # --- Setup figure ---
    fig, ax = create_figure(config)
    colors = config.color_palette if config.color_palette else DEFAULT_COLORS
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.invert_yaxis()
    ax.axis("off")

    # Layout roots
    root_values = [v for _, v, _ in root_entries]
    root_rects = _squarify(root_values, 0, 0, 1, 1)

    # Track color→label pairs for the legend.
    # For hierarchical charts, only leaf (child) nodes go in the legend.
    # For flat charts, all nodes appear.
    legend_entries: list[tuple[str, str]] = []

    for ri, (rx, ry, rw, rh) in enumerate(root_rects):
        rlbl, rval, ridx = root_entries[ri]
        root_color = colors[ridx % len(colors)]

        # Find children of this root
        children = [
            (lbl, val, idx)
            for lbl, val, par, idx in child_entries
            if par == rlbl and val > 0
        ]

        if children:
            # --- Hierarchical: draw children inside parent rect ---
            # Compute header height in data units that ensures enough
            # pixel space for the parent label text.
            fig_w_in, fig_h_in = fig.get_size_inches()
            fig_h_pt = fig_h_in * 72.0
            # Data range is 0..1 so 1 data-unit = fig_h_pt points
            # Header needs ~font_size * 1.5 points of height
            header_pt_needed = config.font_size * 1.5
            header_h_min = header_pt_needed / fig_h_pt
            header_h = min(rh * 0.15, 0.08, header_h_min)
            if header_h < 0.015 or rh < 0.05:
                header_h = 0.0  # too small for a header

            # Parent rectangle background (lighter shade)
            parent_rect = mpatches.Rectangle(
                (rx, ry),
                rw,
                rh,
                facecolor=root_color,
                edgecolor="white",
                linewidth=2,
                alpha=0.25,
            )
            ax.add_patch(parent_rect)

            # Header strip — a semi-transparent dark band to visually
            # separate the parent label from child rectangles
            if header_h > 0:
                header_rect = mpatches.Rectangle(
                    (rx, ry),
                    rw,
                    header_h,
                    facecolor=root_color,
                    edgecolor="none",
                    alpha=0.5,
                )
                ax.add_patch(header_rect)
                _place_label(
                    fig,
                    ax,
                    rx + rw / 2,
                    ry + header_h / 2,
                    rw,
                    header_h,
                    rlbl,
                    None,
                    target_fontsize=config.font_size,
                    text_color="white",
                )

            # Squarify children in the remaining area
            child_area_y = ry + header_h
            child_area_h = rh - header_h
            if child_area_h > 0.01 and rw > 0.01:
                child_values = [v for _, v, _ in children]
                child_rects = _squarify(
                    child_values, rx, child_area_y, rw, child_area_h
                )

                for ci, (cx_, cy_, cw, ch) in enumerate(child_rects):
                    clbl, cval, cidx = children[ci]
                    # Child color: same hue family as parent, varying shade
                    child_color = colors[(ridx + ci + 1) % len(colors)]
                    rect = mpatches.Rectangle(
                        (cx_, cy_),
                        cw,
                        ch,
                        facecolor=child_color,
                        edgecolor="white",
                        linewidth=1,
                    )
                    ax.add_patch(rect)
                    _place_label(
                        fig,
                        ax,
                        cx_ + cw / 2,
                        cy_ + ch / 2,
                        cw,
                        ch,
                        clbl,
                        cval,
                        target_fontsize=config.font_size - 2,
                        text_color="white",
                    )
                    legend_entries.append((clbl, child_color))
        else:
            # --- Flat: this root has no children, draw as a single rect ---
            rect = mpatches.Rectangle(
                (rx, ry),
                rw,
                rh,
                facecolor=root_color,
                edgecolor="white",
                linewidth=1,
            )
            ax.add_patch(rect)
            _place_label(
                fig,
                ax,
                rx + rw / 2,
                ry + rh / 2,
                rw,
                rh,
                rlbl,
                rval,
                target_fontsize=config.font_size,
                text_color="white",
            )
            legend_entries.append((rlbl, root_color))

    # Add legend — deduplicate by label (first color wins)
    if legend_entries:
        seen: set[str] = set()
        handles: list[mpatches.Patch] = []
        labels_out: list[str] = []
        for lbl, clr in legend_entries:
            if lbl not in seen:
                seen.add(lbl)
                handles.append(mpatches.Patch(facecolor=clr, edgecolor="white"))
                labels_out.append(lbl)
        if handles:
            ax.legend(
                handles,
                labels_out,
                fontsize=config.font_size - 2,
                loc="center left",
                bbox_to_anchor=(1.01, 0.5),
                frameon=False,
            )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="treemap",
    label="Treemap",
    category="hierarchical",
    icon="\U0001f333",
    field_roles=[
        FieldRole(name="labels", type="category", required=True),
        FieldRole(name="values", type="numeric", required=True),
        FieldRole(name="parent", type="category", required=False),
    ],
    config_model=TreemapChartConfig,
    renderer=render_treemap,
)
