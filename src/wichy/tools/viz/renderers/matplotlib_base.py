"""Shared helper for matplotlib-based chart renderers.

Provides ``mpl_to_png()`` which exports a matplotlib figure to a PNG file
using the Agg backend (fully self-contained, no browser/Chrome dependency).

Also provides ``apply_theme()`` for consistent styling across all chart
types, and column extraction helpers for working with row-dict data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend, must be set before pyplot import
import matplotlib.pyplot as plt  # noqa: E402

from wichy.tools.viz.config_models import BaseChartConfig  # noqa: E402

# Default color palette (matplotlib tab10 colors as hex)
DEFAULT_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]


def get_colors(config: BaseChartConfig) -> list[str]:
    """Return the color palette to use (custom or default)."""
    if config.color_palette:
        return config.color_palette
    return DEFAULT_COLORS


def _style_legend_dark(leg: Any) -> None:
    """Make a matplotlib legend readable on a dark background.

    Matplotlib's legend text defaults to black (via rcParams), which is
    invisible on the dark axes background. Recolor the legend's text,
    title, and frame edge to a light color.
    """
    # Legend labels (the series names).
    for text in leg.get_texts():
        text.set_color("#e0e0e0")
    # Legend title (set via legend(title=...) or leg.set_title()).
    title = leg.get_title()
    if title is not None:
        title.set_color("#e0e0e0")
    # Frame edge — subtle so it doesn't overpower on dark.
    leg.get_frame().set_edgecolor("#444444")


def apply_theme(
    fig: plt.Figure, ax: plt.Axes, config: BaseChartConfig
) -> tuple[plt.Figure, plt.Axes]:
    """Apply theme styling to a matplotlib figure and axes.

    Args:
        fig: Matplotlib figure.
        ax: Matplotlib axes (or the primary axes).
        config: Chart config with theme, title, axis labels, etc.

    Returns:
        ``(fig, ax)`` — the styled figure and axes (modified in-place).
    """
    # Title and subtitle — position with adequate vertical spacing
    # to prevent overlap.  We compute y-positions in figure coordinates
    # based on font sizes so the gap scales with text size.
    fig_h_in = config.height / config.dpi
    fig_h_pt = fig_h_in * 72.0  # total figure height in points

    if config.title and config.subtitle:
        # Title near the top, subtitle below it with a clear gap
        title_fs = config.font_size + 4
        subtitle_fs = config.font_size - 2
        # Convert font sizes to figure-fraction for y positioning
        title_y = 1.0 - (title_fs * 1.5) / fig_h_pt
        subtitle_y = title_y - (title_fs * 1.2 + subtitle_fs * 0.8) / fig_h_pt
        fig.suptitle(
            config.title,
            fontsize=title_fs,
            fontweight="bold",
            y=title_y,
        )
        subtitle_color = "#aaaaaa" if config.theme != "dark" else "#888888"
        fig.text(
            0.5,
            subtitle_y,
            config.subtitle,
            ha="center",
            va="top",
            fontsize=subtitle_fs,
            color=subtitle_color,
        )
    elif config.title:
        title_fs = config.font_size + 4
        title_y = 1.0 - (title_fs * 1.0) / fig_h_pt
        fig.suptitle(
            config.title,
            fontsize=title_fs,
            fontweight="bold",
            y=title_y,
        )
    elif config.subtitle:
        # Subtitle only (no title) — place near top
        subtitle_fs = config.font_size - 2
        subtitle_y = 1.0 - (subtitle_fs * 1.5) / fig_h_pt
        subtitle_color = "#aaaaaa" if config.theme != "dark" else "#888888"
        fig.text(
            0.5,
            subtitle_y,
            config.subtitle,
            ha="center",
            va="top",
            fontsize=subtitle_fs,
            color=subtitle_color,
        )

    # Axis labels
    if config.x_axis_label:
        ax.set_xlabel(config.x_axis_label, fontsize=config.font_size)
    if config.y_axis_label:
        ax.set_ylabel(config.y_axis_label, fontsize=config.font_size)

    # Tick label size
    ax.tick_params(axis="both", labelsize=config.font_size - 2)

    # Theme
    if config.theme == "dark":
        fig.patch.set_facecolor("#0f1117")
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_color("white")
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        # Update existing suptitle color for dark theme
        if config.title and fig._suptitle is not None:
            fig._suptitle.set_color("white")
        # Legend text: matplotlib defaults to black, which is invisible on a
        # dark background. Style the axes' legend (if one exists yet) so the
        # labels, title, and frame edge all read white. Renderers create the
        # legend before the final apply_theme call, so it is present here.
        leg = ax.get_legend()
        if leg is not None:
            _style_legend_dark(leg)
    else:
        bg = "white" if config.background != "transparent" else "none"
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)

    # Figure size from pixel dimensions
    fig.set_size_inches(config.width / config.dpi, config.height / config.dpi)

    return fig, ax


def mpl_to_png(fig: plt.Figure, config: BaseChartConfig, output_path: Path) -> None:
    """Export a matplotlib figure to a PNG file.

    Args:
        fig: Matplotlib figure to export.
        config: Chart config with dpi setting.
        output_path: Destination PNG file path.
    """
    fig.savefig(
        str(output_path),
        dpi=config.dpi,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
    )
    plt.close(fig)


def create_figure(config: BaseChartConfig) -> tuple[plt.Figure, plt.Axes]:
    """Create a new figure and axes with the right size for the config.

    Args:
        config: Chart config with width, height, dpi.

    Returns:
        ``(fig, ax)`` — a new matplotlib figure and axes.
    """
    figsize = (config.width / config.dpi, config.height / config.dpi)
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax


def extract_column(rows: list[dict[str, Any]], column: str) -> list[Any]:
    """Extract a single column from a list of row dicts.

    Args:
        rows: List of row dicts.
        column: Column name to extract.

    Returns:
        List of values for that column.
    """
    return [row.get(column) for row in rows]


def extract_columns(
    rows: list[dict[str, Any]], columns: list[str]
) -> dict[str, list[Any]]:
    """Extract multiple columns from a list of row dicts.

    Args:
        rows: List of row dicts.
        columns: Column names to extract.

    Returns:
        Dict mapping column name → list of values.
    """
    return {col: [row.get(col) for row in rows] for col in columns}
