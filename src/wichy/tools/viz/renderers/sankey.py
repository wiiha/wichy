"""Sankey diagram renderer (matplotlib).

Flow quantities between source → target nodes. Uses matplotlib's built-in
``matplotlib.sankey`` or a manual flow diagram approach.

Since matplotlib's Sankey is designed for energy flow diagrams (not general
source→target flows), this renderer uses a manual approach: nodes are laid
out as horizontal bars and flows are drawn as curved connections.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.patches as mpatches

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

    Uses a simplified two-column layout: sources on the left, targets on the
    right, with curved bands whose thickness is proportional to the flow value.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Sankey config (source, target, value).
        output_path: Destination PNG file path.
    """
    sources_raw = extract_column(data_rows, config.source)
    targets_raw = extract_column(data_rows, config.target)
    values_raw = extract_column(data_rows, config.value)

    # Build unique node labels
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

    # Aggregate flows
    flows: dict[tuple[str, str], float] = {}
    for s, t, v in zip(sources_raw, targets_raw, values_raw):
        s_key = str(s) if s is not None else "None"
        t_key = str(t) if t is not None else "None"
        val = float(v) if v is not None else 0.0
        flows[(s_key, t_key)] = flows.get((s_key, t_key), 0.0) + val

    fig, ax = create_figure(config)
    colors = get_colors(config)
    ax.set_xlim(-0.5, 1.5)
    ax.set_ylim(-1, max(len(source_labels), len(target_labels)))
    ax.axis("off")

    # Draw source nodes (left column at x=0)
    s_y = {label: i for i, label in enumerate(source_labels)}
    t_y = {label: i for i, label in enumerate(target_labels)}

    max_val = max(flows.values()) if flows else 1.0

    for i, (s_label, t_label) in enumerate(flows.keys()):
        val = flows[(s_label, t_label)]
        y1 = s_y[s_label]
        y2 = t_y[t_label]

        # Draw curved connection
        verts = [
            (0, y1),
            (0.5, y1),
            (0.5, y2),
            (1, y2),
        ]
        codes = [
            mpatches.Path.MOVETO,
            mpatches.Path.CURVE4,
            mpatches.Path.CURVE4,
            mpatches.Path.CURVE4,
        ]
        path = mpatches.Path(verts, codes)
        patch = mpatches.PathPatch(
            path,
            facecolor=colors[i % len(colors)],
            alpha=0.3 + 0.4 * (val / max_val) if max_val > 0 else 0.5,
            edgecolor="none",
        )
        ax.add_patch(patch)

    # Draw node labels
    for label, y in s_y.items():
        ax.text(-0.1, y, label, ha="right", va="center", fontsize=config.font_size - 2)
        ax.add_patch(mpatches.Rectangle((-0.05, y - 0.2), 0.05, 0.4, color=colors[0]))
    for label, y in t_y.items():
        ax.text(1.1, y, label, ha="left", va="center", fontsize=config.font_size - 2)
        ax.add_patch(
            mpatches.Rectangle((1.0, y - 0.2), 0.05, 0.4, color=colors[1 % len(colors)])
        )

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
