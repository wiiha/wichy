"""Bar chart renderer (matplotlib).

Supports vertical/horizontal orientation, grouped/stacked modes, and
optional color-by-category.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import BarChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_bar(
    data_rows: list[dict[str, Any]],
    config: BarChartConfig,
    output_path: Path,
) -> None:
    """Render a bar chart and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Bar chart config (x, y, color_by, orientation, mode).
        output_path: Destination PNG file path.
    """
    x_vals = extract_column(data_rows, config.x)
    y_vals = extract_column(data_rows, config.y)

    fig, ax = create_figure(config)
    colors = get_colors(config)

    if config.color_by:
        color_vals = extract_column(data_rows, config.color_by)
        # Group by color_by value
        groups: dict[str, list[tuple[Any, Any]]] = {}
        for x, y, c in zip(x_vals, y_vals, color_vals):
            c_key = str(c) if c is not None else "None"
            groups.setdefault(c_key, []).append((x, y))

        # Get unique x categories preserving order
        x_cats: list[str] = []
        x_seen: set[str] = set()
        for x in x_vals:
            x_key = str(x) if x is not None else "None"
            if x_key not in x_seen:
                x_seen.add(x_key)
                x_cats.append(x_key)

        x_idx = {c: i for i, c in enumerate(x_cats)}
        n_groups = len(groups)
        bar_width = 0.8 / n_groups if config.mode != "stacked" else 0.8

        if config.mode == "stacked":
            bottom = np.zeros(len(x_cats))
            for i, (gname, pairs) in enumerate(groups.items()):
                heights = np.zeros(len(x_cats))
                for x, y in pairs:
                    heights[x_idx[str(x) if x is not None else "None"]] = y or 0
                orientation = config.orientation
                if orientation == "h":
                    ax.barh(
                        range(len(x_cats)),
                        heights,
                        left=bottom,
                        height=bar_width,
                        label=gname,
                        color=colors[i % len(colors)],
                    )
                else:
                    ax.bar(
                        range(len(x_cats)),
                        heights,
                        bottom=bottom,
                        width=bar_width,
                        label=gname,
                        color=colors[i % len(colors)],
                    )
                bottom += heights
        else:
            for i, (gname, pairs) in enumerate(groups.items()):
                heights = np.zeros(len(x_cats))
                for x, y in pairs:
                    heights[x_idx[str(x) if x is not None else "None"]] = y or 0
                offsets = np.array(range(len(x_cats))) + i * bar_width
                if config.orientation == "h":
                    ax.barh(
                        offsets,
                        heights,
                        height=bar_width,
                        label=gname,
                        color=colors[i % len(colors)],
                    )
                else:
                    ax.bar(
                        offsets,
                        heights,
                        width=bar_width,
                        label=gname,
                        color=colors[i % len(colors)],
                    )

        ax.set_xticks(range(len(x_cats)))
        ax.set_xticklabels(x_cats, rotation=45, ha="right")
        ax.legend(
            fontsize=config.font_size - 2, loc="center left", bbox_to_anchor=(1.01, 0.5)
        )
    else:
        x_cats = [str(x) if x is not None else "None" for x in x_vals]
        safe_y = [y if y is not None else 0 for y in y_vals]
        if config.orientation == "h":
            ax.barh(range(len(x_cats)), safe_y, color=colors[0])
            ax.set_yticks(range(len(x_cats)))
            ax.set_yticklabels(x_cats)
        else:
            ax.bar(range(len(x_cats)), safe_y, color=colors[0])
            ax.set_xticks(range(len(x_cats)))
            ax.set_xticklabels(x_cats, rotation=45, ha="right")

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="bar",
    label="Bar Chart",
    category="basic",
    icon="📊",
    field_roles=[
        FieldRole(name="x", type="category", required=True),
        FieldRole(name="y", type="numeric", required=True),
        FieldRole(name="color_by", type="category", required=False),
    ],
    config_model=BarChartConfig,
    renderer=render_bar,
)
