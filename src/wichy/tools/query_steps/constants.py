"""Shared constants for the Query Steps DSL.

Imported by both the compiler and validator to ensure they reference
the same grammar definitions.
"""

MAX_STEPS = 20
MAX_LIMIT = 10000

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
