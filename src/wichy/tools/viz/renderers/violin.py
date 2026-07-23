"""Violin plot renderer (matplotlib).

Distribution density + optional box overlay per category.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wichy.tools.viz.config_models import ViolinChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_violin(
    data_rows: list[dict[str, Any]],
    config: ViolinChartConfig,
    output_path: Path,
) -> None:
    """Render a violin plot and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Violin config (value, group_by, box_overlay).
        output_path: Destination PNG file path.
    """
    values = extract_column(data_rows, config.value)
    clean_vals = [v for v in values if v is not None]

    fig, ax = create_figure(config)
    colors = get_colors(config)

    if config.group_by:
        group_vals = extract_column(data_rows, config.group_by)
        groups: dict[str, list[Any]] = {}
        for v, g in zip(values, group_vals):
            g_key = str(g) if g is not None else "None"
            groups.setdefault(g_key, []).append(v)

        violin_data = [[v for v in gvals if v is not None] for gvals in groups.values()]
        violin_labels = list(groups.keys())

        parts = ax.violinplot(violin_data, showmeans=True, showmedians=True)
        for i, pc in enumerate(parts["bodies"]):
            pc.set_facecolor(colors[i % len(colors)])
            pc.set_alpha(0.6)
        ax.set_xticks(range(1, len(violin_labels) + 1))
        ax.set_xticklabels(violin_labels, rotation=45, ha="right")

        if config.box_overlay:
            bp = ax.boxplot(violin_data, widths=0.1, patch_artist=True)
            for i, patch in enumerate(bp["boxes"]):
                patch.set_facecolor(colors[i % len(colors)])
                patch.set_alpha(0.3)
    else:
        parts = ax.violinplot([clean_vals], showmeans=True, showmedians=True)
        parts["bodies"][0].set_facecolor(colors[0])
        parts["bodies"][0].set_alpha(0.6)
        ax.set_xticks([1])
        ax.set_xticklabels([config.value])

        if config.box_overlay:
            bp = ax.boxplot([clean_vals], widths=0.1, patch_artist=True)
            bp["boxes"][0].set_facecolor(colors[0])
            bp["boxes"][0].set_alpha(0.3)

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="violin",
    label="Violin Plot",
    category="statistical",
    icon="🎻",
    field_roles=[
        FieldRole(name="value", type="numeric", required=True),
        FieldRole(name="group_by", type="category", required=False),
    ],
    config_model=ViolinChartConfig,
    renderer=render_violin,
)
