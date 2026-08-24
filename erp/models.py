"""Read results and the trusted read context.

Every record has the same shape on every backend:

    {resource, id, company, modified_at, observed_at, backend, synthetic,
     fields: {...allowlisted...}, rows: [...child rows with their ERP row id...],
     source: {doctype, name, fields: [...]}}

Money and quantities are Decimal strings, never floats. Dates are ISO
calendar dates and timestamps UTC RFC 3339 with a Z.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .errors import ERPError
from .resources import DATE_FIELDS, DECIMAL_FIELDS, INT_FIELDS, Resource


@dataclass(frozen=True)
class ERPContext:
    """Who is reading, assembled by trusted server code, never by a model.

    `party_scope` binds a desk case to one customer or supplier: the adapter
    returns `not_found` for any record of another party. `None` means the
    caller may see every party the integration user can see (an internal
    operator of the Order & Invoice Desk)."""

    hub_id: str
    principal: str
    requester: str = ""
    work_item_id: str | None = None
    party_scope: tuple[str, str] | None = None        # ("customer", "SYN-CUST-001")
    allowed_resources: frozenset[str] | None = None   # None: every resource in RESOURCES

    def allows(self, resource: str) -> bool:
        return self.allowed_resources is None or resource in self.allowed_resources


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ERPError("protocol_error", "A boolean arrived where a number was expected.", status=502)
    if isinstance(value, float):
        value = repr(value)
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ERPError("protocol_error", f"{value!r} is not a number.", status=502) from None
    if not d.is_finite():
        raise ERPError("protocol_error", "A number was not finite.", status=502)
    return d


def dec_str(value) -> str | None:
    d = to_decimal(value)
    if d is None:
        return None
    # Normalise without exponent: 125.000000 -> "125", 0.5 -> "0.5"
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


def frappe_ts_to_utc(value: str | None) -> str | None:
    """Frappe timestamps are naive, in the site's time zone. The desk test
    site and the synthetic data run in UTC, which doctor verifies."""
    if not value:
        return None
    text = str(value).replace("T", " ").split("+")[0].rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    raise ERPError("protocol_error", "An ERP timestamp was not in Frappe's format.", status=502)


def _value(name: str, value):
    if name in DECIMAL_FIELDS:
        return dec_str(value)
    if name in INT_FIELDS:
        if value is None or value == "":
            return None
        return int(value)
    if name in DATE_FIELDS:
        return str(value)[:10] if value else None
    if value is None:
        return None
    return value if isinstance(value, (str, int, bool)) else str(value)


def normalize(resource: Resource, doc: dict, *, backend: str, synthetic: bool, observed_at: str,
              full: bool = True) -> dict:
    """An ERP document as a record: allowlisted fields only."""
    fields = {f: _value(f, doc.get(f)) for f in resource.fields}
    if resource.doctype == "Item":
        fields["item_code"] = doc.get("item_code") or doc.get("name")
    rows = []
    if full and resource.child_table:
        for row in doc.get(resource.child_table) or []:
            out = {"row_id": row.get("name"), "idx": row.get("idx")}
            for f in resource.child_fields:
                out[f] = _value(f, row.get(f))
            rows.append(out)
    company = doc.get(resource.company_field) if resource.company_field else None
    return {
        "resource": resource.name, "id": doc.get("name"), "company": company,
        "modified_at": frappe_ts_to_utc(doc.get("modified")), "erp_modified": doc.get("modified"),
        "observed_at": observed_at, "backend": backend, "synthetic": synthetic,
        "fields": fields, "rows": rows if (full and resource.child_table) else None,
        "source": {"doctype": resource.doctype, "name": doc.get("name"),
                   "fields": list(resource.fields) + ([f"{resource.child_table}[].{f}" for f in resource.child_fields]
                                                      if full and resource.child_table else [])},
    }


# -- cursors ------------------------------------------------------------------------------------

@dataclass
class Page:
    records: list[dict] = field(default_factory=list)
    next_cursor: str | None = None


def make_cursor(key: bytes, binding: dict, offset: int) -> str:
    body = json.dumps({"b": binding, "o": offset}, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(key, body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(mac + body).decode().rstrip("=")


def read_cursor(key: bytes, binding: dict, cursor: str) -> int:
    """The offset in a cursor, refused unless it was issued for this exact
    resource, filter set, site and caller."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        mac, body = raw[:16], raw[16:]
        if not hmac.compare_digest(mac, hmac.new(key, body, hashlib.sha256).digest()[:16]):
            raise ValueError
        data = json.loads(body)
        if data.get("b") != binding:
            raise ValueError
        return int(data["o"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ERPError("invalid_cursor", "This cursor belongs to another query, site or caller. Start the "
                       "listing again without it.", status=400) from None
