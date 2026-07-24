"""Treemap renderer (matplotlib).

Hierarchical area-based rectangles. Uses the squarify algorithm to produce
rectangles with good aspect ratios.
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
            """Worst aspect ratio in a row of rectangles laid along `side`."""
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


def render_treemap(
    data_rows: list[dict[str, Any]],
    config: TreemapChartConfig,
    output_path: Path,
) -> None:
    """Render a treemap and save it to output_path.

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
