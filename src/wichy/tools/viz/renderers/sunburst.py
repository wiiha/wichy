"""Sunburst chart renderer (matplotlib).

Hierarchical radial segments. Uses polar projection to draw concentric
rings representing the hierarchy.

Supports arbitrary depth: root nodes are placed on the innermost ring,
their children on the next ring outward, and so on. When a parent node
has a value of 0 (aggregate-only), its angular extent is computed from
the sum of its children's values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from wichy.tools.viz.config_models import SunburstChartConfig
from wichy.tools.viz.registry import FieldRole, register_chart_type
from wichy.tools.viz.renderers.matplotlib_base import (
    DEFAULT_COLORS,
    apply_theme,
    create_figure,
    extract_column,
    mpl_to_png,
)

# ---------------------------------------------------------------------------
# Hierarchy building
# ---------------------------------------------------------------------------


class _Node:
    """A single node in the sunburst hierarchy."""

    __slots__ = ("label", "index", "value", "children", "depth")

    def __init__(self, label: str, index: int, value: float, depth: int = 0):
        self.label: str = label
        self.index: int = index  # original row index (for color pick)
        self.value: float = value
        self.children: list[_Node] = []
        self.depth: int = depth

    @property
    def total_value(self) -> float:
        """Recursive sum of values (node + all descendants)."""
        if self.children:
            return self.value + sum(c.total_value for c in self.children)
        return self.value


def _build_hierarchy(
    labels: list[str],
    values: list[float],
    parents: list[str],
) -> list[_Node]:
    """Build a forest of ``_Node`` trees from flat rows.

    Args:
        labels: Node labels.
        values: Node values (may be 0 for aggregate-only parents).
        parents: Parent label for each node. ``""`` means root.

    Returns:
        List of root ``_Node`` objects.
    """
    # Map label → node (first occurrence wins if duplicate labels exist)
    all_nodes: dict[str, _Node] = {}
    orphan_indices: list[int] = []

    for i, (lbl, val) in enumerate(zip(labels, values)):
        if lbl in all_nodes:
            # Duplicate label — treat as orphan so it still renders
            orphan_indices.append(i)
            continue
        all_nodes[lbl] = _Node(lbl, i, val, depth=0)

    roots: list[_Node] = []
    for i, (lbl, parent) in enumerate(zip(labels, parents)):
        node = all_nodes[lbl]
        if parent == "" or parent not in all_nodes:
            node.depth = 0
            roots.append(node)
        else:
            parent_node = all_nodes[parent]
            node.depth = parent_node.depth + 1
            parent_node.children.append(node)

    # Attach orphans (duplicate labels) as roots so they still render
    for i in orphan_indices:
        roots.append(_Node(labels[i], i, values[i], depth=0))

    return roots


def _max_depth(roots: list[_Node]) -> int:
    """Return the maximum tree depth across all roots."""
    best = 0
    stack = list(roots)
    while stack:
        n = stack.pop()
        best = max(best, n.depth)
        stack.extend(n.children)
    return best


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

# Ring geometry — each ring occupies a radial band of this width.
_RING_HEIGHT = 1.0
# Small visual gap between rings.
_RING_GAP = 0.02


def _truncate_label(text: str, max_chars: int) -> str:
    """Truncate *text* to *max_chars*, appending ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "\u2026"


