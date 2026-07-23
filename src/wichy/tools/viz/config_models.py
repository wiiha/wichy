"""Pydantic config models for chart configurations.

All chart configs share a ``BaseChartConfig`` with common styling fields, then
each chart type has its own config model with chart-specific field mappings.
All models use ``extra="ignore"`` to tolerate unknown fields (INV-003, risk
mitigation: "Config model validation too strict").
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class BaseChartConfig(BaseModel):
    """Common styling configuration shared by all chart types."""

    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = None
    """Chart heading displayed at top of the chart."""

    subtitle: Optional[str] = None
    """Smaller text below the title."""

    x_axis_label: Optional[str] = None
    """Label for the x-axis."""

    y_axis_label: Optional[str] = None
    """Label for the y-axis."""

    width: int = 1200
    """Chart width in pixels."""

    height: int = 800
    """Chart height in pixels."""

    dpi: int = 150
    """Dots per inch for PNG export."""

    theme: str = "light"
    """Color theme: ``"light"`` or ``"dark"`` (INV-012)."""

    color_palette: list[str] = Field(default_factory=list)
    """Custom color palette (hex codes). Empty list uses library defaults."""

    font_size: int = 14
    """Base font size for chart text."""

    background: str = "white"
    """Background color: ``"white"`` or ``"transparent"``."""


# ---------------------------------------------------------------------------
# Chart-specific config models
# ---------------------------------------------------------------------------


class BarChartConfig(BaseChartConfig):
    """Config for bar charts."""

    x: str = Field(..., description="Column name for x-axis (category)")
    y: str = Field(..., description="Column name for y-axis (numeric)")
    color_by: Optional[str] = None
    orientation: str = "v"  # "v" or "h"
    mode: str = "grouped"  # "grouped" or "stacked"


class DistributionChartConfig(BaseChartConfig):
    """Config for distribution charts (histogram, KDE, box)."""

    value: str = Field(..., description="Column name for the numeric value")
    subtype: str = "histogram"  # "histogram", "kde", "box"
    bins: Optional[int] = None
    group_by: Optional[str] = None


class LineChartConfig(BaseChartConfig):
    """Config for line graphs."""

    x: str = Field(..., description="Column name for x-axis (numeric/date)")
    y: list[str] = Field(..., description="Column name(s) for y-axis (numeric)")
    color_by: Optional[str] = None


class ScatterChartConfig(BaseChartConfig):
    """Config for scatter plots."""

    x: str = Field(..., description="Column name for x-axis (numeric)")
    y: str = Field(..., description="Column name for y-axis (numeric)")
    color_by: Optional[str] = None
    size_by: Optional[str] = None


class ChordChartConfig(BaseChartConfig):
    """Config for chord diagrams."""

    source: str = Field(..., description="Column name for source entity")
    target: str = Field(..., description="Column name for target entity")
    value: str = Field(..., description="Column name for flow value (numeric)")


class ParallelCoordsConfig(BaseChartConfig):
    """Config for parallel coordinates plots.

    Dimensions can be numeric or categorical. Categorical values are
    sorted alphabetically and mapped to evenly spaced positions.
    """

    dimensions: list[str] = Field(
        ..., description="Column name(s) for dimensions (numeric or categorical)"
    )
    color_by: Optional[str] = None


class TimeCompassConfig(BaseChartConfig):
    """Config for time compass charts."""

    time: str = Field(..., description="Column name for time/date axis")
    value: str = Field(..., description="Column name for numeric value")
    group_by: Optional[str] = None


class SankeyChartConfig(BaseChartConfig):
    """Config for Sankey diagrams."""

    source: str = Field(..., description="Column name for source node")
    target: str = Field(..., description="Column name for target node")
    value: str = Field(..., description="Column name for flow value (numeric)")


class TreemapChartConfig(BaseChartConfig):
    """Config for treemap charts."""

    labels: str = Field(..., description="Column name for category labels")
    values: str = Field(..., description="Column name for numeric values")
    parent: Optional[str] = None


class SunburstChartConfig(BaseChartConfig):
    """Config for sunburst charts."""

    labels: str = Field(..., description="Column name for category labels")
    values: str = Field(..., description="Column name for numeric values")
    parent: Optional[str] = None


class RadarChartConfig(BaseChartConfig):
    """Config for radar/spider charts."""

    categories: list[str] = Field(..., description="Column name(s) for category axes")
    values: list[str] = Field(..., description="Column name(s) for numeric values")
    group_by: Optional[str] = None


class ViolinChartConfig(BaseChartConfig):
    """Config for violin plots."""

    value: str = Field(..., description="Column name for numeric value")
    group_by: Optional[str] = None
    box_overlay: bool = False


class HeatmapChartConfig(BaseChartConfig):
    """Config for heatmap charts."""

    x: str = Field(..., description="Column name for x-axis (category)")
    y: str = Field(..., description="Column name for y-axis (category)")
    value: str = Field(..., description="Column name for cell value (numeric)")


class CorrelogramChartConfig(BaseChartConfig):
    """Config for correlogram charts."""

    columns: list[str] = Field(
        ..., description="Column name(s) to include in correlation matrix"
    )


# Map chart type id → config model class for easy lookup
CHART_CONFIG_MODELS: dict[str, type[BaseChartConfig]] = {
    "bar": BarChartConfig,
    "distribution": DistributionChartConfig,
    "line": LineChartConfig,
    "scatter": ScatterChartConfig,
    "chord": ChordChartConfig,
    "parallel_coords": ParallelCoordsConfig,
    "time_compass": TimeCompassConfig,
    "sankey": SankeyChartConfig,
    "treemap": TreemapChartConfig,
    "sunburst": SunburstChartConfig,
    "radar": RadarChartConfig,
    "violin": ViolinChartConfig,
    "heatmap": HeatmapChartConfig,
    "correlogram": CorrelogramChartConfig,
}


def get_config_model(chart_type: str) -> Optional[type[BaseChartConfig]]:
    """Look up the Pydantic config model for a chart type."""
    return CHART_CONFIG_MODELS.get(chart_type)


def validate_config(
    chart_type: str, config_dict: dict[str, Any]
) -> tuple[Optional[BaseChartConfig], Optional[str]]:
    """Validate a config dict against the chart type's Pydantic model.

    Returns ``(config_instance, None)`` on success, ``(None, error_msg)`` on
    failure.
    """
    model_cls = get_config_model(chart_type)
    if model_cls is None:
        return None, f"Unknown chart type: {chart_type}"
    try:
        instance = model_cls(**config_dict)
        return instance, None
    except Exception as exc:
        return None, str(exc)
