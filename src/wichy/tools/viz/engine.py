"""Chart rendering engine — the shared entry point for all chart rendering.

``render_chart()`` is called by both GUI API routes and the agent tool
(INV-002). It validates the config via the registry, delegates to the chart
type's renderer function, saves the PNG, writes sidecar metadata, and
returns the PNG file path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wichy.tools.viz.config_models import validate_config
from wichy.tools.viz.metadata import generate_chart_id, get_charts_dir, save_meta
from wichy.tools.viz.registry import get_chart_type


class ChartRenderError(Exception):
    """Raised when chart rendering fails."""

    pass


class ChartConfigError(ChartRenderError):
    """Raised when chart config validation fails (maps to HTTP 400)."""

    pass


class ChartNotFoundError(ChartRenderError):
    """Raised when a requested chart type is not in the registry."""

    pass


def render_chart(
    chart_type: str,
    data_rows: list[dict[str, Any]],
    config_dict: dict[str, Any],
    table: str = "",
) -> Path:
    """Render a chart and return the PNG file path.

    This is the single shared entry point (INV-002) called by both GUI API
    routes and the agent tool.

    Steps:
        1. Look up chart type in the registry.
        2. Validate config against the chart type's Pydantic model.
        3. Generate a chart ID and build the output path.
        4. Call the chart type's renderer function with the output path.
        5. Write sidecar metadata.
        6. Return the PNG file path.

    Args:
        chart_type: Chart type id (e.g. ``"bar"``, ``"scatter"``).
        data_rows: List of dicts (rows) to render. Each dict is a row with
            column names as keys.
        config_dict: Chart config dict (validated by Pydantic model).
        table: Source table name or description (for metadata).

    Returns:
        Path to the generated PNG file.

    Raises:
        ChartNotFoundError: If the chart type is not registered.
        ChartConfigError: If config validation fails (INV-003).
        ChartRenderError: If the renderer fails.
    """
    # 1. Look up chart type
    defn = get_chart_type(chart_type)
    if defn is None:
        raise ChartNotFoundError(f"Unknown chart type: {chart_type}")

    # 2. Validate config
    config, err = validate_config(chart_type, config_dict)
    if config is None or err is not None:
        raise ChartConfigError(f"Invalid config for {chart_type}: {err}")

    # 3. Generate chart ID and output path
    chart_id = generate_chart_id()
    charts_dir = get_charts_dir()
    output_path = charts_dir / f"{chart_id}.png"

    # 4. Call the renderer
    if defn.renderer is None:
        raise ChartRenderError(f"No renderer for chart type: {chart_type}")

    try:
        defn.renderer(data_rows, config, output_path)
    except Exception as exc:
        raise ChartRenderError(f"Renderer failed: {exc}") from exc

    # 5. Write sidecar metadata
    title = config_dict.get("title")
    subtitle = config_dict.get("subtitle")
    save_meta(
        chart_id=chart_id,
        chart_type=chart_type,
        table=table,
        title=title,
        subtitle=subtitle,
    )

    # 6. Return the PNG file path
    return output_path
