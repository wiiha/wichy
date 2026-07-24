"""Chart type discovery tool for agents.

Provides a lightweight way for the agent to learn the required and optional
config fields for any chart type before calling ``generate_chart``. This
follows the "forgiving and guiding tool usage" principle — the agent should
never have to guess config structure.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class ChartInfoParameters(ParametersModel):
    """Parameters for the chart_info tool."""

    chart_type: Optional[str] = Field(
        None,
        description=(
            "Chart type to get detailed config info for (e.g. 'bar', 'scatter'). "
            "If omitted, returns a compact summary of all available chart types. "
            "Available types: bar, distribution, line, scatter, chord, "
            "parallel_coords, time_compass, sankey, treemap, sunburst, "
            "radar, violin, heatmap, correlogram."
        ),
    )

    def info(self) -> str:
        return f'chart_type="{self.chart_type or "all"}"'


class ChartInfoTool(BaseTool):
    """Discover chart type config requirements.

    Call this tool before ``generate_chart`` to learn what config fields a
    chart type requires and what optional fields it accepts. Returns a
    human-readable summary that includes field names, expected column types,
    and an example config.
    """

    name = "chart_info"
    description = (
        "Get config field requirements for chart types. "
        "Call before generate_chart to learn required/optional fields."
    )
    description_long = (
        "Discover the config structure needed for generate_chart.\n"
        "- Without chart_type: lists all 14 chart types with their required and optional fields.\n"
        "- With chart_type: returns full details including field types, example config, and styling options.\n"
        "Always call this before generate_chart if you're unsure how to configure a chart."
    )
    parameters_model = ChartInfoParameters
    needs_verification_in_api: bool = False

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Return chart type info.

        Args:
            chart_type: Optional chart type id. If provided, returns full
                details for that type. If None, returns a summary of all types.

        Returns:
            Human-readable chart type info string, or an error message.
        """
        chart_type: Optional[str] = kwargs.get("chart_type")

        try:
            from wichy.tools.viz.info import (
                format_chart_info,
                format_chart_summary,
                list_chart_type_ids,
            )

            if chart_type is None:
                return format_chart_summary()

            info = format_chart_info(chart_type)
            if info is None:
                available = ", ".join(list_chart_type_ids())
                return format_error(
                    f"Unknown chart type: {chart_type}. "
                    f"Available types: {available}"
                )
            return info

        except Exception as e:
            return format_error(f"chart_info failed: {e}")
