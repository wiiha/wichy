"""Data visualization rendering engine.

Public API:
    - ``render_chart`` — render a chart from data rows and config
    - ``CHART_REGISTRY`` — global chart type registry
    - ``get_chart_types`` — list all registered chart types as dicts

Importing this package also triggers registration of all built-in chart
types via the ``renderers`` subpackage.
"""

from __future__ import annotations

# Import registry first (defines CHART_REGISTRY and helpers)
from wichy.tools.viz.registry import (
    CHART_REGISTRY,
    ChartTypeDefinition,
    FieldRole,
    get_chart_type,
    get_chart_types,
    register_chart_type,
)

# Import renderers to trigger chart type registration (side effect)
import wichy.tools.viz.renderers  # noqa: F401

__all__ = [
    "CHART_REGISTRY",
    "ChartTypeDefinition",
    "FieldRole",
    "get_chart_type",
    "get_chart_types",
    "register_chart_type",
]
