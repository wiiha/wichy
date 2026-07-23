"""Parallel coordinates renderer (matplotlib).

Multivariate visualization with one vertical axis per dimension.
Handles both numeric and categorical dimensions. Categorical values
are sorted alphabetically and mapped to evenly spaced positions.
Each axis displays tick labels showing the actual data values.
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


def _normalize_dimension(
    col: str, vals: list[Any]
) -> tuple[list[float], list[Any], bool]:
    """Normalize a single dimension to [0, 1] range.

    For numeric values, maps min→0, max→1.
    For categorical values, sorts alphabetically and maps to evenly
    spaced positions.

    Returns:
        (normalized_values, tick_values, is_categorical)
    """
    clean = [v for v in vals if v is not None]

    # Detect if all non-None values are numeric
    is_numeric = all(isinstance(v, (int, float)) for v in clean) if clean else True

    if is_numeric and clean:
        numeric_vals = [float(v) for v in vals if v is not None]
        if not numeric_vals:
            return [], [], False
        vmin, vmax = min(numeric_vals), max(numeric_vals)
        normalized = []
        for v in vals:
            if v is None:
                normalized.append(0.0)
            elif vmax > vmin:
                normalized.append((float(v) - vmin) / (vmax - vmin))
            else:
                normalized.append(0.5)
        # Tick values: show min, mid, max
        if vmax > vmin:
            mid = (vmin + vmax) / 2
            tick_values = [vmin, mid, vmax]
        else:
            tick_values = [vmin]
        return normalized, tick_values, False

    # Categorical: sort alphabetically, map to evenly spaced positions
    unique_sorted = sorted(set(str(v) for v in clean))
    if not unique_sorted:
        return [], [], True

    cat_to_pos: dict[str, float] = {}
    n_cats = len(unique_sorted)
    if n_cats == 1:
        cat_to_pos[unique_sorted[0]] = 0.5
    else:
        for i, cat in enumerate(unique_sorted):
            cat_to_pos[cat] = i / (n_cats - 1)

    normalized = []
    for v in vals:
        if v is None:
            normalized.append(0.0)
        else:
            normalized.append(cat_to_pos.get(str(v), 0.0))

    return normalized, unique_sorted, True


def render_parallel_coords(
    data_rows: list[dict[str, Any]],
    config: ParallelCoordsConfig,
    output_path: Path,
) -> None:
    """Render a parallel coordinates plot and save it to output_path.

    Each dimension gets a vertical axis. Numeric dimensions are scaled
    min→max. Categorical dimensions are sorted alphabetically and mapped
    to evenly spaced positions. Tick labels show actual values.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Parallel coords config (dimensions list, color_by).
        output_path: Destination PNG file path.
    """
    dims_data = extract_columns(data_rows, config.dimensions)
    n_dims = len(config.dimensions)

    fig, ax = create_figure(config)
    colors = get_colors(config)

    # Normalize each dimension, tracking tick info
    normalized: dict[str, list[float]] = {}
    tick_info: dict[str, tuple[list[Any], bool]] = {}  # col → (tick_values, is_cat)

    for col in config.dimensions:
        vals = dims_data.get(col, [])
        norm_vals, tick_values, is_cat = _normalize_dimension(col, vals)
        normalized[col] = norm_vals
        tick_info[col] = (tick_values, is_cat)

    # Draw color-by grouping
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

    # Draw y-axis tick labels for each dimension showing actual values.
    # We use a secondary set of y-axes (one per dimension) to show tick
    # labels on the left side of each axis line.
    for dim_idx, col in enumerate(config.dimensions):
        tick_values, is_cat = tick_info.get(col, ([], False))
        if not tick_values:
            continue

        # Draw a vertical line for this axis
        ax.axvline(x=dim_idx, color="gray", linewidth=0.5, alpha=0.5)

        # Create tick positions in [0, 1] space
        if is_cat:
            # Categorical: evenly spaced
            n_cats = len(tick_values)
            if n_cats == 1:
                tick_positions = [0.5]
            else:
                tick_positions = [i / (n_cats - 1) for i in range(n_cats)]
            tick_labels = [str(v) for v in tick_values]
        else:
            # Numeric: map tick_values back to [0, 1]
            if len(tick_values) == 1:
                tick_positions = [0.5]
            else:
                vmin = float(tick_values[0])
                vmax = float(tick_values[-1])
                if vmax > vmin:
                    tick_positions = [
                        (float(v) - vmin) / (vmax - vmin) for v in tick_values
                    ]
                else:
                    tick_positions = [0.5]
            # Format numeric labels
            tick_labels = []
            for v in tick_values:
                if v == int(v):
                    tick_labels.append(str(int(v)))
                else:
                    tick_labels.append(f"{v:.1f}")

        # Add tick labels to the left of each axis
        tick_color = "#888888" if config.theme != "dark" else "#aaaaaa"
        for pos, label in zip(tick_positions, tick_labels):
            # Truncate long categorical labels
            display_label = label[:12] + "…" if len(label) > 13 else label
            ax.text(
                dim_idx - 0.08,
                pos,
                display_label,
                ha="right",
                va="center",
                fontsize=config.font_size - 3,
                color=tick_color,
                alpha=0.7,
            )

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
        FieldRole(name="color_by", type="category", required=False),
    ],
    config_model=ParallelCoordsConfig,
    renderer=render_parallel_coords,
)
