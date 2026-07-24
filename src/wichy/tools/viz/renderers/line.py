"""Line graph renderer (matplotlib).

Supports single and multi-series line graphs with optional date axis
and color-by-category grouping.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates

from wichy.tools.viz.config_models import LineChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_line(
    data_rows: list[dict[str, Any]],
    config: LineChartConfig,
    output_path: Path,
) -> None:
    """Render a line graph and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Line chart config (x, y list, color_by).
        output_path: Destination PNG file path.
    """
    x_vals = extract_column(data_rows, config.x)
    fig, ax = create_figure(config)
    colors = get_colors(config)

    # Try to parse x as dates
    x_dates: list[datetime.datetime] | None = None
    if x_vals and isinstance(x_vals[0], str):
        try:
            x_dates = [datetime.datetime.fromisoformat(v) for v in x_vals]
        except (ValueError, TypeError):
            pass

    x_plot = x_dates if x_dates is not None else x_vals

    if config.color_by:
        color_vals = extract_column(data_rows, config.color_by)
        groups: dict[str, list[int]] = {}
        for i, c in enumerate(color_vals):
            c_key = str(c) if c is not None else "None"
            groups.setdefault(c_key, []).append(i)

        color_idx = 0
        for gname, indices in groups.items():
            gx = [x_plot[i] for i in indices]
            for y_col in config.y:
                y_vals = extract_column(data_rows, y_col)
                gy = [
                    y_vals[i] if y_vals[i] is not None else float("nan")
                    for i in indices
                ]
                ax.plot(
                    gx,
                    gy,
                    marker="o",
                    markersize=3,
                    label=f"{gname} — {y_col}",
                    color=colors[color_idx % len(colors)],
                )
                color_idx += 1
    else:
        for i, y_col in enumerate(config.y):
            y_vals = extract_column(data_rows, y_col)
            # Replace None with nan so matplotlib draws gaps, not crashes
            safe_y = [y if y is not None else float("nan") for y in y_vals]
            ax.plot(
                x_plot,
                safe_y,
                marker="o",
                markersize=3,
                label=y_col,
                color=colors[i % len(colors)],
            )

    if x_dates is not None:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.autofmt_xdate(rotation=45)

    ax.legend(
        fontsize=config.font_size - 2, loc="center left", bbox_to_anchor=(1.01, 0.5)
    )
    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="line",
    label="Line Graph",
    category="basic",
    icon="📉",
    field_roles=[
        FieldRole(name="x", type="any", required=True),
        FieldRole(name="y", type="numeric", required=True, multiple=True),
        FieldRole(name="color_by", type="category", required=False),
    ],
    config_model=LineChartConfig,
    renderer=render_line,
)