def _segment_label(
    fig: plt.Figure,
    ax: plt.Axes,
    theta: float,
    theta_width: float,
    r_inner: float,
    r_height: float,
    label: str,
    fontsize: float,
    text_color: str,
    outer_limit: float,
) -> None:
    """Place a label inside a polar wedge, adapting to wedge geometry.

    Uses pixel-space calculations to determine how many characters fit,
    so truncation is accurate regardless of DPI or figure size.
    """
    r_mid = r_inner + r_height / 2

    # --- Compute available space in pixels ---
    # The polar axes spans the figure with ylim [0, outer_limit].
    # Data-units → pixels: fig_pixels / data_range
    fig_w_in, fig_h_in = fig.get_size_inches()
    fig_w_pt = fig_w_in * 72.0  # 1 inch = 72 pt
    fig_h_pt = fig_h_in * 72.0

    # The polar plot is roughly square (circular), limited by the smaller dim
    plot_pt = min(fig_w_pt, fig_h_pt)
    # data range for radius is [0, outer_limit]
    r_to_pt = plot_pt / (2 * outer_limit)  # radius data-units to points

    # Arc length in points: theta_width (radians) * r_mid (data units) * r_to_pt
    arc_pt = theta_width * r_mid * r_to_pt
    # Radial height in points
    rad_pt = r_height * r_to_pt

    # Cap font size so text height fits within the ring height.
    # Text height ≈ fontsize * 1.3; leave 2pt padding top and bottom.
    max_fs_for_ring = (rad_pt - 4) / 1.3
    fontsize = min(fontsize, max_fs_for_ring)
    if fontsize < 5:
        # Ring too thin for any readable text — skip
        return

    # Character width in points: ~0.55 * fontsize for bold sans
    char_w_pt = 0.55 * fontsize
    # How many chars fit along the arc?
    max_chars_arc = max(1, int((arc_pt - 4) / char_w_pt))  # -4pt padding

    # How many chars fit along the radius (for radial orientation)?
    max_chars_rad = max(1, int((rad_pt - 4) / char_w_pt))

    # --- Choose orientation and truncate ---
    use_radial = theta_width < 0.35 and r_height > 0.5 and max_chars_rad >= 3

    if use_radial:
        max_chars = max_chars_rad
    else:
        max_chars = max_chars_arc

    # Always allow at least 3 chars if the wedge has any reasonable size
    if arc_pt > 15 or rad_pt > 15:
        max_chars = max(max_chars, 3)

    truncated = _truncate_label(label, max_chars)

    # Skip entirely if even 3 chars don't fit and the wedge is tiny
    if max_chars < 2:
        return

    # --- Place the text ---
    if use_radial:
        # Radial text — place at mid-radius, rotated along the wedge
        angle_deg = np.degrees(theta + theta_width / 2)
        # Flip text if it would be upside-down (left half of chart)
        rotation = angle_deg - 90
        if 90 < angle_deg < 270:
            rotation += 180
        ax.text(
            theta + theta_width / 2,
            r_mid,
            truncated,
            ha="center",
            va="center",
            rotation=rotation,
            rotation_mode="anchor",
            fontsize=fontsize,
            color=text_color,
            fontweight="bold",
        )
    else:
        # Tangential text — standard horizontal in the wedge
        ax.text(
            theta + theta_width / 2,
            r_mid,
            truncated,
            ha="center",
            va="center",
            fontsize=fontsize,
            color=text_color,
            fontweight="bold",
        )


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------


