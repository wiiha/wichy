"""Parallel coordinates renderer (matplotlib).

Multivariate visualization with one vertical axis per dimension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


from wichy.tools.viz.config_models import ParallelCoordsConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_columns,
    get_colors,
    mpl_to_png,
)


def render_parallel_coords(
    data_rows: list[dict[str, Any]],
    config: ParallelCoordsConfig,
    output_path: Path,
) -> None:
    """Render a parallel coordinates plot and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Parallel coords config (dimensions list, color_by).
        output_path: Destination PNG file path.
    """
    dims_data = extract_columns(data_rows, config.dimensions)
    n_dims = len(config.dimensions)

    fig, ax = create_figure(config)
    colors = get_colors(config)

    # Normalize each dimension to [0, 1] for plotting
    normalized: dict[str, list[float]] = {}
    for col, vals in dims_data.items():
        clean = [float(v) if v is not None else 0.0 for v in vals]
        if clean:
            vmin, vmax = min(clean), max(clean)
            if vmax > vmin:
                normalized[col] = [(v - vmin) / (vmax - vmin) for v in clean]
            else:
                normalized[col] = [0.5] * len(clean)
        else:
            normalized[col] = []

    # Color values
    if config.color_by:
        color_vals = [row.get(config.color_by) for row in data_rows]
        if all(isinstance(c, (int, float)) for c in color_vals if c is not None):
            # Numeric color: use colormap
            import matplotlib.pyplot as plt2

            cmap = plt2.get_cmap("viridis")
            clean_cv = [float(c) if c is not None else 0.0 for c in color_vals]
            vmin, vmax = (min(clean_cv), max(clean_cv)) if clean_cv else (0, 1)
            for i in range(len(data_rows)):
                y_vals = [normalized[col][i] for col in config.dimensions]
                normalized_color = (
                    (clean_cv[i] - vmin) / (vmax - vmin) if vmax > vmin else 0.5
                )
                ax.plot(
                    range(n_dims),
                    y_vals,
                    alpha=0.5,
                    color=cmap(normalized_color),
                )
        else:
            # Categorical color
            unique_cats = list(
                dict.fromkeys(str(c) for c in color_vals if c is not None)
            )
            cat_colors = {
                cat: colors[i % len(colors)] for i, cat in enumerate(unique_cats)
            }
            for i in range(len(data_rows)):
                y_vals = [normalized[col][i] for col in config.dimensions]
                c_key = str(color_vals[i]) if color_vals[i] is not None else "None"
                ax.plot(
                    range(n_dims),
                    y_vals,
                    alpha=0.5,
                    color=cat_colors.get(c_key, colors[0]),
                )
            for cat in unique_cats:
                ax.plot([], [], color=cat_colors[cat], label=cat)
            ax.legend(fontsize=config.font_size - 2)
    else:
        for i in range(len(data_rows)):
            y_vals = [normalized[col][i] for col in config.dimensions]
            ax.plot(range(n_dims), y_vals, alpha=0.5, color=colors[0])

    # Set x ticks to dimension names
    ax.set_xticks(range(n_dims))
    ax.set_xticklabels(config.dimensions, rotation=45, ha="right")
    ax.set_ylim(-0.05, 1.05)
    ax.set_yticks([])

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="parallel_coords",
    label="Parallel Coordinates",
    category="multivariate",
    icon="🔀",
    field_roles=[
        FieldRole(name="dimensions", type="numeric", required=True, multiple=True),
        FieldRole(name="color_by", type="numeric", required=False),
    ],
    config_model=ParallelCoordsConfig,
    renderer=render_parallel_coords,
)
