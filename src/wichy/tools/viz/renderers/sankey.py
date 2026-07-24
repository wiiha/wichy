"""Sankey diagram renderer (matplotlib).

Flow quantities between source → target nodes. Sources are laid out on the
left, targets on the right, with colored bands whose thickness is proportional
to the flow value. Each source has a consistent color; target bars are stacked
by incoming source color.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import SankeyChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_sankey(
    data_rows: list[dict[str, Any]],
    config: SankeyChartConfig,
    output_path: Path,
) -> None:
    """Render a Sankey-style flow diagram and save it to output_path.

    Uses a two-column layout: sources on the left at x=0, targets on the right
    at x=1. Flows are drawn as filled bands whose vertical thickness is
    proportional to the flow value.

    Each source node is assigned a stable color from the theme palette; all
    outgoing flows from that source use that color (with alpha varying by
    relative magnitude). Target nodes are drawn as stacked segments, one per
    incoming source color.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Sankey config (source, target, value).
        output_path: Destination PNG file path.
    """
    sources_raw = extract_column(data_rows, config.source)
    targets_raw = extract_column(data_rows, config.target)
    values_raw = extract_column(data_rows, config.value)

    # Aggregate flows
    flows: dict[tuple[str, str], float] = {}
    for s, t, v in zip(sources_raw, targets_raw, values_raw):
        s_key = str(s) if s is not None else "None"
        t_key = str(t) if t is not None else "None"
        val = float(v) if v is not None else 0.0
        flows[(s_key, t_key)] = flows.get((s_key, t_key), 0.0) + val

    # Build unique labels preserving first-appearance order
    source_labels: list[str] = []
    target_labels: list[str] = []
    s_seen: set[str] = set()
    t_seen: set[str] = set()
    for s, t in zip(sources_raw, targets_raw):
        s_key = str(s) if s is not None else "None"
        t_key = str(t) if t is not None else "None"
        if s_key not in s_seen:
            s_seen.add(s_key)
            source_labels.append(s_key)
        if t_key not in t_seen:
            t_seen.add(t_key)
            target_labels.append(t_key)

    # Compute total flow per node
    source_totals: dict[str, float] = {label: 0.0 for label in source_labels}
    target_totals: dict[str, float] = {label: 0.0 for label in target_labels}
    for (s, t), v in flows.items():
        source_totals[s] += v
        target_totals[t] += v

    # Assign a stable color to each source node
    colors = get_colors(config)
    s_color: dict[str, Any] = {
        label: colors[i % len(colors)] for i, label in enumerate(source_labels)
    }

    # Layout: stack nodes vertically
    gap = max(max(source_totals.values()) if source_totals else 1.0, 1.0) * 0.05

    s_y_pos: dict[str, float] = {}
    cur_y = 0.0
    for label in source_labels:
        s_y_pos[label] = cur_y
        cur_y += source_totals[label] + gap

    t_y_pos: dict[str, float] = {}
    cur_y = 0.0
    for label in target_labels:
        t_y_pos[label] = cur_y
        cur_y += target_totals[label] + gap

    total_height = max(
        sum(source_totals.values()) + gap * max(len(source_labels) - 1, 0),
        sum(target_totals.values()) + gap * max(len(target_labels) - 1, 0),
        1.0,
    )

    fig, ax = create_figure(config)

    max_val = max(flows.values()) if flows else 1.0

    # Track how much of each node has been consumed by drawn flows
    s_used: dict[str, float] = {label: 0.0 for label in source_labels}
    t_used: dict[str, float] = {label: 0.0 for label in target_labels}

    # Draw flow bands. Iterate sources in order, and for each source iterate
    # targets in order. This preserves a consistent stacking and minimizes
    # visual crossings.
    for s_label in source_labels:
        for t_label in target_labels:
            val = flows.get((s_label, t_label))
            if not val or val <= 0:
                continue

            color = s_color[s_label]
            alpha = 0.5 + 0.35 * (val / max_val) if max_val > 0 else 0.6

            # Source side: next available vertical slice (top to bottom)
            s_top = s_y_pos[s_label] + source_totals[s_label] - s_used[s_label]
            s_bot = s_top - val
            s_used[s_label] += val

            # Target side: next available vertical slice
            t_top = t_y_pos[t_label] + target_totals[t_label] - t_used[t_label]
            t_bot = t_top - val
            t_used[t_label] += val

            # Build filled band from (0, s_top/s_bot) to (1, t_top/t_bot)
            n_points = 50
            t_arr = np.linspace(0, 1, n_points)
            ease = 3 * t_arr**2 - 2 * t_arr**3  # smoothstep

            top_y = s_top + (t_top - s_top) * ease
            bot_y = s_bot + (t_bot - s_bot) * ease

            poly_x = np.concatenate([t_arr, t_arr[::-1]])
            poly_y = np.concatenate([top_y, bot_y[::-1]])

            ax.fill(poly_x, poly_y, color=color, alpha=alpha, edgecolor="none")

    # Draw source node bars using source colors
    bar_width = 0.05
    for label in source_labels:
        y_bot = s_y_pos[label]
        y_top = y_bot + source_totals[label]
        ax.fill_between(
            [-bar_width, 0],
            [y_bot, y_bot],
            [y_top, y_top],
            color=s_color[label],
            edgecolor="white",
            linewidth=0.5,
        )
        ax.text(
            -bar_width - 0.02,
            (y_bot + y_top) / 2,
            label,
            ha="right",
            va="center",
            fontsize=config.font_size - 2,
        )

    # Draw target node bars as stacked incoming flows
    t_used_draw: dict[str, float] = {label: 0.0 for label in target_labels}
    for s_label in source_labels:
        for t_label in target_labels:
            val = flows.get((s_label, t_label))
            if not val or val <= 0:
                continue

            y_bot = (
                t_y_pos[t_label] + target_totals[t_label] - t_used_draw[t_label] - val
            )
            y_top = y_bot + val
            t_used_draw[t_label] += val

            ax.fill_between(
                [1, 1 + bar_width],
                [y_bot, y_bot],
                [y_top, y_top],
                color=s_color[s_label],
                edgecolor="white",
                linewidth=0.5,
            )

    for label in target_labels:
        y_bot = t_y_pos[label]
        y_top = y_bot + target_totals[label]
        ax.text(
            1 + bar_width + 0.02,
            (y_bot + y_top) / 2,
            label,
            ha="left",
            va="center",
            fontsize=config.font_size - 2,
        )

    ax.set_xlim(-bar_width - 0.3, 1 + bar_width + 0.3)
    ax.set_ylim(-gap, total_height)
    ax.axis("off")

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="sankey",
    label="Sankey Diagram",
    category="flow",
    icon="🌊",
    field_roles=[
        FieldRole(name="source", type="category", required=True),
        FieldRole(name="target", type="category", required=True),
        FieldRole(name="value", type="numeric", required=True),
    ],
    config_model=SankeyChartConfig,
    renderer=render_sankey,
)
