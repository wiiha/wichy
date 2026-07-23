"""Recipe-to-SQL CTE compiler for the Query Steps feature."""

import re
from typing import Any

from .constants import AGG_FUNCS, FILTER_OPS, MAX_STEPS


class CompileError(ValueError):
    """Raised when recipe steps cannot be compiled to SQL."""

    pass


def _escape_id(value: str | None) -> str:
    """Escape a SQL identifier by doubling literal double quotes."""
    if value is None:
        return ""
    return str(value).replace('"', '""')


def compile_recipe(steps: list[dict]) -> tuple[str, list[Any]]:
    """Compile recipe steps into a CTE SQL string and parameters.

    Args:
        steps: List of step dicts. First step must be type "source".

    Returns:
        (sql_string, params) where sql_string is the full CTE query
        and params is a list of bound parameter values.

    Raises:
        CompileError: if steps are invalid or empty.
    """
    if not steps:
        raise CompileError("Recipe must have at least one step")
    if len(steps) > MAX_STEPS:
        raise CompileError(f"Max {MAX_STEPS} steps allowed")
    if steps[0].get("type") != "source":
        raise CompileError("First step must be 'source'")

    params: list[Any] = []
    ctes: list[str] = []

    # Step 0: source
    src_step = steps[0]
    table = src_step.get("table", "")
    if not table:
        raise CompileError("Source step requires 'table'")
    ctes.append(f'step_0 AS (SELECT * FROM "{_escape_id(table)}")')

    # Subsequent steps
    for i, step in enumerate(steps[1:], start=1):
        prev = f"step_{i - 1}"
        stype = step.get("type")

        if stype == "filter":
            sql, p = _compile_filter(step, prev)
        elif stype == "sort":
            sql = _compile_sort(step, prev)
            p = []
        elif stype == "group":
            sql = _compile_group(step, prev)
            p = []
        elif stype == "limit":
            sql = _compile_limit(step, prev)
            p = []
        elif stype == "custom_sql":
            sql, p = _compile_custom_sql(step, prev)
        elif stype == "join":
            sql, p = _compile_join(step, prev)
        elif stype == "visualize":
            sql = _compile_visualize(step, prev)
            p = []
        else:
            raise CompileError(f"Unknown step type: {stype}")
        ctes.append(f"step_{i} AS ({sql})")
        params.extend(p)

    cte_block = ",\n".join(ctes)
    final = f"WITH {cte_block}\nSELECT * FROM step_{len(steps) - 1}"
    return final, params


def _compile_filter(step: dict, prev: str) -> tuple[str, list]:
    col = step.get("column", "")
    op = step.get("operator", "")
    val = step.get("value")

    if op not in FILTER_OPS:
        raise CompileError(f"Invalid filter operator: {op}")
    if not col:
        raise CompileError("Filter step requires 'column'")

    safe_col = _escape_id(col)

    if op in ("is_null", "is_not_null"):
        null_op = "IS NULL" if op == "is_null" else "IS NOT NULL"
        return f'SELECT * FROM {prev} WHERE "{safe_col}" {null_op}', []
    elif op == "contains":
        return f'SELECT * FROM {prev} WHERE "{safe_col}" LIKE ?', [f"%{val}%"]
    elif op == "starts_with":
        return f'SELECT * FROM {prev} WHERE "{safe_col}" LIKE ?', [f"{val}%"]
    else:
        return f'SELECT * FROM {prev} WHERE "{safe_col}" {op} ?', [val]


def _compile_sort(step: dict, prev: str) -> str:
    cols = step.get("columns", [])
    if not cols:
        raise CompileError("Sort step requires 'columns'")
    parts = []
    for c in cols:
        name = c.get("column", "")
        order = c.get("order", "asc").upper()
        if order not in ("ASC", "DESC"):
            raise CompileError(f"Invalid sort order: {order}")
        parts.append(f'"{_escape_id(name)}" {order}')
    order_by = ", ".join(parts)
    return f"SELECT * FROM {prev} ORDER BY {order_by}"


