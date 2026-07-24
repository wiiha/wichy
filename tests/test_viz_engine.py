"""Tests for the chart rendering engine and individual renderers.

Each test renders a chart with mock data and verifies that:
1. The PNG file is created at the expected path.
2. The PNG file is a valid image (can be opened by PIL).
3. The sidecar .meta.json file is created.

Tests use an isolated charts directory to avoid polluting the real one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from wichy.tools.viz.engine import (
    ChartConfigError,
    ChartNotFoundError,
    render_chart,
)


@pytest.fixture
def isolated_charts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated charts directory for each test."""
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    from wichy.config.settings import settings

    monkeypatch.setattr(type(settings), "charts_dir", property(lambda self: charts_dir))
    return charts_dir


def _assert_valid_png(path: Path) -> None:
    """Assert that a file exists at path and is a valid PNG image."""
    assert path.exists(), f"PNG file not found at {path}"
    assert path.suffix == ".png"
    img = Image.open(str(path))
    img.verify()  # Raises if invalid
    # Re-open for size check (verify() invalidates the image object)
    img = Image.open(str(path))
    assert img.size[0] > 0 and img.size[1] > 0, "Image has zero dimension"


def _assert_meta_exists(charts_dir: Path, png_path: Path) -> None:
    """Assert that a sidecar .meta.json exists for the chart."""
    meta_path = charts_dir / f"{png_path.stem}.meta.json"
    assert meta_path.exists(), f"Meta file not found at {meta_path}"


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------


def _bar_data() -> list[dict]:
    return [
        {"category": "A", "value": 10, "group": "X"},
        {"category": "B", "value": 20, "group": "X"},
        {"category": "C", "value": 15, "group": "Y"},
        {"category": "D", "value": 25, "group": "Y"},
    ]


def _distribution_data() -> list[dict]:
    import random

    random.seed(42)
    return [{"value": random.gauss(50, 15), "group": "A"} for _ in range(100)] + [
        {"value": random.gauss(70, 10), "group": "B"} for _ in range(100)
    ]


def _line_data() -> list[dict]:
    return [
        {"date": "2024-01-01", "series_a": 10, "series_b": 20},
        {"date": "2024-01-02", "series_a": 15, "series_b": 25},
        {"date": "2024-01-03", "series_a": 12, "series_b": 30},
        {"date": "2024-01-04", "series_a": 20, "series_b": 28},
        {"date": "2024-01-05", "series_a": 25, "series_b": 35},
    ]


def _scatter_data() -> list[dict]:
    import random

    random.seed(42)
    return [
        {"x": random.random() * 100, "y": random.random() * 100, "cat": i % 3}
        for i in range(50)
    ]


def _parallel_coords_data() -> list[dict]:
    import random

    random.seed(42)
    return [
        {
            "dim1": random.random() * 100,
            "dim2": random.random() * 100,
            "dim3": random.random() * 100,
            "dim4": random.random() * 100,
            "color": random.random() * 100,
        }
        for _ in range(30)
    ]


def _sankey_data() -> list[dict]:
    return [
        {"source": "A", "target": "X", "value": 10},
        {"source": "A", "target": "Y", "value": 5},
        {"source": "B", "target": "X", "value": 8},
        {"source": "B", "target": "Z", "value": 3},
        {"source": "X", "target": "Out", "value": 18},
        {"source": "Y", "target": "Out", "value": 5},
        {"source": "Z", "target": "Out", "value": 3},
    ]


def _treemap_data() -> list[dict]:
    return [
        {"label": "Root", "value": 0, "parent": ""},
        {"label": "A", "value": 30, "parent": "Root"},
        {"label": "B", "value": 20, "parent": "Root"},
        {"label": "A1", "value": 15, "parent": "A"},
        {"label": "A2", "value": 15, "parent": "A"},
        {"label": "B1", "value": 10, "parent": "B"},
        {"label": "B2", "value": 10, "parent": "B"},
    ]


def _sunburst_data() -> list[dict]:
    return [
        {"label": "Root", "value": 0, "parent": ""},
        {"label": "A", "value": 30, "parent": "Root"},
        {"label": "B", "value": 20, "parent": "Root"},
        {"label": "A1", "value": 15, "parent": "A"},
        {"label": "A2", "value": 15, "parent": "A"},
    ]


