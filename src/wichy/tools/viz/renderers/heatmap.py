"""Heatmap renderer (matplotlib).

2D grid colored by value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import HeatmapChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    mpl_to_png,
)


def render_heatmap(
    data_rows: list[dict[str, Any]],
    config: HeatmapChartConfig,
    output_path: Path,
) -> None:
    """Render a heatmap and save it to output_path.

    The data is expected in "long" format: each row has an x category, a y
    category, and a numeric value. The renderer pivots this into a 2D matrix.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Heatmap config (x, y, value).
        output_path: Destination PNG file path.
    """
    x_raw = extract_column(data_rows, config.x)
    y_raw = extract_column(data_rows, config.y)
    v_raw = extract_column(data_rows, config.value)

    # Get unique x and y categories (preserving order of first appearance)
    x_cats: list[str] = []
    y_cats: list[str] = []
    x_seen: set[str] = set()
    y_seen: set[str] = set()
    for xv, yv in zip(x_raw, y_raw):
        x_key = str(xv) if xv is not None else "None"
        y_key = str(yv) if yv is not None else "None"
        if x_key not in x_seen:
            x_seen.add(x_key)
            x_cats.append(x_key)
        if y_key not in y_seen:
            y_seen.add(y_key)
            y_cats.append(y_key)

    x_idx = {c: i for i, c in enumerate(x_cats)}
    y_idx = {c: i for i, c in enumerate(y_cats)}

    # Build the z matrix (y_rows × x_cols)
    z: list[list[float]] = [[0.0] * len(x_cats) for _ in range(len(y_cats))]
    for xv, yv, vv in zip(x_raw, y_raw, v_raw):
        x_key = str(xv) if xv is not None else "None"
        y_key = str(yv) if yv is not None else "None"
        val = float(vv) if vv is not None else 0.0
        z[y_idx[y_key]][x_idx[x_key]] = val

    z_array = np.array(z)

    fig, ax = create_figure(config)
    im = ax.imshow(z_array, cmap="viridis", aspect="auto")

    # Set tick labels
    ax.set_xticks(range(len(x_cats)))
    ax.set_xticklabels(x_cats, rotation=45, ha="right")
    ax.set_yticks(range(len(y_cats)))
    ax.set_yticklabels(y_cats)

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(labelsize=config.font_size - 2)

    # Annotate cells with values
    if len(x_cats) * len(y_cats) <= 100:  # Only annotate if not too many cells
        for i in range(len(y_cats)):
            for j in range(len(x_cats)):
                ax.text(
                    j,
                    i,
                    f"{z[i][j]:.1f}",
                    ha="center",
                    va="center",
                    fontsize=config.font_size - 4,
                    color="white" if z[i][j] < z_array.max() / 2 else "black",
                )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="heatmap",
    label="Heatmap",
    category="statistical",
    icon="🔥",
    field_roles=[
        FieldRole(name="x", type="category", required=True),
        FieldRole(name="y", type="category", required=True),
        FieldRole(name="value", type="numeric", required=True),
    ],
    config_model=HeatmapChartConfig,
    renderer=render_heatmap,
)
