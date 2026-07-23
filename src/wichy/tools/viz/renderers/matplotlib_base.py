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
    # Title and subtitle
    if config.title:
        fig.suptitle(config.title, fontsize=config.font_size + 4, fontweight="bold")
    if config.subtitle:
        fig.text(
            0.5,
            0.95,
            config.subtitle,
            ha="center",
            va="top",
            fontsize=config.font_size - 2,
            color="gray",
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
        fig.suptitle(color="white") if config.title else None
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
