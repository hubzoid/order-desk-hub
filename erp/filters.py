"""Frappe list filters, built in one place and evaluated in memory by the
sample backend and the fake REST server.

A filter is `[field, op, value]` on the document, or
`[child_doctype, field, op, value]` on one of its child rows (Frappe's own
form). Only these operators exist here: =, !=, like, in, not in, >, >=, <, <=,
is (set or not set).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import ERPError

OPS = ("=", "!=", "like", "in", "not in", ">", ">=", "<", "<=", "is")

#: child DocType -> the parent's table field, for the resources we read
CHILD_TABLES = {
    "Sales Order Item": "items", "Purchase Order Item": "items", "Purchase Receipt Item": "items",
    "Sales Invoice Item": "items", "Purchase Invoice Item": "items", "UOM Conversion Detail": "uoms",
}


def check(filters) -> list:
    out = []
    for f in filters or []:
        if not isinstance(f, (list, tuple)) or len(f) not in (3, 4):
            raise ERPError("invalid_filter", "A filter is [field, op, value].", status=400)
        op = f[-2]
        if op not in OPS:
            raise ERPError("invalid_filter", f"Unsupported filter operator {op!r}.", status=400)
        out.append(list(f))
    return out


def _num(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _cmp(actual, op, expected) -> bool:
    if op == "is":
        is_set = actual not in (None, "")
        return is_set if expected == "set" else not is_set
    if op == "like":
        if actual is None:
            return False
        pattern = str(expected).lower()
        text = str(actual).lower()
        if pattern.startswith("%") and pattern.endswith("%"):
            return pattern.strip("%") in text
        if pattern.endswith("%"):
            return text.startswith(pattern[:-1])
        if pattern.startswith("%"):
            return text.endswith(pattern[1:])
        return text == pattern
    if op in ("in", "not in"):
        values = expected if isinstance(expected, (list, tuple)) else [v.strip() for v in str(expected).split(",")]
        hit = any(_eq(actual, v) for v in values)
        return hit if op == "in" else not hit
    if op in ("=", "!="):
        hit = _eq(actual, expected)
        return hit if op == "=" else not hit
    a, e = _num(actual), _num(expected)
    if a is None or e is None:
        a, e = str(actual or ""), str(expected or "")
    return {">": a > e, ">=": a >= e, "<": a < e, "<=": a <= e}[op]


def _eq(actual, expected) -> bool:
    if actual is None:
        return expected in (None, "")
    if isinstance(actual, (int, float, Decimal)) or isinstance(expected, (int, float, Decimal)):
        a, e = _num(actual), _num(expected)
        if a is not None and e is not None:
            return a == e
    return str(actual) == str(expected)


def matches(doc: dict, filters) -> bool:
    for f in filters or []:
        if len(f) == 4:
            table = CHILD_TABLES.get(f[0])
            if table is None:
                raise ERPError("invalid_filter", f"Unknown child table {f[0]!r}.", status=400)
            if not any(_cmp(row.get(f[1]), f[2], f[3]) for row in doc.get(table) or []):
                return False
        elif not _cmp(doc.get(f[0]), f[1], f[2]):
            return False
    return True
