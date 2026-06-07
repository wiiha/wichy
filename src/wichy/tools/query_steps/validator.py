"""Column and operator validation for query step recipes."""

from typing import Callable

FILTER_OPS = {
    "==",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "contains",
    "starts_with",
    "is_null",
    "is_not_null",
}
AGG_FUNCS = {"count", "sum", "avg", "min", "max", "count_distinct"}
SORT_ORDERS = {"asc", "desc"}
JOIN_TYPES = {"inner", "left", "right", "cross"}
MAX_STEPS = 20
MAX_LIMIT = 10000


class ValidationError(ValueError):
    """Raised when a recipe fails validation."""

    pass


def validate_recipe(steps: list[dict], get_columns: Callable[[str], set[str]]) -> None:
    """Validate a recipe against the database schema.

    Args:
        steps: List of step dicts.
        get_columns: Callable that takes a table name and returns a set of valid column names.

    Raises:
        ValidationError: if any step is invalid.
    """
    if not steps:
        raise ValidationError("Recipe must have at least one step")
    if steps[0].get("type") != "source":
        raise ValidationError("First step must be 'source'")

    available_cols: set[str] = set()
    current_table = steps[0].get("table", "")
    if current_table:
        available_cols = get_columns(current_table)

    for i, step in enumerate(steps):
        stype = step.get("type", "")
        _validate_step(stype, step, available_cols, i)
        if stype == "group":
            dims = step.get("dimensions", [])
            aggs = step.get("aggregates", [])
            available_cols = set(dims)
            for a in aggs:
                fn = a.get("function", "")
                col = a.get("column", "")
                if fn == "count":
                    available_cols.add(f"{col}_count")
                else:
                    available_cols.add(f"{col}_{fn}")
        elif stype == "join":
            other_table = step.get("table", "")
            if other_table:
                other_cols = get_columns(other_table)
                available_cols = available_cols | other_cols


def _validate_step(stype: str, step: dict, available_cols: set[str], idx: int) -> None:
    prefix = f"Step {idx + 1} ({stype}): "
    if stype == "source":
        if not step.get("table"):
            raise ValidationError(f"{prefix}Missing 'table'")
    elif stype == "filter":
        col = step.get("column", "")
        op = step.get("operator", "")
        if not col:
            raise ValidationError(f"{prefix}Missing 'column'")
        if op not in FILTER_OPS:
            raise ValidationError(f"{prefix}Invalid operator '{op}'")
        if available_cols and col not in available_cols:
            raise ValidationError(f"{prefix}Unknown column '{col}'")
        if op not in ("is_null", "is_not_null") and "value" not in step:
            raise ValidationError(f"{prefix}Missing 'value' for operator '{op}'")
    elif stype == "sort":
        cols = step.get("columns", [])
        if not cols:
            raise ValidationError(f"{prefix}Missing 'columns'")
        for c in cols:
            col_name = c.get("column", "")
            order = c.get("order", "asc")
            if not col_name:
                raise ValidationError(f"{prefix}Missing sort column name")
            if order.lower() not in SORT_ORDERS:
                raise ValidationError(f"{prefix}Invalid sort order '{order}'")
            if available_cols and col_name not in available_cols:
                raise ValidationError(f"{prefix}Unknown column '{col_name}'")
    elif stype == "group":
        dims = step.get("dimensions", [])
        aggs = step.get("aggregates", [])
        if not dims and not aggs:
            raise ValidationError(f"{prefix}Requires 'dimensions' or 'aggregates'")
        for dim in dims:
            if available_cols and dim not in available_cols:
                raise ValidationError(f"{prefix}Unknown dimension '{dim}'")
        for a in aggs:
            fn = a.get("function", "")
            col = a.get("column", "")
            if fn not in AGG_FUNCS:
                raise ValidationError(f"{prefix}Invalid aggregate function '{fn}'")
            if available_cols and col not in available_cols:
                raise ValidationError(f"{prefix}Unknown aggregate column '{col}'")
    elif stype == "limit":
        n = step.get("n", 100)
        if not isinstance(n, int) or n < 1 or n > MAX_LIMIT:
            raise ValidationError(f"{prefix}Limit must be 1-{MAX_LIMIT}")
    elif stype == "custom_sql":
        sql = step.get("sql", "")
        if not sql:
            raise ValidationError(f"{prefix}Missing 'sql'")
        if "{{previous}}" not in sql:
            raise ValidationError(f"{prefix}Custom SQL must contain {{previous}}")
    elif stype == "join":
        table = step.get("table", "")
        left_col = step.get("left_column", "")
        right_col = step.get("right_column", "")
        how = step.get("how", "inner")
        if not table:
            raise ValidationError(f"{prefix}Missing 'table'")
        if not left_col or not right_col:
            raise ValidationError(f"{prefix}Missing join columns")
        if how not in JOIN_TYPES:
            raise ValidationError(f"{prefix}Invalid join type '{how}'")
        if available_cols and left_col not in available_cols:
            raise ValidationError(f"{prefix}Unknown left column '{left_col}'")
    else:
        raise ValidationError(f"{prefix}Unknown step type '{stype}'")
