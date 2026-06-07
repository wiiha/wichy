"""API endpoints for data explorer."""

import math

from flask import Blueprint, jsonify

from wichy.tools.duckdb_manager import DuckDBManager


def _is_numeric_type(dtype: str) -> bool:
    """Check if a data type is numeric."""
    dtype_upper = dtype.upper()
    numeric_types = {
        "INTEGER",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "HUGEINT",
        "FLOAT",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
        "REAL",
        "INT",
        "INT2",
        "INT4",
        "INT8",
        "INT64",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
    }
    # Handle types like DECIMAL(10,2), NUMERIC(15,4) etc.
    base_type = dtype_upper.split("(")[0].strip()
    return base_type in numeric_types


def _serialize_value(value):
    """Convert a value to JSON-serializable format."""
    if value is None:
        return None
    # Handle DuckDB's DECIMAL and other types
    if hasattr(value, "__float__"):
        float_val = float(value)
        # NaN and Infinity are not valid JSON, convert to None
        if math.isnan(float_val) or math.isinf(float_val):
            return None
        return float_val
    return value


def register_routes(bp: Blueprint):
    """Register all API routes on the given blueprint."""

    @bp.route("/api/tables", methods=["GET"])
    def get_tables():
        """Return list of tables in the database."""
        try:
            # Check if database is loaded
            if DuckDBManager._pool is None:
                return jsonify({"error": "No database loaded", "tables": []})

            with DuckDBManager.get_connection() as conn:
                # Get all tables from main schema
                result = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                ).fetchall()
                table_names = [row[0] for row in result]

            tables = []
            for table_name in table_names:
                try:
                    with DuckDBManager.get_connection() as conn:
                        # Get row count
                        row_count_result = conn.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()
                        row_count = row_count_result[0] if row_count_result else 0

                        # Get column count
                        col_count_result = conn.execute(
                            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'main' AND table_name = ?",
                            [table_name],
                        ).fetchone()
                        column_count = col_count_result[0] if col_count_result else 0

                    tables.append(
                        {
                            "name": table_name,
                            "row_count": row_count,
                            "column_count": column_count,
                        }
                    )
                except Exception as e:
                    # Skip table if there's an error
                    tables.append(
                        {
                            "name": table_name,
                            "row_count": 0,
                            "column_count": 0,
                            "error": str(e),
                        }
                    )

            return jsonify({"tables": tables})

        except Exception as e:
            return jsonify({"error": str(e), "tables": []})

    @bp.route("/api/table/<name>", methods=["GET"])
    def get_table(name):
        """Return information about a specific table."""
        try:
            # Check if database is loaded
            if DuckDBManager._pool is None:
                return jsonify({"error": "No database loaded"})

            with DuckDBManager.get_connection() as conn:
                # Check if table exists
                tables_result = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
                    [name],
                ).fetchall()

                if not tables_result:
                    return jsonify({"error": "Table not found"})

                # Get column information from information_schema
                columns_result = conn.execute(
                    """SELECT 
                        column_name, 
                        data_type, 
                        is_nullable
                    FROM information_schema.columns 
                    WHERE table_schema = 'main' AND table_name = ?
                    ORDER BY ordinal_position""",
                    [name],
                ).fetchall()

                columns = []
                for col in columns_result:
                    columns.append(
                        {"name": col[0], "type": col[1], "nullable": col[2] == "YES"}
                    )

                # Get row count
                row_count_result = conn.execute(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).fetchone()
                row_count = row_count_result[0] if row_count_result else 0

                # Try to get table size (approximate)
                size_bytes = None
                try:
                    # DuckDB doesn't have a direct table size function,
                    # but we can estimate from pragma_table_info
                    size_result = conn.execute(
                        f"SELECT SUM(dict_size + data_size) FROM pragma_storage_info('{name}')"
                    ).fetchone()
                    if size_result and size_result[0] is not None:
                        size_bytes = int(size_result[0])
                except Exception:
                    # Size estimation may not be available for all tables
                    pass

            return jsonify(
                {
                    "name": name,
                    "columns": columns,
                    "row_count": row_count,
                    "size_bytes": size_bytes,
                }
            )

        except Exception as e:
            return jsonify({"error": str(e)})

    @bp.route("/api/column/<table>/<col>", methods=["GET"])
    def get_column_profile(table, col):
        """Return profile information for a specific column."""
        try:
            # Check if database is loaded
            if DuckDBManager._pool is None:
                return jsonify({"error": "No database loaded"})

            with DuckDBManager.get_connection() as conn:
                # Check if table exists
                tables_result = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
                    [table],
                ).fetchall()

                if not tables_result:
                    return jsonify({"error": "Table not found"})

                # Get column type
                col_type_result = conn.execute(
                    "SELECT data_type FROM information_schema.columns WHERE table_schema = 'main' AND table_name = ? AND column_name = ?",
                    [table, col],
                ).fetchone()

                if not col_type_result:
                    return jsonify({"error": "Column not found"})

                column_type = col_type_result[0]

                if _is_numeric_type(column_type):
                    # Numeric column profile
                    result = conn.execute(f"""SELECT 
                            COUNT(*) as total,
                            COUNT("{col}") as non_null,
                            MIN("{col}")::VARCHAR as min_val,
                            MAX("{col}")::VARCHAR as max_val,
                            AVG("{col}")::VARCHAR as avg_val,
                            STDDEV("{col}")::VARCHAR as std_dev,
                            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "{col}")::VARCHAR as median
                        FROM "{table}" """).fetchone()

                    total = result[0]
                    non_null_count = result[1] or 0
                    null_count = total - non_null_count

                    # Parse values, handling potential None
                    min_val = (
                        float(result[2])
                        if result[2] is not None and non_null_count > 0
                        else None
                    )
                    max_val = (
                        float(result[3])
                        if result[3] is not None and non_null_count > 0
                        else None
                    )
                    avg_val = (
                        float(result[4])
                        if result[4] is not None and non_null_count > 0
                        else None
                    )
                    std_dev = (
                        float(result[5])
                        if result[5] is not None and non_null_count > 0
                        else None
                    )
                    median_val = (
                        float(result[6])
                        if result[6] is not None and non_null_count > 0
                        else None
                    )

                    # Calculate histogram with 10 buckets
                    histogram = []
                    if (
                        non_null_count > 0
                        and min_val is not None
                        and max_val is not None
                        and min_val != max_val
                    ):
                        try:
                            bucket_size = (max_val - min_val) / 10
                            hist_result = conn.execute(f"""SELECT 
                                    FLOOR(("{col}" - {min_val}) / {bucket_size}) as bucket,
                                    COUNT(*) as count
                                FROM "{table}"
                                WHERE "{col}" IS NOT NULL
                                GROUP BY bucket
                                ORDER BY bucket""").fetchall()

                            # Create all 10 buckets
                            bucket_counts = {row[0]: row[1] for row in hist_result}
                            for i in range(10):
                                bucket_min = min_val + i * bucket_size
                                bucket_max = min_val + (i + 1) * bucket_size
                                histogram.append(
                                    {
                                        "bucket_min": round(bucket_min, 6),
                                        "bucket_max": round(bucket_max, 6),
                                        "count": bucket_counts.get(float(i), 0),
                                    }
                                )
                        except Exception:
                            histogram = []

                    return jsonify(
                        {
                            "type": "numeric",
                            "column_type": column_type,
                            "non_null_count": non_null_count,
                            "null_count": null_count,
                            "min": min_val,
                            "max": max_val,
                            "avg": avg_val,
                            "stddev": std_dev,
                            "median": median_val,
                            "histogram": histogram,
                        }
                    )
                else:
                    # Categorical column profile
                    # Get total and null counts
                    count_result = conn.execute(
                        f'SELECT COUNT(*) as total, COUNT("{col}") as non_null FROM "{table}"'
                    ).fetchone()

                    total = count_result[0]
                    non_null_count = count_result[1] or 0
                    null_count = total - non_null_count

                    # Get distinct count
                    distinct_result = conn.execute(
                        f'SELECT COUNT(DISTINCT "{col}") FROM "{table}" WHERE "{col}" IS NOT NULL'
                    ).fetchone()
                    distinct_count = distinct_result[0] if distinct_result else 0

                    # Get top 20 values
                    top_values_result = conn.execute(
                        f"""SELECT "{col}"::VARCHAR as value, COUNT(*) as count
                        FROM "{table}"
                        WHERE "{col}" IS NOT NULL
                        GROUP BY "{col}"
                        ORDER BY count DESC
                        LIMIT 20"""
                    ).fetchall()

                    top_values = [
                        {"value": row[0], "count": row[1]} for row in top_values_result
                    ]

                    # Calculate other count (values beyond top 20)
                    top_values_sum = sum(row[1] for row in top_values_result)
                    other_count = non_null_count - top_values_sum

                    return jsonify(
                        {
                            "type": "categorical",
                            "column_type": column_type,
                            "non_null_count": non_null_count,
                            "null_count": null_count,
                            "distinct_count": distinct_count,
                            "top_values": top_values,
                            "other_count": other_count if distinct_count > 20 else 0,
                        }
                    )

        except Exception as e:
            return jsonify({"error": str(e)})

    @bp.route("/api/sample/<table>", methods=["GET"])
    def get_sample(table):
        """Return sample rows from a table."""
        try:
            # Check if database is loaded
            if DuckDBManager._pool is None:
                return jsonify({"error": "No database loaded"})

            with DuckDBManager.get_connection() as conn:
                # Check if table exists
                tables_result = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
                    [table],
                ).fetchall()

                if not tables_result:
                    return jsonify({"error": "Table not found"})

                # Get column names
                columns_result = conn.execute(
                    """SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'main' AND table_name = ?
                    ORDER BY ordinal_position""",
                    [table],
                ).fetchall()
                columns = [row[0] for row in columns_result]

                # Get first 100 rows
                rows_result = conn.execute(
                    f'SELECT * FROM "{table}" LIMIT 100'
                ).fetchall()

                # Convert rows to list of lists with JSON-serializable values
                rows = []
                for row in rows_result:
                    serialized_row = [_serialize_value(val) for val in row]
                    rows.append(serialized_row)

            return jsonify({"columns": columns, "rows": rows})

        except Exception as e:
            return jsonify({"error": str(e)})

    @bp.route("/api/correlations/<table>", methods=["GET"])
    def get_correlations(table):
        """Return correlation matrix for numeric columns in a table."""
        try:
            # Check if database is loaded
            if DuckDBManager._pool is None:
                return jsonify({"error": "No database loaded"})

            with DuckDBManager.get_connection() as conn:
                # Check if table exists
                tables_result = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
                    [table],
                ).fetchall()

                if not tables_result:
                    return jsonify({"error": "Table not found"})

                # Get all columns with their types
                columns_result = conn.execute(
                    """SELECT column_name, data_type
                    FROM information_schema.columns 
                    WHERE table_schema = 'main' AND table_name = ?
                    ORDER BY ordinal_position""",
                    [table],
                ).fetchall()

                # Filter numeric columns
                numeric_columns = [
                    col[0] for col in columns_result if _is_numeric_type(col[1])
                ]

                if len(numeric_columns) == 0:
                    return jsonify({"columns": [], "matrix": []})

                if len(numeric_columns) == 1:
                    # Single column - correlation with itself is 1.0
                    return jsonify({"columns": numeric_columns, "matrix": [[1.0]]})

                # Build correlation query
                # Calculate pairwise correlations using CORR function
                n = len(numeric_columns)
                matrix = [[None] * n for _ in range(n)]

                for i in range(n):
                    matrix[i][i] = 1.0  # Diagonal is always 1
                    for j in range(i + 1, n):
                        col1 = numeric_columns[i]
                        col2 = numeric_columns[j]
                        try:
                            corr_result = conn.execute(
                                f'SELECT CORR("{col1}", "{col2}") FROM "{table}" WHERE "{col1}" IS NOT NULL AND "{col2}" IS NOT NULL'
                            ).fetchone()
                            corr_value = None
                            if corr_result and corr_result[0] is not None:
                                float_val = float(corr_result[0])
                                # NaN, Infinity are not valid JSON - use None
                                if not (math.isnan(float_val) or math.isinf(float_val)):
                                    corr_value = float_val
                            matrix[i][j] = corr_value
                            matrix[j][i] = corr_value  # Symmetric
                        except Exception:
                            matrix[i][j] = None
                            matrix[j][i] = None

                # Round values for cleaner output
                for i in range(n):
                    for j in range(n):
                        if matrix[i][j] is not None:
                            # Round to 6 decimal places to avoid floating point issues
                            matrix[i][j] = round(matrix[i][j], 6)

                return jsonify({"columns": numeric_columns, "matrix": matrix})

        except Exception as e:
            return jsonify({"error": str(e)})

