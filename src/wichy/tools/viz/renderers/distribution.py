"""Distribution chart renderer (matplotlib).

Supports histogram, KDE density, and box plot subtypes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import DistributionChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def render_distribution(
    data_rows: list[dict[str, Any]],
    config: DistributionChartConfig,
    output_path: Path,
) -> None:
    """Render a distribution chart and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Distribution chart config (value, subtype, bins, group_by).
        output_path: Destination PNG file path.
    """
    values = extract_column(data_rows, config.value)
    clean_vals = [v for v in values if v is not None]
    fig, ax = create_figure(config)
    colors = get_colors(config)

    if config.subtype == "histogram":
        if config.group_by:
            group_vals = extract_column(data_rows, config.group_by)
            groups: dict[str, list[Any]] = {}
            for v, g in zip(values, group_vals):
                g_key = str(g) if g is not None else "None"
                groups.setdefault(g_key, []).append(v)
            for i, (gname, gvals) in enumerate(groups.items()):
                clean = [v for v in gvals if v is not None]
                ax.hist(
                    clean,
                    bins=config.bins or 30,
                    alpha=0.6,
                    label=gname,
                    color=colors[i % len(colors)],
                )
            ax.legend(fontsize=config.font_size - 2)
        else:
            ax.hist(clean_vals, bins=config.bins or 30, color=colors[0])

    elif config.subtype == "kde":
        # KDE via histogram with density + kernel density estimate
        if len(clean_vals) > 1:
            ax.hist(
                clean_vals,
                bins=config.bins or 30,
                density=True,
                alpha=0.5,
                color=colors[0],
                label="Distribution",
            )
            try:
                from scipy.stats import gaussian_kde  # type: ignore[import-not-found]

                kde = gaussian_kde(clean_vals)
                x_range = np.linspace(min(clean_vals), max(clean_vals), 200)
                ax.plot(
                    x_range,
                    kde(x_range),
                    color=colors[1 % len(colors)],
                    linewidth=2,
                    label="KDE",
                )
                ax.legend(fontsize=config.font_size - 2)
            except ImportError:
                pass  # Fallback to just histogram
        else:
            ax.hist(clean_vals, bins=config.bins or 30, color=colors[0])

    elif config.subtype == "box":
        if config.group_by:
            group_vals = extract_column(data_rows, config.group_by)
            groups2: dict[str, list[Any]] = {}
            for v, g in zip(values, group_vals):
                g_key = str(g) if g is not None else "None"
                groups2.setdefault(g_key, []).append(v)
            box_data = [
                [v for v in gvals if v is not None] for gvals in groups2.values()
            ]
            box_labels = list(groups2.keys())
            bp = ax.boxplot(box_data, tick_labels=box_labels, patch_artist=True)
            for i, patch in enumerate(bp["boxes"]):
                patch.set_facecolor(colors[i % len(colors)])
                patch.set_alpha(0.6)
        else:
            bp = ax.boxplot(clean_vals, labels=[config.value], patch_artist=True)
            bp["boxes"][0].set_facecolor(colors[0])
            bp["boxes"][0].set_alpha(0.6)

    else:
        ax.hist(clean_vals, bins=config.bins or 30, color=colors[0])

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="distribution",
    label="Distribution",
    category="statistical",
    icon="📈",
    field_roles=[
        FieldRole(name="value", type="numeric", required=True),
        FieldRole(name="group_by", type="category", required=False),
    ],
    config_model=DistributionChartConfig,
    renderer=render_distribution,
)