def _compile_group(step: dict, prev: str) -> str:
    dims = step.get("dimensions", [])
    aggs = step.get("aggregates", [])
    if not dims and not aggs:
        raise CompileError("Group step requires 'dimensions' or 'aggregates'")

    dim_str = ", ".join(f'"{_escape_id(d)}"' for d in dims) if dims else ""
    agg_parts = []
    for a in aggs:
        fn = a.get("function", "")
        col = a.get("column", "")
        if fn not in AGG_FUNCS:
            raise CompileError(f"Invalid aggregate function: {fn}")
        safe_col = _escape_id(col)
        if fn == "count_distinct":
            agg_parts.append(
                f'COUNT(DISTINCT "{safe_col}") AS "{safe_col}_count_distinct"'
            )
        elif fn == "count":
            agg_parts.append(f'COUNT(*) AS "{safe_col}_count"')
        else:
            agg_parts.append(f'{fn.upper()}("{safe_col}") AS "{safe_col}_{fn}"')

    select_parts = []
    if dim_str:
        select_parts.append(dim_str)
    select_parts.extend(agg_parts)
    select_clause = ", ".join(select_parts)
    group_clause = ", ".join(f'"{_escape_id(d)}"' for d in dims) if dims else ""
    if group_clause:
        return f"SELECT {select_clause} FROM {prev} GROUP BY {group_clause}"
    return f"SELECT {select_clause} FROM {prev}"


def _compile_limit(step: dict, prev: str) -> str:
    n = step.get("n", 100)
    if not isinstance(n, int) or n < 1 or n > 10000:
        raise CompileError("Limit must be an integer between 1 and 10000")
    return f"SELECT * FROM {prev} LIMIT {n}"


def _compile_custom_sql(step: dict, prev: str) -> tuple[str, list]:
    sql = step.get("sql", "")
    if not sql:
        raise CompileError("Custom SQL step requires 'sql'")
    if "{{previous}}" not in sql:
        raise CompileError("Custom SQL must contain {{previous}} placeholder")
    replaced = sql.replace("{{previous}}", prev).strip().rstrip(";")
    lowered = replaced.lower()
    for forbidden in ("insert", "update", "delete", "drop", "create", "alter"):
        if re.search(rf"\b{forbidden}\b", lowered):
            raise CompileError(f"Custom SQL contains forbidden keyword: {forbidden}")
    return replaced, []


def _compile_join(step: dict, prev: str) -> tuple[str, list]:
    table = step.get("table", "")
    left_col = step.get("left_column", "")
    right_col = step.get("right_column", "")
    how = step.get("how", "inner").upper()
    if how not in ("INNER", "LEFT", "RIGHT", "CROSS"):
        raise CompileError(f"Invalid join type: {how}")
    if not table or not left_col or not right_col:
        raise CompileError("Join step requires 'table', 'left_column', 'right_column'")
    return (
        f'SELECT * FROM {prev} {how} JOIN "{_escape_id(table)}" ON {prev}."{_escape_id(left_col)}" = "{_escape_id(table)}"."{_escape_id(right_col)}"',
        [],
    )


def _compile_visualize(step: dict, prev: str) -> str:
    """Compile a visualize step as a passthrough (SELECT * FROM prev).

    The visualize step is terminal-only (INV-005). The API layer detects it,
    compiles the preceding steps, executes the SQL, then renders a chart from
    the result instead of returning rows. The compiler just passes the data
    through unchanged.

    Args:
        step: Step dict (must have 'chart_type' and 'config').
        prev: Previous CTE name.

    Returns:
        Passthrough SQL: ``SELECT * FROM {prev}``
    """
    if not step.get("chart_type"):
        raise CompileError("Visualize step requires 'chart_type'")
    if "config" not in step:
        raise CompileError("Visualize step requires 'config'")
    return f"SELECT * FROM {prev}"
