"""Radar / spider chart renderer (matplotlib).

Multivariate radial axes from a center point. Each data row is one series
(one polygon). ``values`` columns provide the numeric values for each axis,
and ``categories`` provides the human-readable axis labels.

If ``group_by`` is set, rows are grouped and averaged. If ``name_column``
is set, that column provides the series label in the legend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import RadarChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    DEFAULT_COLORS,
    apply_theme,
    create_figure,
    extract_column,
    mpl_to_png,
)


def _safe_float(val: Any) -> float:
    """Convert a value to float, returning 0.0 for None/empty/invalid."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def render_radar(
    data_rows: list[dict[str, Any]],
    config: RadarChartConfig,
    output_path: Path,
) -> None:
    """Render a radar/spider chart and save it to *output_path*.

    Each row in the data represents one series. The ``values`` columns
    provide the numeric values for each axis, and ``categories`` provides
    the axis labels shown around the radar.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Radar config (categories, values, group_by, name_column).
        output_path: Destination PNG file path.
    """
    if not data_rows or not config.values:
        fig, ax = create_figure(config)
        apply_theme(fig, ax, config)
        mpl_to_png(fig, config, output_path)
        return

    # Number of axes = number of value columns
    n_axes = len(config.values)

    # Build axis labels: use categories if provided, fall back to column names
    cat_labels: list[str] = []
    for i in range(n_axes):
        if i < len(config.categories) and config.categories[i]:
            cat_labels.append(str(config.categories[i]))
        else:
            cat_labels.append(str(config.values[i]))

    # Compute angles
    angles = np.linspace(0, 2 * np.pi, n_axes, endpoint=False).tolist()
    angles += angles[:1]  # Close the polygon

    # Set up polar axes (replace default Cartesian from create_figure)
    fig, _default_ax = create_figure(config)
    _default_ax.remove()
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)

    # Adjust axes position to leave room for title/subtitle at top
    # and legend on the right.  The polar axes is square (circular),
    # so we shrink it to fit within the available space.
    # Margins are in figure-fraction units. Polar tick labels extend
    # beyond the axes bounding box, so we need generous margins.
    top_margin = 0.12
    if config.title:
        top_margin += 0.06
    if config.subtitle:
        top_margin += 0.05

    right_margin = 0.06
    # Legend is shown when there are multiple series
    has_legend = bool(config.group_by) or len(data_rows) > 1
    if has_legend:
        right_margin = 0.22

    bottom_margin = 0.10
    left_margin = 0.10

    available_w = 1.0 - left_margin - right_margin
    available_h = 1.0 - top_margin - bottom_margin
    # Polar axes should be square — use the smaller dimension
    side = min(available_w, available_h)
    # Center it in the available space
    ax_x = left_margin + (available_w - side) / 2
    ax_y = bottom_margin + (available_h - side) / 2
    ax.set_position([ax_x, ax_y, side, side])

    # Axis labels
    ax.set_thetagrids(np.degrees(angles[:-1]), cat_labels)
    ax.set_rlabel_position(180.0 / n_axes if n_axes > 0 else 0)

    colors = config.color_palette if config.color_palette else DEFAULT_COLORS

    if config.group_by:
        # --- Group-by mode: average values within each group ---
        group_vals = extract_column(data_rows, config.group_by)
        groups: dict[str, list[int]] = {}
        for i, g in enumerate(group_vals):
            g_key = str(g) if g is not None else "None"
            groups.setdefault(g_key, []).append(i)

        for gi, (gname, indices) in enumerate(groups.items()):
            # Average each value column across rows in this group
            val_vals: list[float] = []
            for vcol in config.values:
                col_vals = [_safe_float(data_rows[idx].get(vcol)) for idx in indices]
                val_vals.append(sum(col_vals) / len(col_vals) if col_vals else 0.0)
            val_vals += val_vals[:1]  # Close polygon
            color = colors[gi % len(colors)]
            ax.plot(angles, val_vals, "o-", linewidth=2, label=gname, color=color)
            ax.fill(angles, val_vals, alpha=0.25, color=color)
    else:
        # --- Per-row mode: each row is a separate series ---
        for i, row in enumerate(data_rows):
            val_vals = [_safe_float(row.get(vcol)) for vcol in config.values]
            val_vals += val_vals[:1]  # Close polygon

            # Series label: use name_column if set, otherwise "Series N"
            if config.name_column:
                name_val = row.get(config.name_column)
                name = str(name_val) if name_val is not None else f"Series {i + 1}"
            else:
                name = f"Series {i + 1}"

            color = colors[i % len(colors)]
            ax.plot(angles, val_vals, "o-", linewidth=2, label=name, color=color)
            ax.fill(angles, val_vals, alpha=0.25, color=color)

    # Legend — use bbox_to_anchor in axes-fraction but place it further
    # right to clear the polar tick labels that extend past the axes box.
    if has_legend:
        ax.legend(
            fontsize=config.font_size - 2,
            loc="center left",
            bbox_to_anchor=(1.25, 0.5),
            frameon=False,
        )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="radar",
    label="Radar / Spider",
    category="multivariate",
    icon="\U0001f578\ufe0f",
    field_roles=[
        FieldRole(name="categories", type="category", required=True, multiple=True),
        FieldRole(name="values", type="numeric", required=True, multiple=True),
        FieldRole(name="group_by", type="category", required=False),
        FieldRole(name="name_column", type="category", required=False),
    ],
    config_model=RadarChartConfig,
    renderer=render_radar,
)
