"""Time compass renderer (matplotlib).

Central time axis radiating bidirectionally. Uses polar projection with
time mapped to angular position. Each time period gets a radial segment
whose length and direction represent the value.

The compass has:
- Angular axis: time periods (e.g., months, hours) arranged clockwise
- Radial axis: value magnitude, radiating outward (positive) and inward (negative)
- Optional group_by for multiple series
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import TimeCompassConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    apply_theme,
    create_figure,
    extract_column,
    get_colors,
    mpl_to_png,
)


def _parse_time(time_val: Any) -> float:
    """Parse a time value to a numeric angle position (0-1).

    Handles ISO date strings, datetime objects, and numeric values.
    Returns a value in [0, 1) representing position on the circle.
    """
    if time_val is None:
        return 0.0

    if isinstance(time_val, (int, float)):
        return float(time_val) % 1.0

    if isinstance(time_val, datetime.datetime):
        # Map to day-of-year position (0-1)
        day_of_year = time_val.timetuple().tm_yday
        return (day_of_year - 1) / 365.0

    if isinstance(time_val, datetime.date):
        day_of_year = time_val.timetuple().tm_yday
        return (day_of_year - 1) / 365.0

    # Try to parse as ISO date string
    try:
        dt = datetime.datetime.fromisoformat(str(time_val))
        day_of_year = dt.timetuple().tm_yday
        return (day_of_year - 1) / 365.0
    except (ValueError, TypeError):
        pass

    # Try to extract month from string (e.g., "2024-03")
    try:
        parts = str(time_val).split("-")
        if len(parts) >= 2:
            month = int(parts[1])
            return (month - 1) / 12.0
    except (ValueError, IndexError):
        pass

    return 0.0


def render_time_compass(
    data_rows: list[dict[str, Any]],
    config: TimeCompassConfig,
    output_path: Path,
) -> None:
    """Render a time compass and save it to output_path.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Time compass config (time, value, group_by).
        output_path: Destination PNG file path.
    """
    time_vals = extract_column(data_rows, config.time)
    value_vals = extract_column(data_rows, config.value)

    # Parse times to angular positions (0-1 → 0 to 2π)
    angles = [_parse_time(t) * 2 * np.pi for t in time_vals]
    values = [float(v) if v is not None else 0.0 for v in value_vals]

    fig = create_figure(config)[0]
    colors = get_colors(config)

    # Use polar projection
    fig.clear()
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # Clockwise

    # Draw center circle
    max_abs_val = max(abs(v) for v in values) if values else 1.0
    if max_abs_val == 0:
        max_abs_val = 1.0

    if config.group_by:
        group_vals = extract_column(data_rows, config.group_by)
        groups: dict[str, list[int]] = {}
        for i, g in enumerate(group_vals):
            g_key = str(g) if g is not None else "None"
            groups.setdefault(g_key, []).append(i)

        for gi, (gname, indices) in enumerate(groups.items()):
            color = colors[gi % len(colors)]
            g_angles = [angles[i] for i in indices]
            g_values = [values[i] for i in indices]

            # Positive values radiate outward, negative inward
            for a, v in zip(g_angles, g_values):
                normalized = v / max_abs_val
                if normalized >= 0:
                    ax.bar(
                        a,
                        abs(normalized),
                        width=0.1,
                        bottom=0,
                        color=color,
                        alpha=0.7,
                        label=gname if a == g_angles[0] else None,
                    )
                else:
                    ax.bar(
                        a, abs(normalized), width=0.1, bottom=0, color=color, alpha=0.7
                    )

            # Connect points with a line
            sorted_pairs = sorted(zip(g_angles, g_values), key=lambda x: x[0])
            if sorted_pairs:
                line_angles = [p[0] for p in sorted_pairs]
                line_values = [p[1] / max_abs_val for p in sorted_pairs]
                ax.plot(
                    line_angles,
                    [max(0, v) for v in line_values],
                    "o-",
                    color=color,
                    markersize=4,
                    label=gname,
                )
    else:
        color = colors[0]
        for a, v in zip(angles, values):
            normalized = v / max_abs_val
            ax.bar(a, abs(normalized), width=0.1, bottom=0, color=color, alpha=0.7)

        # Connect points with a line
        sorted_pairs = sorted(zip(angles, values), key=lambda x: x[0])
        if sorted_pairs:
            line_angles = [p[0] for p in sorted_pairs]
            line_values = [p[1] / max_abs_val for p in sorted_pairs]
            ax.plot(
                line_angles,
                [max(0, v) for v in line_values],
                "o-",
                color=color,
                markersize=4,
            )

    # Add month labels
    month_labels = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    month_angles = [i / 12 * 2 * np.pi for i in range(12)]
    ax.set_xticks(month_angles)
    ax.set_xticklabels(month_labels, fontsize=config.font_size - 2)

    ax.set_ylim(0, 1.1)
    ax.set_yticks([])

    if config.group_by:
        ax.legend(
            fontsize=config.font_size - 2, loc="upper right", bbox_to_anchor=(1.3, 1.1)
        )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="time_compass",
    label="Time Compass",
    category="multivariate",
    icon="🧭",
    field_roles=[
        FieldRole(name="time", type="date", required=True),
        FieldRole(name="value", type="numeric", required=True),
        FieldRole(name="group_by", type="category", required=False),
    ],
    config_model=TimeCompassConfig,
    renderer=render_time_compass,
)
