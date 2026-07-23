"""Correlogram renderer (matplotlib).

Correlation matrix as a colored heatmap with correlation coefficients.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import CorrelogramChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_columns,
    mpl_to_png,
)


def render_correlogram(
    data_rows: list[dict[str, Any]],
    config: CorrelogramChartConfig,
    output_path: Path,
) -> None:
    """Render a correlogram and save it to output_path.

    Computes the Pearson correlation matrix between the specified numeric
    columns and renders it as a heatmap with coefficient annotations.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Correlogram config (columns list).
        output_path: Destination PNG file path.
    """
    cols_data = extract_columns(data_rows, config.columns)

    # Build a numeric matrix (rows × columns)
    n_rows = len(data_rows)
    n_cols = len(config.columns)
    matrix = np.full((n_rows, n_cols), np.nan)

    for j, col in enumerate(config.columns):
        vals = cols_data[col]
        for i, v in enumerate(vals):
            try:
                matrix[i, j] = float(v) if v is not None else np.nan
            except (TypeError, ValueError):
                pass

    # Compute correlation matrix
    if n_rows < 2 or n_cols < 2:
        corr = np.eye(n_cols)
    else:
        try:
            corr = np.corrcoef(matrix, rowvar=False)
            corr = np.nan_to_num(corr, nan=0.0)
        except (ValueError, np.linalg.LinAlgError):
            corr = np.eye(n_cols)

    # Ensure corr is 2D
    if corr.ndim == 0:
        corr = np.array([[float(corr)]])
    elif corr.ndim == 1:
        corr = np.array([[float(c)] for c in corr])

    fig, ax = create_figure(config)

    # Use diverging colormap centered at 0
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")

    # Set tick labels
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(config.columns, rotation=45, ha="right")
    ax.set_yticks(range(n_cols))
    ax.set_yticklabels(config.columns)

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.ax.tick_params(labelsize=config.font_size - 2)

    # Annotate cells with correlation coefficients
    for i in range(n_cols):
        for j in range(n_cols):
            val = corr[i, j] if corr.ndim == 2 else corr[i][j]
            ax.text(
                j,
                i,
                f"{val:.2f}",
                ha="center",
                va="center",
                fontsize=config.font_size - 3,
                color="white" if abs(val) > 0.5 else "black",
            )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="correlogram",
    label="Correlogram",
    category="statistical",
    icon="🔗",
    field_roles=[
        FieldRole(name="columns", type="numeric", required=True, multiple=True),
    ],
    config_model=CorrelogramChartConfig,
    renderer=render_correlogram,
)
