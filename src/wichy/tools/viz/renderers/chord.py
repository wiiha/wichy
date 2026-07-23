"""Chord diagram renderer (matplotlib).

Relationship/flow between entities on a circle. Uses matplotlib's polar
projection to draw arcs connecting entities, with arc thickness proportional
to the flow value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import ChordChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_chord(
    data_rows: list[dict[str, Any]],
    config: ChordChartConfig,
    output_path: Path,
) -> None:
    """Render a chord diagram and save it to output_path.

    Entities are arranged on a circle. Arcs connect source → target pairs,
    with thickness proportional to the value. Each entity gets a colored arc
    segment on the circle's perimeter.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Chord config (source, target, value).
        output_path: Destination PNG file path.
    """
    sources_raw = extract_column(data_rows, config.source)
    targets_raw = extract_column(data_rows, config.target)
    values_raw = extract_column(data_rows, config.value)

    # Build unique entity labels
    all_labels: list[str] = []
    label_set: set[str] = set()
    for s, t in zip(sources_raw, targets_raw):
        s_key = str(s) if s is not None else "None"
        t_key = str(t) if t is not None else "None"
        for key in (s_key, t_key):
            if key not in label_set:
                label_set.add(key)
                all_labels.append(key)

    n_entities = len(all_labels)
    if n_entities == 0:
        # No data — create empty figure
        fig, ax = create_figure(config)
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        apply_theme(fig, ax, config)
        mpl_to_png(fig, config, output_path)
        return

    label_to_idx = {label: i for i, label in enumerate(all_labels)}

    # Aggregate flows into a matrix
    flow_matrix = np.zeros((n_entities, n_entities))
    for s, t, v in zip(sources_raw, targets_raw, values_raw):
        s_key = str(s) if s is not None else "None"
        t_key = str(t) if t is not None else "None"
        val = float(v) if v is not None else 0.0
        flow_matrix[label_to_idx[s_key], label_to_idx[t_key]] += val

    # Compute total flow per entity for arc segment sizing
    out_flows = flow_matrix.sum(axis=1)
    in_flows = flow_matrix.sum(axis=0)
    total_flows = out_flows + in_flows
    total = total_flows.sum()

    if total == 0:
        # Equal segments
        seg_sizes = np.ones(n_entities) / n_entities
    else:
        seg_sizes = total_flows / total

    # Compute arc positions
    gaps = 0.02  # Gap between segments (in radians)
    total_gap = gaps * n_entities
    available = 2 * np.pi - total_gap
    seg_angles = seg_sizes * available

    # Start angles for each entity
    start_angles = np.zeros(n_entities)
    current = 0.0
    for i in range(n_entities):
        start_angles[i] = current
        current += seg_angles[i] + gaps

    # Midpoint angles for label placement
    mid_angles = start_angles + seg_angles / 2

    fig = create_figure(config)[0]
    colors = get_colors(config)

    # Use polar projection
    fig.clear()
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 1.3)
    ax.set_yticks([])
    ax.set_xticks([])

    # Draw entity arcs on the outer ring
    for i in range(n_entities):
        theta_start = start_angles[i]
        theta_end = start_angles[i] + seg_angles[i]
        theta_mid = mid_angles[i]

        # Draw arc
        theta_range = np.linspace(theta_start, theta_end, 50)
        ax.plot(
            theta_range,
            np.ones(50),
            color=colors[i % len(colors)],
            linewidth=8,
            solid_capstyle="round",
        )

        # Label
        label_radius = 1.15
        ha = "left" if 0 < theta_mid < np.pi else "right"
        ax.text(
            theta_mid,
            label_radius,
            all_labels[i],
            ha=ha,
            va="center",
            fontsize=config.font_size - 2,
        )

    # Draw chord connections
    max_flow = flow_matrix.max() if flow_matrix.max() > 0 else 1.0
    for i in range(n_entities):
        for j in range(n_entities):
            if flow_matrix[i, j] > 0:
                # Draw a curve from entity i to entity j
                theta_i = mid_angles[i]
                theta_j = mid_angles[j]
                # Bezier-like curve through the center
                n_points = 50
                t = np.linspace(0, 1, n_points)
                # Interpolate angle and radius
                theta = theta_i * (1 - t) + theta_j * t
                # Radius dips toward center
                radius = 1.0 - 0.7 * 4 * t * (1 - t)
                alpha = 0.3 + 0.4 * (flow_matrix[i, j] / max_flow)
                ax.plot(
                    theta,
                    radius,
                    color=colors[i % len(colors)],
                    alpha=alpha,
                    linewidth=1 + 3 * (flow_matrix[i, j] / max_flow),
                )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="chord",
    label="Chord Diagram",
    category="flow",
    icon="🔄",
    field_roles=[
        FieldRole(name="source", type="category", required=True),
        FieldRole(name="target", type="category", required=True),
        FieldRole(name="value", type="numeric", required=True),
    ],
    config_model=ChordChartConfig,
    renderer=render_chord,
)
