"""The one ERPNext schema change Hubzoid needs, as an administrator step.

Hubzoid marks every draft it creates with `custom_hubzoid_operation_id`, a
unique, read-only, not-copied Data field on Sales Order and Purchase Invoice.
The unique index is what makes a lost answer safe: the same operation can
never create a second document. Installing it is an explicit administrator
action with an administrator credential, never something the integration
user or a desk run can do.

    hubzoid-erp bootstrap export . --output output/setup/erpnext-bootstrap.json
    hubzoid-erp bootstrap apply  . --manifest output/setup/erpnext-bootstrap.json --admin-credential-ref env:ERP_ADMIN_TOKEN
    hubzoid-erp bootstrap verify .
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import quote

from .errors import ERPError

FIELD = "custom_hubzoid_operation_id"
DOCTYPES = ("Sales Order", "Purchase Invoice")
_PROPS = {"fieldtype": "Data", "unique": 1, "no_copy": 1, "read_only": 1, "length": 140}


def manifest() -> dict:
    fields = [{"doctype": "Custom Field", "dt": dt, "fieldname": FIELD, "label": "Hubzoid operation id",
               "insert_after": "amended_from", "description": "Set by Hubzoid. Prevents duplicate drafts.",
               "print_hide": 1, "report_hide": 1, "allow_on_submit": 0, **_PROPS} for dt in DOCTYPES]
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return {"version": 1, "fields": fields, "digest": "sha256:" + hashlib.sha256(body.encode()).hexdigest()}


def _ok(row: dict) -> bool:
    return all(str(row.get(k)) == str(v) for k, v in _PROPS.items())


def inspect(transport) -> dict:
    """{doctype: "ok" | "missing" | "conflict" | "unverifiable"} using the
    given (integration or administrator) transport."""
    out = {}
    try:
        rows = transport.list_docs("Custom Field", filters=[["dt", "in", list(DOCTYPES)], ["fieldname", "=", FIELD]],
                                   fields=["dt", "fieldname", *_PROPS], start=0, limit=10)
    except ERPError as exc:
        if exc.code in ("erp_permission_denied", "erp_request_failed"):
            return {dt: "unverifiable" for dt in DOCTYPES}
        raise
    by_dt = {r.get("dt"): r for r in rows}
    for dt in DOCTYPES:
        row = by_dt.get(dt)
        out[dt] = "missing" if row is None else ("ok" if _ok(row) else "conflict")
    return out


def apply(admin_http, *, expected_digest: str) -> dict:
    """Create the two fields with an administrator client. Refuses a manifest
    other than the one this version ships and never edits a conflicting
    field."""
    m = manifest()
    if expected_digest != m["digest"]:
        raise ERPError("bootstrap_mismatch", "The manifest is not the one this version ships. Export it again.",
                       status=409)
    from .backends.frappe_rest import FrappeTransport  # noqa: F401 - shape reference only

    results = {}
    for field in m["fields"]:
        params = {"filters": json.dumps([["dt", "=", field["dt"]], ["fieldname", "=", FIELD]]),
                  "fields": json.dumps(["name", *_PROPS])}
        existing = (admin_http.get(f"/api/resource/{quote('Custom Field')}", params) or {}).get("data") or []
        if existing:
            results[field["dt"]] = "ok" if _ok(existing[0]) else "conflict"
            continue
        admin_http.post(f"/api/resource/{quote('Custom Field')}", field)
        results[field["dt"]] = "created"
    if "conflict" in results.values():
        raise ERPError("bootstrap_conflict", "A custom_hubzoid_operation_id field exists with other settings. It "
                       "was left unchanged. An administrator must make it unique, read-only and not copied.",
                       status=409, results=results)
    return results
