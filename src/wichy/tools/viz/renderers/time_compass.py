"""Time compass renderer (matplotlib).

Central time axis radiating bidirectionally. Uses polar projection with
time mapped to angular position. Each time period gets a radial segment
whose length and direction represent the value.

The compass has:
- Angular axis: time periods arranged clockwise from top
- Radial axis: value magnitude, radiating outward (positive) and inward (negative)
- Optional group_by for multiple series

Period labels are auto-detected from the data (months, days of week,
hours, quarters, etc.) or can be explicitly provided via ``periods``.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import numpy as np

from wichy.tools.viz.config_models import TimeCompassConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    DEFAULT_COLORS,
    apply_theme,
    create_figure,
    extract_column,
    mpl_to_png,
)

# ---------------------------------------------------------------------------
# Period detection
# ---------------------------------------------------------------------------

_MONTH_LABELS = [
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

_DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_HOUR_LABELS = [f"{h:02d}:00" for h in range(24)]

_QUARTER_LABELS = ["Q1", "Q2", "Q3", "Q4"]


def _parse_to_datetime(val: Any) -> datetime.datetime | None:
    """Try to parse a value into a datetime object."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.date):
        return datetime.datetime(val.year, val.month, val.day)
    try:
        return datetime.datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return None


def _detect_periods_and_map(
    time_vals: list[Any],
) -> tuple[list[str], list[int]]:
    """Auto-detect time granularity and map each row to a period index.

    Examines the parsed datetime values to determine the finest granularity
    that distinguishes the data points:
    - If values span multiple months → monthly (12 periods)
    - If all same month but different days → daily (by day-of-month)
    - If all same day but different hours → hourly (24 periods)
    - If values look like quarters → quarterly (4 periods)
    - Fallback: use unique values as-is sorted

    Returns:
        Tuple of (period_labels, period_indices) where period_indices[i]
        is the index into period_labels for row i.
    """
    dts = [_parse_to_datetime(v) for v in time_vals]
    valid_dts = [d for d in dts if d is not None]

    if not valid_dts:
        # No parseable dates — use unique raw values as labels
        unique_vals: list[str] = []
        seen: set[str] = set()
        for v in time_vals:
            s = str(v) if v is not None else "None"
            if s not in seen:
                seen.add(s)
                unique_vals.append(s)
        # Map each row to its index
        val_to_idx = {v: i for i, v in enumerate(unique_vals)}
        indices = [
            val_to_idx.get(str(v) if v is not None else "None", 0) for v in time_vals
        ]
        return unique_vals, indices

    # Check granularity: collect the set of months, days, hours
    months = set(d.month for d in valid_dts)
    days = set(d.day for d in valid_dts)
    hours = set(d.hour for d in valid_dts)

    # Determine the granularity level
    # If multiple years and 12 or fewer unique months → monthly
    # If single year and multiple months → monthly
    # If single month, multiple days → daily (by day of month)
    # If single day, multiple hours → hourly
    # If 4 or fewer unique months and spans full year → quarterly

    if len(months) > 1:
        # Check if it's quarterly (months are 1,4,7,10 or similar)
        sorted_months = sorted(months)
        if (
            len(months) <= 4
            and all(m in {1, 4, 7, 10} or m in {1, 4, 7, 10, 12} for m in sorted_months)
            and len(months) <= 4
        ):
            # Could be quarterly — use quarter labels
            labels = _QUARTER_LABELS
            indices = [_map_to_quarter(d) for d in dts]
            return labels, indices
        else:
            # Monthly
            labels = _MONTH_LABELS
            indices = [(d.month - 1) if d is not None else 0 for d in dts]
            return labels, indices
    elif len(days) > 1:
        # Daily — use day-of-month
        max_day = max(days)
        if max_day <= 7:
            # Could be day-of-week if days are 1-7
            # But day-of-month 1-7 is ambiguous; use day labels
            labels = [str(d) for d in range(1, max_day + 1)]
            indices = [(d.day - 1) if d is not None else 0 for d in dts]
            return labels, indices
        else:
            # Day of month
            labels = [str(d) for d in range(1, max_day + 1)]
            indices = [(d.day - 1) if d is not None else 0 for d in dts]
            return labels, indices
    elif len(hours) > 1:
        # Hourly
        labels = _HOUR_LABELS
        indices = [d.hour if d is not None else 0 for d in dts]
        return labels, indices
    else:
        # All same timestamp or very low variety — use unique values
        unique_strs: list[str] = []
        seen_s: set[str] = set()
        for v in time_vals:
            s = str(v) if v is not None else "None"
            if s not in seen_s:
                seen_s.add(s)
                unique_strs.append(s)
        val_to_idx = {v: i for i, v in enumerate(unique_strs)}
        indices = [
            val_to_idx.get(str(v) if v is not None else "None", 0) for v in time_vals
        ]
        return unique_strs, indices


