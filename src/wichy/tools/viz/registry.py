"""Chart type registry — registry-driven chart type definitions.

Each chart type registers a ``ChartTypeDefinition`` containing its id, label,
category, icon, field roles, Pydantic config model, and renderer function.
Adding a new chart type requires only calling ``register_chart_type`` — no
if/elif chains (INV-010).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pydantic import BaseModel


@dataclass
class FieldRole:
    """A field mapping role for a chart type (e.g. 'x', 'y', 'color_by')."""

    name: str
    """Role name (e.g. ``"x"``, ``"y"``, ``"color_by"``)."""

    type: str
    """Expected data type: ``"category"``, ``"numeric"``, ``"date"``."""

    required: bool = True
    """Whether this role must be mapped to a column."""

    multiple: bool = False
    """Whether multiple columns can be mapped to this role."""


@dataclass
class ChartTypeDefinition:
    """Definition of a chart type in the registry."""

    id: str
    """Unique chart type identifier (e.g. ``"bar"``, ``"scatter"``)."""

    label: str
    """Human-readable label for the chart type."""

    category: str
    """Category for grouping (e.g. ``"basic"``, ``"statistical"``, ``"hierarchical"``)."""

    icon: str
    """Emoji or icon string for the chart type."""

    field_roles: list[FieldRole] = field(default_factory=list)
    """Field roles expected by this chart type."""

    config_model: Optional[type[BaseModel]] = None
    """Pydantic config model for validating chart configs."""

    renderer: Optional[Callable[[list[dict[str, Any]], BaseModel, Any], None]] = None
    """Renderer function: (data_rows, config, output_path) -> None.

    The renderer creates the chart and saves it to ``output_path`` (a
    ``pathlib.Path``). It does not return anything — the engine handles
    metadata and returns the path.
    """


# Module-level registry dict (INV-010: registry-driven, no if/elif)
CHART_REGISTRY: dict[str, ChartTypeDefinition] = {}


def register_chart_type(
    chart_id: str,
    label: str,
    category: str,
    icon: str,
    field_roles: list[FieldRole],
    config_model: type[BaseModel],
    renderer: Callable[[list[dict[str, Any]], BaseModel, Any], None],
) -> None:
    """Register a chart type in the global registry.

    Adding a new chart type requires only calling this function — no if/elif
    chains (INV-010).

    Args:
        chart_id: Unique chart type identifier.
        label: Human-readable label.
        category: Category for grouping.
        icon: Emoji or icon string.
        field_roles: List of field roles expected by this chart type.
        config_model: Pydantic config model class.
        renderer: Renderer function ``(data_rows, config, output_path) -> None``.
    """
    CHART_REGISTRY[chart_id] = ChartTypeDefinition(
        id=chart_id,
        label=label,
        category=category,
        icon=icon,
        field_roles=field_roles,
        config_model=config_model,
        renderer=renderer,
    )


def get_chart_type(chart_id: str) -> Optional[ChartTypeDefinition]:
    """Look up a chart type by id. Returns ``None`` if not found."""
    return CHART_REGISTRY.get(chart_id)


def get_chart_types() -> list[dict[str, Any]]:
    """Return a list of chart type metadata for API consumption.

    Each entry contains: ``id``, ``label``, ``category``, ``icon``,
    ``field_roles`` (as dicts).
    """
    result: list[dict[str, Any]] = []
    for defn in CHART_REGISTRY.values():
        result.append(
            {
                "id": defn.id,
                "label": defn.label,
                "category": defn.category,
                "icon": defn.icon,
                "field_roles": [
                    {
                        "name": role.name,
                        "type": role.type,
                        "required": role.required,
                        "multiple": role.multiple,
                    }
                    for role in defn.field_roles
                ],
            }
        )
    return result