import json
import os
from pathlib import Path
from flask import request
from wichy.tools.query_steps.compiler import compile_recipe, CompileError
from wichy.tools.query_steps.validator import validate_recipe, ValidationError

QUERY_STEPS_DIR = Path(".wichy") / "query_steps"


def _get_query_steps_dir() -> Path:
    QUERY_STEPS_DIR.mkdir(parents=True, exist_ok=True)
    return QUERY_STEPS_DIR


def _get_columns_for_table(table_name: str) -> set[str]:
    """Return column names for a given table."""
    from wichy.tools.duckdb_manager import DuckDBManager
    with DuckDBManager.get_connection() as conn:
        cursor = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'main' AND table_name = ?",
            [table_name],
        )
        return {row[0] for row in cursor.fetchall()}


def register_recipe_routes(bp):
    """Attach recipe-related routes to the data blueprint."""

    @bp.route("/api/recipes")
    def list_recipes():
        try:
            qdir = _get_query_steps_dir()
            recipes = []
            for f in sorted(qdir.glob("*.json")):
                data = json.loads(f.read_text())
                recipes.append({
                    "name": data.get("name", f.stem),
                    "slug": data.get("slug", f.stem),
                    "created_at": data.get("created_at", ""),
                    "step_count": len(data.get("steps", [])),
                })
            return jsonify({"recipes": recipes})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/recipe/<slug>", methods=["GET"])
    def get_recipe(slug):
        try:
            qdir = _get_query_steps_dir()
            filepath = qdir / f"{slug}.json"
            if not filepath.exists():
                return jsonify({"error": "Recipe not found"}), 404
            data = json.loads(filepath.read_text())
            return jsonify({"recipe": data})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/recipe/preview", methods=["POST"])
    def preview_recipe():
        from wichy.tools.duckdb_manager import DuckDBManager
        try:
            data = request.get_json()
            if not data or "steps" not in data:
                return jsonify({"error": "Missing 'steps'"}), 400
            steps = data["steps"]
            validate_recipe(steps, _get_columns_for_table)
            sql, params = compile_recipe(steps)
            preview_sql = f"{sql} LIMIT 100"
            with DuckDBManager.get_connection() as conn:
                cursor = conn.execute(preview_sql, params)
                rows = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description] if cursor.description else []
            result_rows = [dict(zip(col_names, row)) for row in rows]
            return jsonify({"columns": col_names, "rows": result_rows, "row_count": len(result_rows), "sql_preview": sql})
        except (ValidationError, CompileError) as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/recipe/save", methods=["POST"])
    def save_recipe():
        try:
            data = request.get_json()
            if not data or "name" not in data or "steps" not in data:
                return jsonify({"error": "Missing 'name' or 'steps'"}), 400
            name = data["name"]
            slug = data.get("slug", name.lower().replace(" ", "-"))
            steps = data["steps"]
            validate_recipe(steps, _get_columns_for_table)
            qdir = _get_query_steps_dir()
            filepath = qdir / f"{slug}.json"
            from datetime import datetime, timezone
            recipe_data = {
                "name": name,
                "slug": slug,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "steps": steps,
            }
            filepath.write_text(json.dumps(recipe_data, indent=2))
            return jsonify({"status": "ok", "slug": slug, "filename": str(filepath)})
        except (ValidationError, CompileError) as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/recipe/export", methods=["POST"])
    def export_recipe():
        try:
            data = request.get_json()
            if not data or "format" not in data or "steps" not in data:
                return jsonify({"error": "Missing 'format' or 'steps'"}), 400
            fmt = data["format"]
            steps = data["steps"]
            slug = data.get("slug", "recipe")
            if fmt not in ("sql", "python"):
                return jsonify({"error": "format must be 'sql' or 'python'"}), 400
            sql, _ = compile_recipe(steps)
            qdir = _get_query_steps_dir()
            if fmt == "sql":
                path = qdir / f"{slug}.sql"
                path.write_text(f"-- Recipe: {slug}\n{sql}\n")
            else:
                path = qdir / f"{slug}.py"
                python_code = (
                    f"# Recipe: {slug}\n"
                    f"import duckdb\n\n"
                    f"SQL = \"\"\"{sql}\"\"\"\n\n"
                    f"conn = duckdb.connect()\n"
                    f"result = conn.execute(SQL).fetchall()\n"
                    f"print(result)\n"
                )
                path.write_text(python_code)
            return jsonify({"status": "ok", "filename": str(path)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/recipe/<slug>", methods=["DELETE"])
    def delete_recipe(slug):
        try:
            qdir = _get_query_steps_dir()
            filepath = qdir / f"{slug}.json"
            if not filepath.exists():
                return jsonify({"error": "Recipe not found"}), 404
            filepath.unlink()
            return jsonify({"status": "ok", "slug": slug})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