def render_sunburst(
    data_rows: list[dict[str, Any]],
    config: SunburstChartConfig,
    output_path: Path,
) -> None:
    """Render a sunburst chart and save it to *output_path*.

    For flat data (no parent), draws a single ring. For hierarchical data
    (with parent), draws concentric rings — one per depth level.

    Args:
        data_rows: List of row dicts with column names as keys.
        config: Sunburst config (labels, values, parent).
        output_path: Destination PNG file path.
    """
    labels = [
        str(lbl) if lbl is not None else ""
        for lbl in extract_column(data_rows, config.labels)
    ]
    raw_values = extract_column(data_rows, config.values)
    values = [float(v) if v is not None else 0.0 for v in raw_values]

    if config.parent:
        parents = [
            str(p) if p is not None else ""
            for p in extract_column(data_rows, config.parent)
        ]
    else:
        parents = [""] * len(labels)

    # Build hierarchy
    roots = _build_hierarchy(labels, values, parents)
    if not roots:
        # Nothing to draw — create a blank figure
        fig, ax = create_figure(config)
        apply_theme(fig, ax, config)
        mpl_to_png(fig, config, output_path)
        return

    # Compute effective values: if a node has children, use max(own value,
    # sum of children) so aggregate parents with value=0 still get an arc.
    for root in roots:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.children:
                child_sum = sum(c.total_value for c in node.children)
                node.value = max(node.value, child_sum)
                stack.extend(node.children)

    grand_total = sum(r.total_value for r in roots) or 1.0

    # Set up polar axes (replace the default Cartesian axes from create_figure)
    fig, _default_ax = create_figure(config)
    _default_ax.remove()  # discard the unused Cartesian axes
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.axis("off")

    colors = config.color_palette if config.color_palette else DEFAULT_COLORS
    max_d = _max_depth(roots)
    outer_limit = (max_d + 1) * (_RING_HEIGHT + _RING_GAP)
    ax.set_ylim(0, outer_limit)

    # Track color→label pairs for the legend.
    # For hierarchical charts, only leaf nodes go in the legend (parents are
    # visible as ring labels). For flat charts, all nodes appear.
    legend_entries: list[tuple[str, str]] = []

    # Recursive draw
    def _draw_node(
        node: _Node,
        theta_start: float,
        theta_width: float,
        color_idx: int,
    ) -> None:
        """Draw a wedge for *node* and recurse into children."""
        r_inner = node.depth * (_RING_HEIGHT + _RING_GAP)
        color = colors[color_idx % len(colors)]

        ax.bar(
            theta_start + theta_width / 2,
            _RING_HEIGHT,
            width=theta_width,
            bottom=r_inner,
            color=color,
            edgecolor="white",
            linewidth=1 if node.depth == 0 else 0.5,
        )

        # Label — always try to place it; _segment_label handles truncation
        label_fs = max(6, config.font_size - 2 - node.depth)
        _segment_label(
            fig,
            ax,
            theta_start,
            theta_width,
            r_inner,
            _RING_HEIGHT,
            node.label,
            fontsize=label_fs,
            text_color="white",
            outer_limit=outer_limit,
        )

        # Children share the parent's angular extent proportionally
        if node.children:
            child_total = sum(c.total_value for c in node.children) or 1.0
            child_theta = theta_start
            for ci, child in enumerate(node.children):
                child_frac = child.total_value / child_total
                child_width = child_frac * theta_width
                # Color: offset from parent so siblings differ visually
                child_color_idx = color_idx + ci + 1
                _draw_node(child, child_theta, child_width, child_color_idx)
                child_theta += child_width
        else:
            # Leaf node — add to legend
            legend_entries.append((node.label, color))

    # Draw all root nodes
    theta_cursor = 0.0
    for ri, root in enumerate(roots):
        root_frac = root.total_value / grand_total
        root_width = root_frac * 2 * np.pi
        _draw_node(root, theta_cursor, root_width, color_idx=ri)
        theta_cursor += root_width

    # If the chart is flat (no children at all), roots are the legend entries
    # (already collected as leaf nodes above). For hierarchical charts, only
    # leaves were collected.

    # Add legend — deduplicate by label (first color wins)
    if legend_entries:
        seen: set[str] = set()
        handles: list[mpatches.Patch] = []
        labels_out: list[str] = []
        for lbl, clr in legend_entries:
            if lbl not in seen:
                seen.add(lbl)
                handles.append(mpatches.Patch(facecolor=clr, edgecolor="white"))
                labels_out.append(lbl)
        if handles:
            ax.legend(
                handles,
                labels_out,
                fontsize=config.font_size - 2,
                loc="center left",
                bbox_to_anchor=(1.01, 0.5),
                frameon=False,
            )

    apply_theme(fig, ax, config)
    mpl_to_png(fig, config, output_path)


register_chart_type(
    chart_id="sunburst",
    label="Sunburst",
    category="hierarchical",
    icon="\u2600\ufe0f",
    field_roles=[
        FieldRole(name="labels", type="category", required=True),
        FieldRole(name="values", type="numeric", required=True),
        FieldRole(name="parent", type="category", required=False),
    ],
    config_model=SunburstChartConfig,
    renderer=render_sunburst,
)