def _map_to_quarter(dt: datetime.datetime | None) -> int:
    """Map a datetime to a quarter index (0-3)."""
    if dt is None:
        return 0
    return (dt.month - 1) // 3


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------


def render_time_compass(
    data_rows: list[dict[str, Any]],
    config: TimeCompassConfig,
    output_path: Path,
) -> None:
    """Render a time compass and save it to *output_path*.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Time compass config (time, value, group_by, periods).
        output_path: Destination PNG file path.
    """
    if not data_rows:
        fig, ax = create_figure(config)
        apply_theme(fig, ax, config)
        mpl_to_png(fig, config, output_path)
        return

    raw_values = extract_column(data_rows, config.value)
    values = [float(v) if v is not None else 0.0 for v in raw_values]

    # Determine period labels and mapping
    if config.periods:
        # Explicit periods provided
        period_labels = list(config.periods)
        n_periods = len(period_labels)

        # Map each row to a period index
        if config.period_column:
            # Use period_column values to match against period labels
            period_vals = extract_column(data_rows, config.period_column)
            indices = []
            for pv in period_vals:
                s = str(pv) if pv is not None else ""
                if s in period_labels:
                    indices.append(period_labels.index(s))
                else:
                    # Try as 1-based or 0-based index
                    try:
                        idx = int(s)
                        if 1 <= idx <= n_periods:
                            indices.append(idx - 1)
                        elif 0 <= idx < n_periods:
                            indices.append(idx)
                        else:
                            indices.append(0)
                    except (ValueError, TypeError):
                        indices.append(0)
            else:
                pass  # indices already built in loop
        else:
            # Use time column to match against period labels
            time_vals = extract_column(data_rows, config.time)
            indices = []
            for tv in time_vals:
                s = str(tv) if tv is not None else ""
                if s in period_labels:
                    indices.append(period_labels.index(s))
                else:
                    # Try parsing as datetime and extracting the period
                    dt = _parse_to_datetime(tv)
                    if dt is not None:
                        if period_labels == _MONTH_LABELS:
                            indices.append(dt.month - 1)
                        elif period_labels == _DOW_LABELS:
                            indices.append(dt.weekday())
                        elif period_labels == _HOUR_LABELS:
                            indices.append(dt.hour)
                        elif period_labels == _QUARTER_LABELS:
                            indices.append(_map_to_quarter(dt))
                        else:
                            indices.append(0)
                    else:
                        # Try as numeric index
                        try:
                            idx = int(float(s))
                            if 0 <= idx < n_periods:
                                indices.append(idx)
                            elif 1 <= idx <= n_periods:
                                indices.append(idx - 1)
                            else:
                                indices.append(0)
                        except (ValueError, TypeError):
                            indices.append(0)
    else:
        # Auto-detect periods from data
        time_vals = extract_column(data_rows, config.time)
        if config.period_column:
            # Use period_column for mapping, but still need labels
            period_col_vals = extract_column(data_rows, config.period_column)
            unique_periods: list[str] = []
            seen_p: set[str] = set()
            for pv in period_col_vals:
                s = str(pv) if pv is not None else "None"
                if s not in seen_p:
                    seen_p.add(s)
                    unique_periods.append(s)
            period_labels = unique_periods
            val_to_idx = {v: i for i, v in enumerate(period_labels)}
            indices = [
                val_to_idx.get(str(pv) if pv is not None else "None", 0)
                for pv in period_col_vals
            ]
        else:
            period_labels, indices = _detect_periods_and_map(time_vals)

    n_periods = len(period_labels)
    if n_periods == 0:
        n_periods = 1
        period_labels = ["?"]

    # Compute angles for each period (evenly spaced, clockwise from top)
    period_angles = [i / n_periods * 2 * np.pi for i in range(n_periods)]

    # Set up polar axes
    fig, _default_ax = create_figure(config)
    _default_ax.remove()
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)  # Clockwise

    # Adjust axes position to leave room for title/subtitle at top
    # and legend on the right.  Polar tick labels extend beyond the
    # axes bounding box, so we need generous margins.
    top_margin = 0.12
    if config.title:
        top_margin += 0.06
    if config.subtitle:
        top_margin += 0.05

    has_legend = bool(config.group_by)
    right_margin = 0.22 if has_legend else 0.06
    bottom_margin = 0.10
    left_margin = 0.10

    available_w = 1.0 - left_margin - right_margin
    available_h = 1.0 - top_margin - bottom_margin
    side = min(available_w, available_h)
    ax_x = left_margin + (available_w - side) / 2
    ax_y = bottom_margin + (available_h - side) / 2
    ax.set_position([ax_x, ax_y, side, side])

    # Draw bars
    max_abs_val = max(abs(v) for v in values) if values else 1.0
    if max_abs_val == 0:
        max_abs_val = 1.0

    colors = config.color_palette if config.color_palette else DEFAULT_COLORS

    # Bar width: slightly less than the angular gap between periods
    bar_width = (2 * np.pi / n_periods) * 0.7

    if config.group_by:
        group_vals = extract_column(data_rows, config.group_by)
        groups: dict[str, list[int]] = {}
        for i, g in enumerate(group_vals):
            g_key = str(g) if g is not None else "None"
            groups.setdefault(g_key, []).append(i)

        for gi, (gname, indices_g) in enumerate(groups.items()):
            color = colors[gi % len(colors)]
            g_values = [values[i] for i in indices_g]

            # Aggregate values per period (sum if multiple rows map to same period)
            period_sums: dict[int, float] = {}
            for a_idx, v in zip(indices_g, g_values):
                p_idx = indices[a_idx] % n_periods
                period_sums[p_idx] = period_sums.get(p_idx, 0.0) + v

            # Draw bars for each period
            labeled = False
            for p_idx, total_val in sorted(period_sums.items()):
                normalized = total_val / max_abs_val
                a = period_angles[p_idx]
                ax.bar(
                    a,
                    abs(normalized),
                    width=bar_width,
                    bottom=0,
                    color=color,
                    alpha=0.7,
                    label=gname if not labeled else None,
                )
                labeled = True

            # Connect points with a line
            sorted_periods = sorted(period_sums.keys())
            if sorted_periods:
                line_angles = [period_angles[p] for p in sorted_periods]
                line_values = [period_sums[p] / max_abs_val for p in sorted_periods]
                # Close the loop
                line_angles.append(line_angles[0])
                line_values.append(line_values[0])
                ax.plot(
                    line_angles,
                    [max(0, v) for v in line_values],
                    "o-",
                    color=color,
                    markersize=4,
                )
    else:
        color = colors[0]
        # Aggregate values per period
        period_sums: dict[int, float] = {}
        for i, v in enumerate(values):
            p_idx = indices[i] % n_periods
            period_sums[p_idx] = period_sums.get(p_idx, 0.0) + v

        for p_idx, total_val in sorted(period_sums.items()):
            normalized = total_val / max_abs_val
            a = period_angles[p_idx]
            ax.bar(
                a, abs(normalized), width=bar_width, bottom=0, color=color, alpha=0.7
            )

        # Connect points
        sorted_periods = sorted(period_sums.keys())
        if sorted_periods:
            line_angles = [period_angles[p] for p in sorted_periods]
            line_values = [period_sums[p] / max_abs_val for p in sorted_periods]
            line_angles.append(line_angles[0])
            line_values.append(line_values[0])
            ax.plot(
                line_angles,
                [max(0, v) for v in line_values],
                "o-",
                color=color,
                markersize=4,
            )

    # Set period labels on the angular axis
    ax.set_xticks(period_angles)
    ax.set_xticklabels(period_labels, fontsize=config.font_size - 2)

    ax.set_ylim(0, 1.15)
    ax.set_yticks([])

    if config.group_by:
        ax.legend(
            fontsize=config.font_size - 2,
            loc="center left",
            bbox_to_anchor=(1.25, 0.5),
            frameon=False,
        )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="time_compass",
    label="Time Compass",
    category="multivariate",
    icon="\U0001f9ed",
    field_roles=[
        FieldRole(name="time", type="date", required=True),
        FieldRole(name="value", type="numeric", required=True),
        FieldRole(name="group_by", type="category", required=False),
        FieldRole(name="periods", type="category", required=False, multiple=True),
        FieldRole(name="period_column", type="category", required=False),
    ],
    config_model=TimeCompassConfig,
    renderer=render_time_compass,
)
