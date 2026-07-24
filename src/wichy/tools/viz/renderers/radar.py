"""Radar / spider chart renderer (matplotlib).

Multivariate radial axes from a center point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import RadarChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_radar(
    data_rows: list[dict[str, Any]],
    config: RadarChartConfig,
    output_path: Path,
) -> None:
    """Render a radar/spider chart and save it to output_path.

    Each row in the data represents one series. The ``categories`` columns
    provide the axis labels, and the ``values`` columns provide the numeric
    values for each axis.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Radar config (categories list, values list, group_by).
        output_path: Destination PNG file path.
    """
    n_axes = max(len(config.categories), len(config.values), 3)

    # Compute angles
    angles = np.linspace(0, 2 * np.pi, n_axes, endpoint=False).tolist()
    angles += angles[:1]  # Close the polygon

    fig = create_figure(config)[0]
    colors = get_colors(config)

    # Use polar projection
    fig.clear()
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)

    # Build category labels, padding if needed
    cat_labels = list(config.categories)
    while len(cat_labels) < n_axes:
        cat_labels.append(f"Axis {len(cat_labels) + 1}")
    ax.set_thetagrids(np.degrees(angles[:-1]), cat_labels)
    ax.set_rlabel_position(0)

    if config.group_by:
        group_vals = extract_column(data_rows, config.group_by)
        groups: dict[str, list[int]] = {}
        for i, g in enumerate(group_vals):
            g_key = str(g) if g is not None else "None"
            groups.setdefault(g_key, []).append(i)

        for gi, (gname, indices) in enumerate(groups.items()):
            for idx in indices:
                row = data_rows[idx]
                val_vals = [float(row.get(v) or 0) for v in config.values]
                # Pad to n_axes if needed
                while len(val_vals) < n_axes:
                    val_vals.append(0.0)
                val_vals = val_vals[:n_axes]
                val_vals += val_vals[:1]  # Close polygon
                color = colors[gi % len(colors)]
                ax.plot(angles, val_vals, "o-", linewidth=2, label=gname, color=color)
                ax.fill(angles, val_vals, alpha=0.25, color=color)
    else:
        for i, row in enumerate(data_rows):
            val_vals = [float(row.get(v) or 0) for v in config.values]
            while len(val_vals) < n_axes:
                val_vals.append(0.0)
            val_vals = val_vals[:n_axes]
            val_vals += val_vals[:1]
            color = colors[i % len(colors)]
            name = str(row.get(config.categories[0], f"Series {i+1}"))
            ax.plot(angles, val_vals, "o-", linewidth=2, label=name, color=color)
            ax.fill(angles, val_vals, alpha=0.25, color=color)

    ax.legend(
        fontsize=config.font_size - 2, loc="center left", bbox_to_anchor=(1.01, 0.5)
    )
    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="radar",
    label="Radar / Spider",
    category="multivariate",
    icon="🕸️",
    field_roles=[
        FieldRole(name="categories", type="category", required=True, multiple=True),
        FieldRole(name="values", type="numeric", required=True, multiple=True),
        FieldRole(name="group_by", type="category", required=False),
    ],
    config_model=RadarChartConfig,
    renderer=render_radar,
)
