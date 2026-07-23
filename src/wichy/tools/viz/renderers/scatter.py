"""Scatter plot renderer (matplotlib).

Supports optional color-by-category and size-by-numeric encoding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


from wichy.tools.viz.config_models import ScatterChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_scatter(
    data_rows: list[dict[str, Any]],
    config: ScatterChartConfig,
    output_path: Path,
) -> None:
    """Render a scatter plot and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Scatter chart config (x, y, color_by, size_by).
        output_path: Destination PNG file path.
    """
    x_vals = extract_column(data_rows, config.x)
    y_vals = extract_column(data_rows, config.y)

    fig, ax = create_figure(config)
    colors = get_colors(config)

    scatter_kwargs: dict[str, Any] = {}

    if config.color_by:
        color_vals = extract_column(data_rows, config.color_by)
        # If categorical, use discrete colors; if numeric, use colormap
        if all(isinstance(c, (int, float)) for c in color_vals if c is not None):
            scatter_kwargs["c"] = color_vals
            scatter_kwargs["cmap"] = "viridis"
        else:
            # Categorical: map each category to a color
            unique_cats = list(
                dict.fromkeys(str(c) for c in color_vals if c is not None)
            )
            cat_colors = {
                cat: colors[i % len(colors)] for i, cat in enumerate(unique_cats)
            }
            point_colors = [cat_colors.get(str(c), colors[0]) for c in color_vals]
            scatter_kwargs["c"] = point_colors
            # Add legend
            for cat in unique_cats:
                ax.scatter([], [], c=cat_colors[cat], label=cat)
            ax.legend(fontsize=config.font_size - 2)

    if config.size_by:
        size_vals = extract_column(data_rows, config.size_by)
        clean_sizes = [s for s in size_vals if s is not None]
        if clean_sizes:
            min_s = min(clean_sizes)
            max_s = max(clean_sizes)
            if max_s > min_s:
                scatter_kwargs["s"] = [
                    10 + 200 * ((s - min_s) / (max_s - min_s)) if s is not None else 10
                    for s in size_vals
                ]
            else:
                scatter_kwargs["s"] = 30

    ax.scatter(x_vals, y_vals, alpha=0.7, **scatter_kwargs)
    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="scatter",
    label="Scatter Plot",
    category="basic",
    icon="🔵",
    field_roles=[
        FieldRole(name="x", type="numeric", required=True),
        FieldRole(name="y", type="numeric", required=True),
        FieldRole(name="color_by", type="category", required=False),
        FieldRole(name="size_by", type="numeric", required=False),
    ],
    config_model=ScatterChartConfig,
    renderer=render_scatter,
)