# ---------------------------------------------------------------------------
# Engine error tests
# ---------------------------------------------------------------------------


class TestEngineErrors:
    """Tests for engine error handling."""

    def test_unknown_chart_type(self, isolated_charts_dir: Path) -> None:
        """render_chart raises ChartNotFoundError for unknown chart type."""
        with pytest.raises(ChartNotFoundError):
            render_chart("nonexistent", [{"a": 1}], {}, table="test")

    def test_invalid_config(self, isolated_charts_dir: Path) -> None:
        """render_chart raises ChartConfigError for invalid config."""
        with pytest.raises(ChartConfigError):
            render_chart("bar", [{"a": 1}], {}, table="test")  # missing x, y


# ---------------------------------------------------------------------------
# Individual renderer tests
# ---------------------------------------------------------------------------


class TestBarRenderer:
    """Test bar chart rendering."""

    def test_renders_png(self, isolated_charts_dir: Path) -> None:
        """Bar chart renders a valid PNG with sidecar metadata."""
        path = render_chart(
            "bar", _bar_data(), {"x": "category", "y": "value"}, table="test_table"
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_renders_with_color_by(self, isolated_charts_dir: Path) -> None:
        """Bar chart with color_by renders a valid PNG."""
        path = render_chart(
            "bar",
            _bar_data(),
            {"x": "category", "y": "value", "color_by": "group"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_renders_with_title(self, isolated_charts_dir: Path) -> None:
        """Bar chart with title and subtitle renders correctly."""
        path = render_chart(
            "bar",
            _bar_data(),
            {
                "x": "category",
                "y": "value",
                "title": "My Bar Chart",
                "subtitle": "By Category",
                "x_axis_label": "Category",
                "y_axis_label": "Count",
            },
            table="test_table",
        )
        _assert_valid_png(path)


class TestDistributionRenderer:
    """Test distribution chart rendering."""

    def test_histogram(self, isolated_charts_dir: Path) -> None:
        """Histogram subtype renders a valid PNG."""
        path = render_chart(
            "distribution",
            _distribution_data(),
            {"value": "value", "subtype": "histogram", "bins": 20},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_box(self, isolated_charts_dir: Path) -> None:
        """Box plot subtype renders a valid PNG."""
        path = render_chart(
            "distribution",
            _distribution_data(),
            {"value": "value", "subtype": "box", "group_by": "group"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_kde(self, isolated_charts_dir: Path) -> None:
        """KDE subtype renders a valid PNG."""
        path = render_chart(
            "distribution",
            _distribution_data(),
            {"value": "value", "subtype": "kde"},
            table="test_table",
        )
        _assert_valid_png(path)


class TestLineRenderer:
    """Test line graph rendering."""

    def test_single_series(self, isolated_charts_dir: Path) -> None:
        """Single-series line graph renders a valid PNG."""
        path = render_chart(
            "line",
            _line_data(),
            {"x": "date", "y": ["series_a"]},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_multi_series(self, isolated_charts_dir: Path) -> None:
        """Multi-series line graph renders a valid PNG."""
        path = render_chart(
            "line",
            _line_data(),
            {"x": "date", "y": ["series_a", "series_b"]},
            table="test_table",
        )
        _assert_valid_png(path)


class TestScatterRenderer:
    """Test scatter plot rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Basic scatter plot renders a valid PNG."""
        path = render_chart(
            "scatter", _scatter_data(), {"x": "x", "y": "y"}, table="test_table"
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_with_color_and_size(self, isolated_charts_dir: Path) -> None:
        """Scatter with color_by and size_by renders correctly."""
        path = render_chart(
            "scatter",
            _scatter_data(),
            {"x": "x", "y": "y", "color_by": "cat", "size_by": "x"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_numeric_color_by_has_colorbar(self, isolated_charts_dir: Path) -> None:
        """Scatter with numeric color_by (e.g. Survived 0/1) renders a colorbar."""
        data = [
            {"x": 1, "y": 10, "survived": 0},
            {"x": 2, "y": 20, "survived": 1},
            {"x": 3, "y": 15, "survived": 0},
            {"x": 4, "y": 25, "survived": 1},
            {"x": 5, "y": 30, "survived": 1},
        ]
        path = render_chart(
            "scatter",
            data,
            {"x": "x", "y": "y", "color_by": "survived"},
            table="test_table",
        )
        _assert_valid_png(path)


class TestParallelCoordsRenderer:
    """Test parallel coordinates rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Parallel coordinates renders a valid PNG."""
        path = render_chart(
            "parallel_coords",
            _parallel_coords_data(),
            {
                "dimensions": ["dim1", "dim2", "dim3", "dim4"],
                "color_by": "color",
            },
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_with_categorical_dimension(self, isolated_charts_dir: Path) -> None:
        """Parallel coords handles categorical dimensions (sorted alphabetically)."""
        data = [
            {"price": 10000, "type": "SUV", "hp": 200, "fuel": "Petrol"},
            {"price": 20000, "type": "Sedan", "hp": 150, "fuel": "Diesel"},
            {"price": 30000, "type": "Sports", "hp": 350, "fuel": "Petrol"},
            {"price": 15000, "type": "Van", "hp": 180, "fuel": "Electric"},
            {"price": 25000, "type": "Truck", "hp": 300, "fuel": "Diesel"},
        ]
        path = render_chart(
            "parallel_coords",
            data,
            {
                "dimensions": ["price", "type", "hp", "fuel"],
            },
            table="cars",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)


class TestSankeyRenderer:
    """Test Sankey diagram rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Sankey diagram renders a valid PNG."""
        path = render_chart(
            "sankey",
            _sankey_data(),
            {"source": "source", "target": "target", "value": "value"},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)


class TestTreemapRenderer:
    """Test treemap rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Treemap renders a valid PNG."""
        path = render_chart(
            "treemap",
            _treemap_data(),
            {"labels": "label", "values": "value", "parent": "parent"},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_flat(self, isolated_charts_dir: Path) -> None:
        """Treemap renders without parent column (flat)."""
        data = [
            {"label": "X", "value": 40},
            {"label": "Y", "value": 30},
            {"label": "Z", "value": 20},
        ]
        path = render_chart(
            "treemap",
            data,
            {"labels": "label", "values": "value"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_aggregate_root_renders(self, isolated_charts_dir: Path) -> None:
        """Treemap with parent value=0 (aggregate root) still renders."""
        data = [
            {"label": "Root", "value": 0, "parent": ""},
            {"label": "A", "value": 50, "parent": "Root"},
            {"label": "B", "value": 50, "parent": "Root"},
        ]
        path = render_chart(
            "treemap",
            data,
            {"labels": "label", "values": "value", "parent": "parent"},
            table="test_table",
        )
        _assert_valid_png(path)
        # Verify the image has substantial content (not blank)
        img = Image.open(str(path))
        import numpy as np

        arr = np.array(img.convert("RGB"))
        # At least 15% non-white pixels indicates real rendering
        non_white = ~(
            (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
        )
        assert non_white.sum() / non_white.size > 0.15, "Treemap image appears blank"


class TestSunburstRenderer:
    """Test sunburst rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Sunburst renders a valid PNG."""
        path = render_chart(
            "sunburst",
            _sunburst_data(),
            {"labels": "label", "values": "value", "parent": "parent"},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_flat(self, isolated_charts_dir: Path) -> None:
        """Sunburst renders without parent column (flat single ring)."""
        data = [
            {"label": "Alpha", "value": 40},
            {"label": "Beta", "value": 30},
            {"label": "Gamma", "value": 20},
        ]
        path = render_chart(
            "sunburst",
            data,
            {"labels": "label", "values": "value"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_aggregate_root_renders(self, isolated_charts_dir: Path) -> None:
        """Sunburst with parent value=0 (aggregate root) still renders.

        This is a regression test: previously the sunburst was completely
        blank when root nodes had value=0 because child drawing was nested
        inside the root loop with zero angular width.
        """
        data = [
            {"label": "Root", "value": 0, "parent": ""},
            {"label": "A", "value": 50, "parent": "Root"},
            {"label": "B", "value": 50, "parent": "Root"},
        ]
        path = render_chart(
            "sunburst",
            data,
            {"labels": "label", "values": "value", "parent": "parent"},
            table="test_table",
        )
        _assert_valid_png(path)
        # Verify the image has substantial content (not blank)
        img = Image.open(str(path))
        import numpy as np

        arr = np.array(img.convert("RGB"))
        non_white = ~(
            (arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
        )
        assert non_white.sum() / non_white.size > 0.10, "Sunburst image appears blank"


class TestRegistryPopulated:
    """Verify that all expected chart types are registered after import."""

    EXPECTED_TYPES = [
        "bar",
        "distribution",
        "line",
        "scatter",
        "parallel_coords",
        "sankey",
        "treemap",
        "sunburst",
        "radar",
        "violin",
        "heatmap",
        "correlogram",
    ]

    def test_all_types_registered(self) -> None:
        """All 12 Plotly chart types are registered."""
        from wichy.tools.viz.registry import CHART_REGISTRY

        for chart_id in self.EXPECTED_TYPES:
            assert chart_id in CHART_REGISTRY, f"Chart type '{chart_id}' not registered"


# ---------------------------------------------------------------------------
# Stage 5 renderer tests: radar, violin, heatmap, correlogram
# ---------------------------------------------------------------------------


def _radar_data() -> list[dict]:
    return [
        {"name": "Alice", "speed": 80, "power": 90, "agility": 70, "endurance": 85},
        {"name": "Bob", "speed": 65, "power": 75, "agility": 85, "endurance": 60},
    ]


def _violin_data() -> list[dict]:
    import random

    random.seed(42)
    return [{"value": random.gauss(50, 10), "group": "A"} for _ in range(50)] + [
        {"value": random.gauss(70, 15), "group": "B"} for _ in range(50)
    ]


def _heatmap_data() -> list[dict]:
    return [
        {"x": "Mon", "y": "Morning", "value": 10},
        {"x": "Mon", "y": "Afternoon", "value": 20},
        {"x": "Tue", "y": "Morning", "value": 15},
        {"x": "Tue", "y": "Afternoon", "value": 25},
        {"x": "Wed", "y": "Morning", "value": 12},
        {"x": "Wed", "y": "Afternoon", "value": 22},
    ]


def _correlogram_data() -> list[dict]:
    import random

    random.seed(42)
    data = []
    for _ in range(50):
        x = random.gauss(0, 1)
        data.append(
            {
                "a": x,
                "b": x * 0.8 + random.gauss(0, 0.3),
                "c": -x * 0.5 + random.gauss(0, 0.5),
                "d": random.gauss(0, 1),
            }
        )
    return data


class TestRadarRenderer:
    """Test radar/spider chart rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Radar chart renders a valid PNG."""
        path = render_chart(
            "radar",
            _radar_data(),
            {
                "categories": ["Speed", "Power", "Agility", "Endurance"],
                "values": ["speed", "power", "agility", "endurance"],
                "name_column": "name",
            },
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_group_by(self, isolated_charts_dir: Path) -> None:
        """Radar chart with group_by averages values per group."""
        data = [
            {"team": "X", "speed": 90, "power": 60, "agility": 80},
            {"team": "X", "speed": 70, "power": 80, "agility": 60},
            {"team": "Y", "speed": 85, "power": 75, "agility": 70},
        ]
        path = render_chart(
            "radar",
            data,
            {
                "categories": ["Speed", "Power", "Agility"],
                "values": ["speed", "power", "agility"],
                "group_by": "team",
            },
            table="test_table",
        )
        _assert_valid_png(path)

    def test_no_name_column(self, isolated_charts_dir: Path) -> None:
        """Radar chart without name_column auto-generates 'Series N' labels."""
        data = [
            {"a": 80, "b": 70, "c": 90},
            {"a": 65, "b": 85, "c": 75},
        ]
        path = render_chart(
            "radar",
            data,
            {
                "categories": ["Quality", "Speed", "Cost"],
                "values": ["a", "b", "c"],
            },
            table="test_table",
        )
        _assert_valid_png(path)

    def test_short_categories_fallback(self, isolated_charts_dir: Path) -> None:
        """Radar chart with fewer categories than values uses column names as fallback."""
        data = [
            {"a": 80, "b": 70, "c": 90, "d": 60},
        ]
        path = render_chart(
            "radar",
            data,
            {
                "categories": ["Quality", "Speed"],
                "values": ["a", "b", "c", "d"],
            },
            table="test_table",
        )
        _assert_valid_png(path)


class TestViolinRenderer:
    """Test violin plot rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Violin plot renders a valid PNG."""
        path = render_chart(
            "violin",
            _violin_data(),
            {"value": "value", "group_by": "group", "box_overlay": True},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)


class TestHeatmapRenderer:
    """Test heatmap rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Heatmap renders a valid PNG."""
        path = render_chart(
            "heatmap",
            _heatmap_data(),
            {"x": "x", "y": "y", "value": "value"},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)


class TestCorrelogramRenderer:
    """Test correlogram rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Correlogram renders a valid PNG."""
        path = render_chart(
            "correlogram",
            _correlogram_data(),
            {"columns": ["a", "b", "c", "d"]},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_with_null_values(self, isolated_charts_dir: Path) -> None:
        """Correlogram with null values in a column computes pairwise correlations."""
        data = [
            {"a": 1, "b": 10, "c": 100},
            {"a": 2, "b": 20, "c": None},
            {"a": 3, "b": None, "c": 300},
            {"a": 4, "b": 40, "c": 400},
            {"a": 5, "b": 50, "c": 500},
        ]
        path = render_chart(
            "correlogram", data, {"columns": ["a", "b", "c"]}, table="test_table"
        )
        _assert_valid_png(path)


# ---------------------------------------------------------------------------
# Stage 6 renderer tests: chord, time_compass
# ---------------------------------------------------------------------------


def _chord_data() -> list[dict]:
    return [
        {"source": "A", "target": "B", "value": 10},
        {"source": "A", "target": "C", "value": 5},
        {"source": "B", "target": "C", "value": 8},
        {"source": "B", "target": "A", "value": 3},
        {"source": "C", "target": "A", "value": 7},
        {"source": "C", "target": "B", "value": 2},
    ]


def _time_compass_data() -> list[dict]:
    return [
        {"time": "2024-01-15", "value": 10, "group": "A"},
        {"time": "2024-03-15", "value": 25, "group": "A"},
        {"time": "2024-06-15", "value": 40, "group": "A"},
        {"time": "2024-09-15", "value": 20, "group": "A"},
        {"time": "2024-01-15", "value": -5, "group": "B"},
        {"time": "2024-03-15", "value": -15, "group": "B"},
        {"time": "2024-06-15", "value": -30, "group": "B"},
        {"time": "2024-09-15", "value": -10, "group": "B"},
    ]


class TestChordRenderer:
    """Test chord diagram rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Chord diagram renders a valid PNG."""
        path = render_chart(
            "chord",
            _chord_data(),
            {"source": "source", "target": "target", "value": "value"},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)


class TestTimeCompassRenderer:
    """Test time compass rendering."""

    def test_basic(self, isolated_charts_dir: Path) -> None:
        """Time compass renders a valid PNG."""
        path = render_chart(
            "time_compass",
            _time_compass_data(),
            {"time": "time", "value": "value", "group_by": "group"},
            table="test_table",
        )
        _assert_valid_png(path)
        _assert_meta_exists(isolated_charts_dir, path)

    def test_explicit_periods(self, isolated_charts_dir: Path) -> None:
        """Time compass with explicit period labels (not hardcoded months)."""
        data = [
            {"day": "Mon", "value": 30},
            {"day": "Wed", "value": 50},
            {"day": "Fri", "value": 40},
        ]
        path = render_chart(
            "time_compass",
            data,
            {
                "time": "day",
                "value": "value",
                "periods": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            },
            table="test_table",
        )
        _assert_valid_png(path)

    def test_non_date_periods(self, isolated_charts_dir: Path) -> None:
        """Time compass with non-date category periods (auto-detect)."""
        data = [
            {"season": "Spring", "value": 30},
            {"season": "Summer", "value": 50},
            {"season": "Fall", "value": 40},
            {"season": "Winter", "value": 20},
        ]
        path = render_chart(
            "time_compass",
            data,
            {"time": "season", "value": "value"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_period_column(self, isolated_charts_dir: Path) -> None:
        """Time compass with period_column for mapping."""
        data = [
            {"date": "2024-01-15", "month_name": "Jan", "value": 10},
            {"date": "2024-02-20", "month_name": "Feb", "value": 25},
            {"date": "2024-03-10", "month_name": "Mar", "value": 40},
        ]
        path = render_chart(
            "time_compass",
            data,
            {"time": "date", "value": "value", "period_column": "month_name"},
            table="test_table",
        )
        _assert_valid_png(path)


class TestAllChartTypesRegistered:
    """Verify ALL 14 chart types are registered after import."""

    ALL_TYPES = [
        "bar",
        "distribution",
        "line",
        "scatter",
        "chord",
        "parallel_coords",
        "time_compass",
        "sankey",
        "treemap",
        "sunburst",
        "radar",
        "violin",
        "heatmap",
        "correlogram",
    ]

    def test_all_14_registered(self) -> None:
        """All 14 chart types are registered."""
        from wichy.tools.viz.registry import CHART_REGISTRY

        for chart_id in self.ALL_TYPES:
            assert chart_id in CHART_REGISTRY, f"Chart type '{chart_id}' not registered"
        assert len(CHART_REGISTRY) >= 14


class TestNullHandling:
    """Tests for null/None value handling across chart renderers.

    Real-world datasets (e.g. Titanic) contain nulls.  Every chart type must
    handle them gracefully — either filtering them, substituting 0, or
    drawing gaps — rather than crashing.
    """

    def test_bar_with_null_y(self, isolated_charts_dir: Path) -> None:
        """Bar chart with None y-values renders without crashing."""
        data = [
            {"category": "A", "value": 10},
            {"category": "B", "value": None},
            {"category": "C", "value": 15},
            {"category": "D", "value": None},
        ]
        path = render_chart(
            "bar", data, {"x": "category", "y": "value"}, table="test_table"
        )
        _assert_valid_png(path)

    def test_bar_with_null_y_color_by(self, isolated_charts_dir: Path) -> None:
        """Bar chart with color_by and None y-values renders without crashing."""
        data = [
            {"category": "A", "value": 10, "group": "X"},
            {"category": "B", "value": None, "group": "X"},
            {"category": "C", "value": 15, "group": "Y"},
            {"category": "D", "value": None, "group": "Y"},
        ]
        path = render_chart(
            "bar",
            data,
            {"x": "category", "y": "value", "color_by": "group"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_bar_horizontal_with_null_y(self, isolated_charts_dir: Path) -> None:
        """Horizontal bar chart with None y-values renders without crashing."""
        data = [
            {"category": "A", "value": 10},
            {"category": "B", "value": None},
            {"category": "C", "value": 15},
        ]
        path = render_chart(
            "bar",
            data,
            {"x": "category", "y": "value", "orientation": "h"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_line_with_null_y(self, isolated_charts_dir: Path) -> None:
        """Line graph with None y-values draws gaps instead of crashing."""
        data = [
            {"date": "2024-01-01", "series_a": 10, "series_b": 20},
            {"date": "2024-01-02", "series_a": None, "series_b": 25},
            {"date": "2024-01-03", "series_a": 12, "series_b": None},
            {"date": "2024-01-04", "series_a": 20, "series_b": 28},
            {"date": "2024-01-05", "series_a": 25, "series_b": 35},
        ]
        path = render_chart(
            "line", data, {"x": "date", "y": ["series_a"]}, table="test_table"
        )
        _assert_valid_png(path)

    def test_line_with_null_y_color_by(self, isolated_charts_dir: Path) -> None:
        """Line graph with color_by and None y-values draws gaps instead of crashing."""
        data = [
            {"date": "2024-01-01", "series_a": 10, "group": "X"},
            {"date": "2024-01-02", "series_a": None, "group": "X"},
            {"date": "2024-01-03", "series_a": 12, "group": "X"},
            {"date": "2024-01-01", "series_a": 20, "group": "Y"},
            {"date": "2024-01-02", "series_a": None, "group": "Y"},
            {"date": "2024-01-03", "series_a": 28, "group": "Y"},
        ]
        path = render_chart(
            "line",
            data,
            {"x": "date", "y": ["series_a"], "color_by": "group"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_scatter_with_null_y(self, isolated_charts_dir: Path) -> None:
        """Scatter plot with None y-values filters them out and renders."""
        data = [
            {"x": 1, "y": 10},
            {"x": 2, "y": None},
            {"x": 3, "y": 30},
            {"x": 4, "y": None},
            {"x": 5, "y": 50},
        ]
        path = render_chart("scatter", data, {"x": "x", "y": "y"}, table="test_table")
        _assert_valid_png(path)

    def test_scatter_with_null_x(self, isolated_charts_dir: Path) -> None:
        """Scatter plot with None x-values filters them out and renders."""
        data = [
            {"x": 1, "y": 10},
            {"x": None, "y": 20},
            {"x": 3, "y": 30},
        ]
        path = render_chart("scatter", data, {"x": "x", "y": "y"}, table="test_table")
        _assert_valid_png(path)

    def test_scatter_with_nulls_color_and_size(self, isolated_charts_dir: Path) -> None:
        """Scatter with color_by/size_by and null coords filters correctly."""
        data = [
            {"x": 1, "y": 10, "cat": "A", "size": 5},
            {"x": 2, "y": None, "cat": "A", "size": 3},
            {"x": 3, "y": 30, "cat": "B", "size": 8},
            {"x": None, "y": 40, "cat": "B", "size": 6},
            {"x": 5, "y": 50, "cat": "A", "size": 2},
        ]
        path = render_chart(
            "scatter",
            data,
            {"x": "x", "y": "y", "color_by": "cat", "size_by": "size"},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_scatter_all_null(self, isolated_charts_dir: Path) -> None:
        """Scatter plot where all y-values are None renders 'No data'."""
        data = [
            {"x": 1, "y": None},
            {"x": 2, "y": None},
        ]
        path = render_chart("scatter", data, {"x": "x", "y": "y"}, table="test_table")
        _assert_valid_png(path)

    def test_line_with_null_x(self, isolated_charts_dir: Path) -> None:
        """Line graph with None x-values draws gaps instead of crashing."""
        data = [
            {"date": "2024-01-01", "val": 10},
            {"date": None, "val": 20},
            {"date": "2024-01-03", "val": 30},
        ]
        path = render_chart(
            "line", data, {"x": "date", "y": ["val"]}, table="test_table"
        )
        _assert_valid_png(path)

    def test_line_with_null_x_numeric(self, isolated_charts_dir: Path) -> None:
        """Line graph with None numeric x-values draws gaps instead of crashing."""
        data = [
            {"x": 1, "val": 10},
            {"x": None, "val": 20},
            {"x": 3, "val": 30},
        ]
        path = render_chart("line", data, {"x": "x", "y": ["val"]}, table="test_table")
        _assert_valid_png(path)

    def test_parallel_coords_all_none_dimension(
        self, isolated_charts_dir: Path
    ) -> None:
        """Parallel coords with a dimension where all values are None."""
        data = [
            {"a": 1, "b": None, "c": 10},
            {"a": 2, "b": None, "c": 20},
            {"a": 3, "b": None, "c": 30},
        ]
        path = render_chart(
            "parallel_coords",
            data,
            {"dimensions": ["a", "b", "c"]},
            table="test_table",
        )
        _assert_valid_png(path)

    def test_radar_with_null_values(self, isolated_charts_dir: Path) -> None:
        """Radar chart with None in a values column."""
        data = [
            {"name": "A", "v1": 10, "v2": None},
            {"name": "B", "v1": 20, "v2": 30},
        ]
        path = render_chart(
            "radar",
            data,
            {
                "categories": ["Metric 1", "Metric 2"],
                "values": ["v1", "v2"],
                "name_column": "name",
            },
            table="test_table",
        )
        _assert_valid_png(path)

    def test_scatter_numeric_color_by_with_null(
        self, isolated_charts_dir: Path
    ) -> None:
        """Scatter with numeric color_by containing None renders without crash."""
        data = [
            {"x": 1, "y": 10, "survived": 0},
            {"x": 2, "y": 20, "survived": None},
            {"x": 3, "y": 30, "survived": 1},
            {"x": 4, "y": 40, "survived": 1},
        ]
        path = render_chart(
            "scatter",
            data,
            {"x": "x", "y": "y", "color_by": "survived"},
            table="test_table",
        )
        _assert_valid_png(path)
