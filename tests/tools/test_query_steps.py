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
            validate_recipe([
                {"type": "source", "table": "test"},
                {"type": "filter", "column": "col1", "operator": "invalid"},
            ], _fake_get_columns)

    def test_filter_unknown_column_strict(self):
        with pytest.raises(ValidationError):
            validate_recipe([
                {"type": "source", "table": "test"},
                {"type": "filter", "column": "nope", "operator": "==", "value": "x"},
            ], _fake_get_columns)

    def test_custom_sql_permissive_filter(self):
        # After custom_sql, unknown columns should NOT raise
        validate_recipe([
            {"type": "source", "table": "test"},
            {"type": "custom_sql", "sql": "SELECT col1, col2 + 1 AS new_col FROM {{previous}}"},
            {"type": "filter", "column": "new_col", "operator": "==", "value": "x"},
        ], _fake_get_columns)

    def test_custom_sql_permissive_even_for_misspellings(self):
        # After custom_sql, strict_validation is disabled entirely,
        # so misspellings of formerly-known columns are also allowed.
        validate_recipe([
            {"type": "source", "table": "test"},
            {"type": "custom_sql", "sql": "SELECT * FROM {{previous}}"},
            {"type": "filter", "column": "col111", "operator": "==", "value": "x"},
        ], _fake_get_columns)






class TestCompiler:
    def test_basic_compile(self):
        sql, params = compile_recipe([
            {"type": "source", "table": "test"},
            {"type": "limit", "n": 10},
        ])
        assert "WITH" in sql
        assert "SELECT * FROM step_1" in sql

    def test_unknown_step_type(self):
        with pytest.raises(CompileError):
            compile_recipe([
                {"type": "source", "table": "test"},
                {"type": "invalid_step"},
            ])
