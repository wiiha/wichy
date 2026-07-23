"""Sunburst chart renderer (matplotlib).

Hierarchical radial segments. Uses polar projection to draw concentric
rings representing the hierarchy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import SunburstChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_sunburst(
    data_rows: list[dict[str, Any]],
    config: SunburstChartConfig,
    output_path: Path,
) -> None:
    """Render a sunburst chart and save it to output_path.

    For flat data (no parent), draws a single ring. For hierarchical data
    (with parent), draws concentric rings.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Sunburst config (labels, values, parent).
        output_path: Destination PNG file path.
    """
    labels = [
        str(lbl) if lbl is not None else ""
        for lbl in extract_column(data_rows, config.labels)
    ]
    raw_values = extract_column(data_rows, config.values)
    values = [float(v) if v is not None else 0.0 for v in raw_values]

    parents: list[str]
    if config.parent:
        parents = [
            str(p) if p is not None else ""
            for p in extract_column(data_rows, config.parent)
        ]
    else:
        parents = [""] * len(labels)

    fig, ax = create_figure(config)
    colors = get_colors(config)
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.axis("off")

    # Build hierarchy: root nodes (parent="" or parent not in labels) → children
    label_set = set(labels)
    root_indices = [i for i, p in enumerate(parents) if p == "" or p not in label_set]
    root_total = sum(values[i] for i in root_indices) or 1.0

    # Draw root ring (innermost)
    theta_start = 0.0
    for idx in root_indices:
        frac = values[idx] / root_total if root_total > 0 else 0
        theta_width = frac * 2 * np.pi
        color = colors[idx % len(colors)]
        ax.bar(
            theta_start + theta_width / 2,
            1.0,
            width=theta_width,
            bottom=0,
            color=color,
            edgecolor="white",
            linewidth=1,
        )
        # Label
        if theta_width > 0.15:
            ax.text(
                theta_start + theta_width / 2,
                0.5,
                labels[idx],
                ha="center",
                va="center",
                fontsize=config.font_size - 4,
                color="white",
                fontweight="bold",
            )

        # Draw children (second ring)
        children = [i for i, p in enumerate(parents) if p == labels[idx]]
        if children:
            child_total = sum(values[i] for i in children) or 1.0
            child_theta = theta_start
            for ci in children:
                child_frac = values[ci] / child_total if child_total > 0 else 0
                child_width = child_frac * theta_width
                child_color = colors[(idx + ci + 1) % len(colors)]
                ax.bar(
                    child_theta + child_width / 2,
                    0.6,
                    width=child_width,
                    bottom=1.0,
                    color=child_color,
                    edgecolor="white",
                    linewidth=0.5,
                )
                if child_width > 0.1:
                    ax.text(
                        child_theta + child_width / 2,
                        1.3,
                        labels[ci],
                        ha="center",
                        va="center",
                        fontsize=config.font_size - 5,
                        color="white",
                    )
                child_theta += child_width

        theta_start += theta_width

    ax.set_ylim(0, 1.8)
    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="sunburst",
    label="Sunburst",
    category="hierarchical",
    icon="☀️",
    field_roles=[
        FieldRole(name="labels", type="category", required=True),
        FieldRole(name="values", type="numeric", required=True),
        FieldRole(name="parent", type="category", required=False),
    ],
    config_model=SunburstChartConfig,
    renderer=render_sunburst,
)
