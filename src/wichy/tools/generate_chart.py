"""Agent-facing chart generation tool.

Exposes the shared rendering engine to the LLM agent. The tool queries
DuckDB for data, calls ``render_chart()``, and returns the file path as a
string — no inline/multimodal content (INV-008).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class GenerateChartParameters(ParametersModel):
    """Parameters for the generate_chart tool."""

    data_source: str = Field(
        ...,
        description=(
            "Table name in DuckDB (e.g. 'sales_data') or a SQL SELECT query "
            "(e.g. 'SELECT category, SUM(revenue) as total FROM sales GROUP BY category'). "
            "If a table name is given, all columns are fetched. If a SQL query is given, "
            "the query result is used as the data source."
        ),
    )
    chart_type: str = Field(
        ...,
        description=(
            "Type of chart to render. One of: bar, distribution, line, scatter, "
            "chord, parallel_coords, time_compass, sankey, treemap, sunburst, "
            "radar, violin, heatmap, correlogram."
        ),
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Chart configuration dict. Required fields depend on chart_type. "
            "Common optional fields: title, subtitle, x_axis_label, y_axis_label, "
            "width (default 1200), height (default 800), dpi (default 150), "
            "theme ('light' or 'dark'), color_palette (list of hex strings), "
            "font_size (default 14), background ('white' or 'transparent'). "
            "Chart-specific fields: bar needs {x, y, color_by?, orientation?, mode?}, "
            "scatter needs {x, y, color_by?, size_by?}, line needs {x, y (list), color_by?}, "
            "etc. See chart-types endpoint for full field role definitions."
        ),
    )

    def info(self) -> str:
        return f'chart_type="{self.chart_type}" data_source="{self.data_source[:50]}"'


class GenerateChartTool(BaseTool):
    """Generate a chart from DuckDB data and return the PNG file path."""

    name = "generate_chart"
    description = (
        "Generate a chart from DuckDB data and save it as a PNG file. "
        "Returns the file path to the generated chart. "
        "The chart is rendered server-side using matplotlib."
    )
    parameters_model = GenerateChartParameters
    needs_verification_in_api: bool = False

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute chart generation.

        Args:
            data_source: Table name or SQL query.
            chart_type: Chart type id (e.g. 'bar', 'scatter').
            config: Chart configuration dict.

        Returns:
            File path string to the generated PNG, or an error message.
        """
        data_source: str = kwargs["data_source"]
        chart_type: str = kwargs["chart_type"]
        config: dict[str, Any] = kwargs.get("config", {})

        try:
            from wichy.tools.duckdb_manager import DuckDBManager
            from wichy.tools.viz.engine import (
                ChartConfigError,
                ChartNotFoundError,
                ChartRenderError,
                render_chart,
            )

            # Determine if data_source is a table name or SQL query
            is_sql = data_source.strip().upper().startswith("SELECT")

            if is_sql:
                query = data_source
                table_desc = "SQL query"
            else:
                # Validate table name (prevent SQL injection)
                # Simple check: table names should be alphanumeric + underscores
                if not all(c.isalnum() or c == "_" for c in data_source):
                    return format_error(
                        f"Invalid table name: {data_source}. "
                        "Table names must be alphanumeric with underscores."
                    )
                query = f"SELECT * FROM {data_source}"
                table_desc = data_source

            # Add row limit for safety (INV-013: max 50,000 rows)
            if "LIMIT" not in query.upper():
                query = f"{query} LIMIT 50000"

            # Execute query and get rows as dicts
            manager = DuckDBManager.get_instance()
            with manager.get_connection() as conn:
                result = conn.execute(query)
                if result.description is None:
                    return format_error("Query returned no result set.")
                columns = [desc[0] for desc in result.description]
                rows = result.fetchall()

            data_rows = [dict(zip(columns, row)) for row in rows]

            if not data_rows:
                return format_error("Query returned 0 rows. Cannot render chart.")

            # Render the chart
            png_path = render_chart(
                chart_type=chart_type,
                data_rows=data_rows,
                config_dict=config,
                table=table_desc,
            )

            return str(png_path)

        except ChartNotFoundError:
            return format_error(f"Unknown chart type: {chart_type}")
        except ChartConfigError as e:
            return format_error(f"Invalid chart config: {e}")
        except ChartRenderError as e:
            return format_error(f"Chart rendering failed: {e}")
        except Exception as e:
            return format_error(f"Chart generation failed: {e}")
