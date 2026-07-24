"""Human-readable chart type information for agent discovery.

Provides formatting functions that introspect the chart registry and config
models to produce guidance text. Used by:

- ``ChartInfoTool`` — agent-facing discovery tool.
- ``GenerateChartTool`` — enriched error messages on config validation failure.
"""

from __future__ import annotations

from typing import Any, Optional

from wichy.tools.viz.registry import (
    CHART_REGISTRY,
    ChartTypeDefinition,
    FieldRole,
    get_chart_type,
    get_chart_types,
)


def _type_label(field_type: str) -> str:
    """Convert a FieldRole type to a human-readable label."""
    labels = {
        "category": "categorical (string/category)",
        "numeric": "numeric",
        "date": "date/time",
        "any": "any (numeric, category, or date)",
    }
    return labels.get(field_type, field_type)


def _role_line(role: FieldRole) -> str:
    """Format a single FieldRole into a human-readable line."""
    req = "required" if role.required else "optional"
    multi = " (accepts multiple columns)" if role.multiple else ""
    return f"  - {role.name} ({req}): column of type {_type_label(role.type)}{multi}"


def _styling_fields() -> list[str]:
    """Return the list of common styling field names from BaseChartConfig."""
    return [
        "title",
        "subtitle",
        "x_axis_label",
        "y_axis_label",
        "width (default 1200)",
        "height (default 800)",
        "dpi (default 150)",
        "theme ('light' or 'dark')",
        "color_palette (list of hex color strings)",
        "font_size (default 14)",
        "background ('white' or 'transparent')",
    ]


def _config_specific_fields(defn: ChartTypeDefinition) -> list[str]:
    """Extract chart-specific (non-base) optional fields from the config model.

    Returns field names with their defaults or descriptions, excluding the
    required field-role fields and all BaseChartConfig fields.
    """
    if defn.config_model is None:
        return []

    from wichy.tools.viz.config_models import BaseChartConfig

    base_fields = set(BaseChartConfig.model_fields.keys())
    role_names = {r.name for r in defn.field_roles}
    extras: list[str] = []

    for name, field_info in defn.config_model.model_fields.items():
        if name in base_fields or name in role_names:
            continue
        # Build a compact description
        default = field_info.default
        if default is not None and default != "":
            extras.append(f"{name} (default: {default!r})")
        else:
            extras.append(name)

    return extras


def format_chart_info(chart_type: str) -> Optional[str]:
    """Format full details for a single chart type.

    Returns a multi-line string with:
    - Label, category, and icon
    - Required and optional field roles with types
    - Chart-specific config fields (beyond field roles)
    - Common styling fields
    - A minimal example config

    Returns ``None`` if the chart type is not registered.
    """
    defn = get_chart_type(chart_type)
    if defn is None:
        return None

    lines: list[str] = []
    lines.append(f"{defn.icon} {defn.label} (id: {defn.id})")
    lines.append(f"Category: {defn.category}")
    lines.append("")

    # Field roles
    required = [r for r in defn.field_roles if r.required]
    optional = [r for r in defn.field_roles if not r.required]

    if required:
        lines.append("Required config fields (column mappings):")
        for role in required:
            lines.append(_role_line(role))

    if optional:
        lines.append("")
        lines.append("Optional config fields (column mappings):")
        for role in optional:
            lines.append(_role_line(role))

    # Chart-specific extras (e.g. orientation, mode, subtype)
    extras = _config_specific_fields(defn)
    if extras:
        lines.append("")
        lines.append("Chart-specific options:")
        for e in extras:
            lines.append(f"  - {e}")

    # Styling
    lines.append("")
    lines.append("Common styling fields (all optional):")
    for s in _styling_fields():
        lines.append(f"  - {s}")

    # Example config
    lines.append("")
    lines.append("Example config:")
    example: dict[str, Any] = {}
    for role in required:
        example[role.name] = ["col_a", "col_b"] if role.multiple else "col_a"
    for role in optional:
        example[role.name] = ["col_b"] if role.multiple else "col_b"
    example["title"] = "My Chart"
    lines.append(f"  {example}")

    return "\n".join(lines)


def format_chart_summary() -> str:
    """Format a compact one-line-per-type summary of all registered chart types.

    Each line: ``id — label (required: x, y; optional: color_by)``
    """
    types = get_chart_types()
    if not types:
        return "No chart types are registered."

    lines: list[str] = []
    lines.append(f"Available chart types ({len(types)} total):")
    lines.append("")

    for ct in types:
        required = [r["name"] for r in ct["field_roles"] if r["required"]]
        optional = [r["name"] for r in ct["field_roles"] if not r["required"]]

        parts = [f"  {ct['icon']} {ct['id']} — {ct['label']}"]
        parts.append(f"  required: {', '.join(required)}")
        if optional:
            parts.append(f"  optional: {', '.join(optional)}")
        lines.append(" | ".join(parts))

    lines.append("")
    lines.append(
        "Call chart_info with a specific chart_type for full field details "
        "and example config."
    )
    return "\n".join(lines)


def format_chart_requirements(chart_type: str) -> Optional[str]:
    """Format a compact one-liner of required/optional fields for a chart type.

    Designed for embedding in error messages. Returns ``None`` if the chart
    type is not registered.

    Example output::

        bar requires: x (category), y (numeric); optional: color_by (category)
    """
    defn = get_chart_type(chart_type)
    if defn is None:
        return None

    required = [
        f"{r.name} ({_type_label(r.type)})" for r in defn.field_roles if r.required
    ]
    optional = [
        f"{r.name} ({_type_label(r.type)})" for r in defn.field_roles if not r.required
    ]

    parts: list[str] = []
    if required:
        parts.append(f"required: {', '.join(required)}")
    if optional:
        parts.append(f"optional: {', '.join(optional)}")

    return f"{chart_type} {'; '.join(parts)}"


def list_chart_type_ids() -> list[str]:
    """Return a sorted list of all registered chart type ids."""
    return sorted(CHART_REGISTRY.keys())
