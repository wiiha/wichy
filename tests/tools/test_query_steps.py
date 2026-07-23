"""Tests for query steps validator and compiler."""

import pytest
from wichy.tools.query_steps.validator import validate_recipe, ValidationError
from wichy.tools.query_steps.compiler import compile_recipe, CompileError


def _fake_get_columns(table: str) -> set[str]:
    """Fake get_columns for testing."""
    return {"col1", "col2", "Order Date", "amount"}


class TestValidator:
    def test_basic_source_only(self):
        validate_recipe([{"type": "source", "table": "test"}], _fake_get_columns)

    def test_filter_invalid_operator(self):
        with pytest.raises(ValidationError):
            validate_recipe(
                [
                    {"type": "source", "table": "test"},
                    {"type": "filter", "column": "col1", "operator": "invalid"},
                ],
                _fake_get_columns,
            )

    def test_filter_unknown_column_strict(self):
        with pytest.raises(ValidationError):
            validate_recipe(
                [
                    {"type": "source", "table": "test"},
                    {
                        "type": "filter",
                        "column": "nope",
                        "operator": "==",
                        "value": "x",
                    },
                ],
                _fake_get_columns,
            )

    def test_custom_sql_permissive_filter(self):
        # After custom_sql, unknown columns should NOT raise
        validate_recipe(
            [
                {"type": "source", "table": "test"},
                {
                    "type": "custom_sql",
                    "sql": "SELECT col1, col2 + 1 AS new_col FROM {{previous}}",
                },
                {"type": "filter", "column": "new_col", "operator": "==", "value": "x"},
            ],
            _fake_get_columns,
        )

    def test_custom_sql_permissive_even_for_misspellings(self):
        # After custom_sql, strict_validation is disabled entirely,
        # so misspellings of formerly-known columns are also allowed.
        validate_recipe(
            [
                {"type": "source", "table": "test"},
                {"type": "custom_sql", "sql": "SELECT * FROM {{previous}}"},
                {"type": "filter", "column": "col111", "operator": "==", "value": "x"},
            ],
            _fake_get_columns,
        )


class TestCompiler:
    def test_basic_compile(self):
        sql, params = compile_recipe(
            [
                {"type": "source", "table": "test"},
                {"type": "limit", "n": 10},
            ]
        )
        assert "WITH" in sql
        assert "SELECT * FROM step_1" in sql

    def test_unknown_step_type(self):
        with pytest.raises(CompileError):
            compile_recipe(
                [
                    {"type": "source", "table": "test"},
                    {"type": "invalid_step"},
                ]
            )

    def test_visualize_as_terminal_step_compiles(self):
        """Visualize step compiles as passthrough when it's the last step."""
        sql, params = compile_recipe(
            [
                {"type": "source", "table": "test"},
                {"type": "limit", "n": 10},
                {
                    "type": "visualize",
                    "chart_type": "bar",
                    "config": {"x": "col1", "y": "col2"},
                },
            ]
        )
        assert "WITH" in sql
        assert "SELECT * FROM step_2" in sql
        assert params == []

    def test_visualize_missing_chart_type(self):
        """Visualize step without chart_type raises CompileError."""
        with pytest.raises(CompileError, match="chart_type"):
            compile_recipe(
                [
                    {"type": "source", "table": "test"},
                    {"type": "visualize", "config": {}},
                ]
            )

    def test_visualize_missing_config(self):
        """Visualize step without config raises CompileError."""
        with pytest.raises(CompileError, match="config"):
            compile_recipe(
                [
                    {"type": "source", "table": "test"},
                    {"type": "visualize", "chart_type": "bar"},
                ]
            )


class TestValidatorVisualize:
    """Tests for the visualize step validation."""

    def test_visualize_as_last_step_valid(self):
        """Visualize as the last step passes validation."""
        validate_recipe(
            [
                {"type": "source", "table": "test"},
                {
                    "type": "visualize",
                    "chart_type": "bar",
                    "config": {"x": "col1", "y": "col2"},
                },
            ],
            _fake_get_columns,
        )

    def test_visualize_not_last_step_raises(self):
        """Visualize not as the last step raises ValidationError (INV-005)."""
        with pytest.raises(ValidationError, match="last step"):
            validate_recipe(
                [
                    {"type": "source", "table": "test"},
                    {
                        "type": "visualize",
                        "chart_type": "bar",
                        "config": {},
                    },
                    {"type": "limit", "n": 10},
                ],
                _fake_get_columns,
            )

    def test_visualize_missing_chart_type_raises(self):
        """Visualize without chart_type raises ValidationError."""
        with pytest.raises(ValidationError, match="chart_type"):
            validate_recipe(
                [
                    {"type": "source", "table": "test"},
                    {"type": "visualize", "config": {}},
                ],
                _fake_get_columns,
            )

    def test_visualize_missing_config_raises(self):
        """Visualize without config raises ValidationError."""
        with pytest.raises(ValidationError, match="config"):
            validate_recipe(
                [
                    {"type": "source", "table": "test"},
                    {"type": "visualize", "chart_type": "bar"},
                ],
                _fake_get_columns,
            )

    def test_visualize_after_group_valid(self):
        """Visualize after a group step is valid (common use case)."""
        validate_recipe(
            [
                {"type": "source", "table": "test"},
                {
                    "type": "group",
                    "dimensions": ["col1"],
                    "aggregates": [{"function": "sum", "column": "amount"}],
                },
                {
                    "type": "visualize",
                    "chart_type": "bar",
                    "config": {"x": "col1", "y": "amount_sum"},
                },
            ],
            _fake_get_columns,
        )
