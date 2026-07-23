"""Treemap renderer (matplotlib).

Hierarchical area-based rectangles. Uses the ``squarify`` algorithm
implemented inline (no external dependency beyond matplotlib).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.patches as mpatches

from wichy.tools.viz.config_models import TreemapChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def _squarify(
    values: list[float],
    x: float,
    y: float,
    dx: float,
    dy: float,
) -> list[tuple[float, float, float, float]]:
    """Compute treemap rectangles using the squarify algorithm.

    Returns a list of (x, y, width, height) tuples.
    """
    if not values:
        return []

    total = sum(values)
    if total <= 0:
        return [(x, y, dx / len(values), dy) for _ in values]

    normalized = [v / total for v in values]
    rects: list[tuple[float, float, float, float]] = []

    def _worst_ratio(side: float, area: list[float]) -> float:
        """Worst aspect ratio for a row of squares."""
        if not area:
            return float("inf")
        total_area = sum(area)
        max_area = max(area)
        min_area = min(area)
        return max(
            (side**2 * max_area) / total_area**2,
            total_area**2 / (side**2 * min_area),
        )

    def _layout_row(
        row: list[float], side: float, x: float, y: float, dx: float, dy: float
    ) -> list[tuple[float, float, float, float]]:
        """Layout a single row of rectangles."""
        row_total = sum(row)
        rects_out: list[tuple[float, float, float, float]] = []
        if dx > dy:
            # Row is vertical
            row_height = row_total / dx
            cy = y
            for v in row:
                w = v / row_height
                rects_out.append((x, cy, row_height, w))
                cy += w
            new_x = x + row_height
            return rects_out, new_x, y, dx - row_height, dy  # type: ignore[return-value]
        else:
            # Row is horizontal
            row_width = row_total / dy
            cx = x
            for v in row:
                h = v / row_width
                rects_out.append((cx, y, w, h))
                cx += w  # type: ignore[name-defined]
            new_y = y + row_width
            return rects_out, x, new_y, dx, dy - row_width  # type: ignore[return-value]

    remaining = list(normalized)
    cur_x, cur_y, cur_dx, cur_dy = x, y, dx, dy

    while remaining:
        if len(remaining) == 1:
            rects.append((cur_x, cur_y, cur_dx, cur_dy))
            break

        side = min(cur_dx, cur_dy)
        row: list[float] = []

        while remaining:
            if not row:
                row.append(remaining[0])
                remaining = remaining[1:]
            else:
                # Check if adding the next value improves the worst ratio
                new_row = row + [remaining[0]]
                if _worst_ratio(side, new_row) <= _worst_ratio(side, row):
                    row = new_row
                    remaining = remaining[1:]
                else:
                    break

        # Layout this row
        if cur_dx >= cur_dy:
            row_height = sum(row) / cur_dx
            cy = cur_y
            for v in row:
                w = v / row_height
                rects.append((cur_x, cy, row_height, w))
                cy += w
            cur_x += row_height
            cur_dx -= row_height
        else:
            row_width = sum(row) / cur_dy
            cx = cur_x
            for v in row:
                h = v / row_width
                rects.append((cx, cur_y, w, h))  # type: ignore[name-defined]
                cx += h
            cur_y += row_width
            cur_dy -= row_width

    return rects


def render_treemap(
    data_rows: list[dict[str, Any]],
    config: TreemapChartConfig,
    output_path: Path,
) -> None:
    """Render a treemap and save it to output_path.

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

    # Filter out zero or negative values
    filtered = [(lbl, v) for lbl, v in zip(labels, values) if v > 0]
    if not filtered:
        filtered = [(lbl, 1.0) for lbl, v in zip(labels, values)]

    f_labels = [f[0] for f in filtered]
    f_values = [f[1] for f in filtered]

    fig, ax = create_figure(config)
    colors = get_colors(config)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.invert_yaxis()
    ax.axis("off")

    rects = _squarify(f_values, 0, 0, 1, 1)

    for i, (rx, ry, rw, rh) in enumerate(rects):
        color = colors[i % len(colors)]
        rect = mpatches.Rectangle(
            (rx, ry), rw, rh, facecolor=color, edgecolor="white", linewidth=1
        )
        ax.add_patch(rect)
        # Add label if rectangle is large enough
        if rw > 0.05 and rh > 0.03:
            ax.text(
                rx + rw / 2,
                ry + rh / 2,
                f_labels[i],
                ha="center",
                va="center",
                fontsize=config.font_size - 2,
                color="white",
                fontweight="bold",
            )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="treemap",
    label="Treemap",
    category="hierarchical",
    icon="🌳",
    field_roles=[
        FieldRole(name="labels", type="category", required=True),
        FieldRole(name="values", type="numeric", required=True),
        FieldRole(name="parent", type="category", required=False),
    ],
    config_model=TreemapChartConfig,
    renderer=render_treemap,
)
