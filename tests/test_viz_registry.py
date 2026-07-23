"""Tests for the chart type registry and config models."""

from __future__ import annotations

import pytest

from wichy.tools.viz.config_models import (
    BarChartConfig,
    BaseChartConfig,
    CHART_CONFIG_MODELS,
    LineChartConfig,
    ScatterChartConfig,
    validate_config,
)
from wichy.tools.viz.registry import (
    CHART_REGISTRY,
    FieldRole,
    get_chart_type,
    get_chart_types,
    register_chart_type,
)

# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    """Tests for the chart type registry."""

    def test_registry_is_dict(self) -> None:
        """CHART_REGISTRY is a dict."""
        assert isinstance(CHART_REGISTRY, dict)

    def test_register_and_lookup(self) -> None:
        """Registering a chart type makes it findable via get_chart_type."""

        # Use a unique id to avoid collision with real chart types
        test_id = "_test_register"

        # Clean up if a previous test run left this
        CHART_REGISTRY.pop(test_id, None)

        def fake_renderer(rows, config, output_path):
            pass

        register_chart_type(
            chart_id=test_id,
            label="Test Chart",
            category="test",
            icon="T",
            field_roles=[FieldRole(name="x", type="category")],
            config_model=BarChartConfig,
            renderer=fake_renderer,
        )

        defn = get_chart_type(test_id)
        assert defn is not None
        assert defn.id == test_id
        assert defn.label == "Test Chart"
        assert defn.category == "test"
        assert defn.icon == "T"
        assert len(defn.field_roles) == 1
        assert defn.field_roles[0].name == "x"
        assert defn.config_model is BarChartConfig
        assert defn.renderer is fake_renderer

        # Clean up
        CHART_REGISTRY.pop(test_id, None)

    def test_get_chart_type_not_found(self) -> None:
        """get_chart_type returns None for unknown ids."""
        assert get_chart_type("nonexistent_type") is None

    def test_get_chart_types_returns_list_of_dicts(self) -> None:
        """get_chart_types returns a list of dicts with expected keys."""
        types = get_chart_types()
        assert isinstance(types, list)
        if types:
            entry = types[0]
            assert "id" in entry
            assert "label" in entry
            assert "category" in entry
            assert "icon" in entry
            assert "field_roles" in entry

    def test_register_requires_explicit_args(self) -> None:
        """register_chart_type requires all args (no bare decorator use)."""
        # Just verify it's callable with the right signature
        import inspect

        sig = inspect.signature(register_chart_type)
        required = [
            p for p in sig.parameters.values() if p.default is inspect.Parameter.empty
        ]
        assert len(required) == 7  # all 7 params are required


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------


class TestBaseChartConfig:
    """Tests for the common BaseChartConfig."""

    def test_defaults(self) -> None:
        """BaseChartConfig has correct default values."""
        cfg = BaseChartConfig()
        assert cfg.title is None
        assert cfg.subtitle is None
        assert cfg.width == 1200
        assert cfg.height == 800
        assert cfg.dpi == 150
        assert cfg.theme == "light"
        assert cfg.font_size == 14
        assert cfg.background == "white"
        assert cfg.color_palette == []

    def test_extra_ignored(self) -> None:
        """Unknown fields are silently ignored (INV-003)."""
        cfg = BaseChartConfig(unknown_field="hello", title="Test")
        assert cfg.title == "Test"
        assert not hasattr(cfg, "unknown_field")


class TestBarChartConfig:
    """Tests for BarChartConfig."""

    def test_valid_config(self) -> None:
        """Valid bar config with required fields."""
        cfg = BarChartConfig(x="category_col", y="value_col")
        assert cfg.x == "category_col"
        assert cfg.y == "value_col"
        assert cfg.orientation == "v"
        assert cfg.mode == "grouped"

    def test_missing_required_field(self) -> None:
        """Missing required field raises validation error."""
        with pytest.raises(Exception):
            BarChartConfig(x="category_col")  # type: ignore[call-arg]

    def test_optional_fields(self) -> None:
        """Optional fields work correctly."""
        cfg = BarChartConfig(
            x="cat", y="val", color_by="group", orientation="h", mode="stacked"
        )
        assert cfg.color_by == "group"
        assert cfg.orientation == "h"
        assert cfg.mode == "stacked"


class TestScatterChartConfig:
    """Tests for ScatterChartConfig."""

    def test_valid_config(self) -> None:
        """Valid scatter config."""
        cfg = ScatterChartConfig(x="xcol", y="ycol")
        assert cfg.x == "xcol"
        assert cfg.y == "ycol"

    def test_with_optional_encoding(self) -> None:
        """Scatter with color and size encoding."""
        cfg = ScatterChartConfig(
            x="xcol", y="ycol", color_by="cat", size_by="magnitude"
        )
        assert cfg.color_by == "cat"
        assert cfg.size_by == "magnitude"


class TestLineChartConfig:
    """Tests for LineChartConfig (multi-series y)."""

    def test_multi_series_y(self) -> None:
        """Line config accepts multiple y columns."""
        cfg = LineChartConfig(x="date", y=["series_a", "series_b"])
        assert cfg.x == "date"
        assert cfg.y == ["series_a", "series_b"]


class TestValidateConfig:
    """Tests for the validate_config helper."""

    def test_valid_config(self) -> None:
        """validate_config returns (instance, None) for valid config."""
        instance, err = validate_config("bar", {"x": "cat", "y": "val"})
        assert err is None
        assert instance is not None
        assert instance.x == "cat"  # type: ignore[union-attr]

    def test_invalid_config(self) -> None:
        """validate_config returns (None, error_msg) for invalid config."""
        instance, err = validate_config("bar", {"x": "cat"})  # missing y
        assert instance is None
        assert err is not None

    def test_unknown_chart_type(self) -> None:
        """validate_config returns error for unknown chart type."""
        instance, err = validate_config("nonexistent", {})
        assert instance is None
        assert "nonexistent" in (err or "")


class TestChartConfigModels:
    """Verify all 14 chart types have config models registered."""

    EXPECTED_TYPES = [
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

    def test_all_types_have_models(self) -> None:
        """Every expected chart type has a config model."""
        for chart_id in self.EXPECTED_TYPES:
            assert (
                chart_id in CHART_CONFIG_MODELS
            ), f"Missing config model for {chart_id}"

    def test_all_models_inherit_base(self) -> None:
        """All config models inherit from BaseChartConfig."""
        for chart_id, model_cls in CHART_CONFIG_MODELS.items():
            assert issubclass(
                model_cls, BaseChartConfig
            ), f"{chart_id} config model does not inherit BaseChartConfig"
