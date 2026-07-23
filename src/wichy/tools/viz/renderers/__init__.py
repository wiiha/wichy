"""Renderer modules — importing this package triggers chart type registration.

Each renderer module calls ``register_chart_type()`` at import time, adding
its entry to the global ``CHART_REGISTRY``. This file imports all renderer
modules so that simply importing ``wichy.tools.viz`` makes all chart types
available (INV-010).

All renderers use matplotlib with the Agg backend — fully self-contained,
no browser or Chrome dependency.
"""

from __future__ import annotations

# All 14 chart type renderers (matplotlib)
from wichy.tools.viz.renderers.bar import render_bar  # noqa: F401
from wichy.tools.viz.renderers.distribution import render_distribution  # noqa: F401
from wichy.tools.viz.renderers.line import render_line  # noqa: F401
from wichy.tools.viz.renderers.scatter import render_scatter  # noqa: F401
from wichy.tools.viz.renderers.parallel_coords import (  # noqa: F401
    render_parallel_coords as render_parallel_coords,
)
from wichy.tools.viz.renderers.sankey import render_sankey  # noqa: F401
from wichy.tools.viz.renderers.treemap import render_treemap  # noqa: F401
from wichy.tools.viz.renderers.sunburst import render_sunburst  # noqa: F401
from wichy.tools.viz.renderers.radar import render_radar  # noqa: F401
from wichy.tools.viz.renderers.violin import render_violin  # noqa: F401
from wichy.tools.viz.renderers.heatmap import render_heatmap  # noqa: F401
from wichy.tools.viz.renderers.correlogram import render_correlogram  # noqa: F401
from wichy.tools.viz.renderers.chord import render_chord  # noqa: F401
from wichy.tools.viz.renderers.time_compass import render_time_compass  # noqa: F401
