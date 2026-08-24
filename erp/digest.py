"""Canonical business content of the two writable documents, and its digests.

* `content(doc)` is what a person approves: company, party, reference, dates,
  currency, price list, every line and the ERP-computed totals. Values are
  normalised (Decimal strings, ISO dates) so the same document always has the
  same bytes, on every backend.
* `content_digest(doc)` hashes `content`.
* `approval_digest(doc)` also binds the ERP document's name, `modified`
  version and docstatus, so any change to the saved draft, however small,
  makes an approval stale.

Digests are `sha256:` plus lowercase hex of canonical JSON (sorted keys, no
spaces), as in the Hubzoid conventions.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

from .models import dec_str

HEADER = {
    "Sales Order": ("company", "customer", "po_no", "transaction_date", "delivery_date", "currency",
                    "selling_price_list", "set_warehouse"),
    "Purchase Invoice": ("company", "supplier", "bill_no", "bill_date", "posting_date", "due_date", "currency",
                         "buying_price_list", "set_warehouse"),
}
LINE = {
    "Sales Order": ("item_code", "qty", "uom", "conversion_factor", "rate", "amount", "delivery_date",
                    "warehouse"),
    "Purchase Invoice": ("item_code", "qty", "uom", "conversion_factor", "rate", "amount", "purchase_order",
                         "po_detail", "purchase_receipt", "pr_detail", "warehouse"),
}
TOTALS = ("total_qty", "total", "net_total", "total_taxes_and_charges", "grand_total", "rounded_total")
_NUMERIC = {"qty", "conversion_factor", "rate", "amount", *TOTALS}


def _norm(field: str, value):
    if value is None or value == "":
        return None
    if field in _NUMERIC:
        return dec_str(value)
    if field.endswith("_date"):
        return str(value)[:10]
    return str(value)


def content(doc: dict) -> dict:
    doctype = doc["doctype"]
    return {
        "doctype": doctype,
        "header": {f: _norm(f, doc.get(f)) for f in HEADER[doctype]},
        "lines": [{f: _norm(f, row.get(f)) for f in LINE[doctype]}
                  for row in sorted(doc.get("items") or [], key=lambda r: int(r.get("idx") or 0))],
        "totals": {f: _norm(f, doc.get(f)) for f in TOTALS},
    }


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def content_digest(doc: dict) -> str:
    return sha(content(doc))


def approval_digest(doc: dict) -> str:
    return sha({"content": content(doc), "name": doc.get("name"), "modified": str(doc.get("modified")),
                "docstatus": int(doc.get("docstatus") or 0)})


def normalize_reference(value: str | None) -> str:
    """Business references: Unicode NFKC, trimmed, casefolded. Punctuation and
    leading zeros are kept."""
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def operation_marker(deployment_id: str, site_fingerprint: str, company: str, external_ref: str) -> str:
    """The value stored in `custom_hubzoid_operation_id`: stable for one
    logical operation on one site and company, 64 hex characters."""
    raw = "\n".join([deployment_id, site_fingerprint, company, external_ref])
    return hashlib.sha256(raw.encode()).hexdigest()
