"""The sample backend: an honest simulation with no network.

Reads come from the immutable `distributor-v1` files plus a local state file
that holds the drafts and submissions the desk makes, so the dataset itself
never changes. Writes follow ERPNext's own rules where they matter to the
desk: links must exist and be enabled, the operation marker is unique, a new
document is a draft (docstatus 0), totals are computed by the ERP, and a
submit is refused if the document changed since it was read (Frappe's
`modified` check). It does not run ERPNext's tax, accounting or stock logic,
and every result says `backend: sample`.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .. import filters as flt
from ..errors import ERPError, NotDispatched, Refused, WriteUncertain
from ..resources import WRITE_KINDS

USER = "sample-integration@synthetic-meridian.example"
_LOCK = threading.Lock()
_SERIES = {"Sales Order": "SAL-ORD-2000-", "Purchase Invoice": "ACC-PINV-2000-"}
_STATUS_FIELDS = ("docstatus", "status", "per_delivered", "per_billed", "per_received", "delivery_status",
                  "billing_status", "outstanding_amount")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _d(value) -> Decimal:
    return Decimal(str(value if value not in (None, "") else 0))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SampleTransport:
    name = "sample"

    def __init__(self, dataset_dir: Path, state_dir: Path):
        self.dataset_dir = Path(dataset_dir)
        if not (self.dataset_dir / "manifest.json").is_file():
            raise ERPError("sample_missing", f"The sample dataset is not at {self.dataset_dir}. Run "
                           "`hubzoid-erp sample install .`.", status=503)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._docs = self._load()
        #: test hooks: a list of (operation, fault) consumed in order
        self.faults: list[tuple[str, str]] = []

    # -- data -----------------------------------------------------------------------------------
    def _load(self) -> dict:
        docs: dict[str, dict[str, dict]] = {}
        for path in sorted((self.dataset_dir / "erp").glob("*.json")):
            for doc in json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal):
                docs.setdefault(doc["doctype"], {})[doc["name"]] = doc
        return docs

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.state_dir / "erp-sample.sqlite", timeout=30, isolation_level=None)
        conn.execute("CREATE TABLE IF NOT EXISTS sample_docs (doctype TEXT NOT NULL, name TEXT NOT NULL, "
                     "marker TEXT, doc_json TEXT NOT NULL, PRIMARY KEY (doctype, name))")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS sample_docs_marker ON sample_docs (doctype, marker) "
                     "WHERE marker IS NOT NULL")
        return conn

    def _all(self, doctype: str) -> dict[str, dict]:
        out = dict(self._docs.get(doctype, {}))
        with closing(self._db()) as db:
            for name, text in db.execute("SELECT name, doc_json FROM sample_docs WHERE doctype=?", (doctype,)):
                out[name] = json.loads(text, parse_float=Decimal)
        return out

    def reset_state(self) -> None:
        """Forget every draft and submission the desk made (sample only)."""
        with closing(self._db()) as db:
            db.execute("DELETE FROM sample_docs")

    # -- reads ----------------------------------------------------------------------------------
    def list_docs(self, doctype, *, filters, fields, start, limit):
        rows = [d for _, d in sorted(self._all(doctype).items()) if flt.matches(d, flt.check(filters))]
        return [{k: copy.deepcopy(d.get(k)) for k in ("name", "modified", *fields)} for d in rows[start:start + limit]]

    def get_doc(self, doctype, name):
        doc = self._all(doctype).get(name)
        return copy.deepcopy(doc) if doc is not None else None

    def print_pdf(self, doctype, name, print_format):
        path = self.dataset_dir / "print" / f"{name}.pdf"
        if doctype != "Sales Invoice" or not path.is_file():
            raise ERPError("not_found", "No print is available for this record.", status=404)
        return path.read_bytes()

    def logged_user(self) -> str:
        return USER

    def versions(self) -> dict:
        manifest = json.loads((self.dataset_dir / "manifest.json").read_text(encoding="utf-8"))
        return {"sample": manifest.get("dataset", "distributor-v1")}

    # -- writes ---------------------------------------------------------------------------------
    def _fault(self, op: str) -> str | None:
        for i, (name, fault) in enumerate(self.faults):
            if name == op:
                self.faults.pop(i)
                return fault
        return None

    def _validate(self, doctype: str, doc: dict) -> None:
        def need(link_doctype, value, what):
            target = self._all(link_doctype).get(value) if value else None
            if target is None:
                raise Refused("erp_validation_failed", f"{what} {value!r} does not exist.", status=422)
            if target.get("disabled"):
                raise Refused("erp_validation_failed", f"{what} {value} is disabled.", status=422)
            return target

        company = self._docs.get("Company", {})
        if doc.get("company") not in company:
            raise Refused("erp_validation_failed", f"Company {doc.get('company')!r} does not exist.", status=422)
        party = "customer" if doctype == "Sales Order" else "supplier"
        need("Customer" if party == "customer" else "Supplier", doc.get(party), party.title())
        if not doc.get("items"):
            raise Refused("erp_validation_failed", "Items are required.", status=422)
        if doctype == "Sales Order" and not doc.get("delivery_date"):
            raise Refused("erp_validation_failed", "Delivery Date is mandatory.", status=422)
        if doctype == "Purchase Invoice" and not doc.get("posting_date"):
            raise Refused("erp_validation_failed", "Posting Date is mandatory.", status=422)
        warehouses = self._docs.get("Warehouse", {})
        for row in doc["items"]:
            item = need("Item", row.get("item_code"), "Item")
            wh = row.get("warehouse") or doc.get("set_warehouse")
            if wh and wh not in warehouses:
                raise Refused("erp_validation_failed", f"Warehouse {wh!r} does not exist.", status=422)
            uom = row.get("uom") or item.get("stock_uom")
            factors = {u["uom"]: _d(u["conversion_factor"]) for u in item.get("uoms") or []}
            if uom not in factors:
                raise Refused("erp_validation_failed", f"UOM {uom} is not set for item {item['name']}.", status=422)
            if _d(row.get("qty")) <= 0:
                raise Refused("erp_validation_failed", "Quantity must be positive.", status=422)

    def _compute(self, doctype: str, doc: dict) -> None:
        total, qty = Decimal("0"), Decimal("0")
        items = self._all("Item")
        for i, row in enumerate(doc["items"], 1):
            item = items[row["item_code"]]
            row.setdefault("item_name", item.get("item_name"))
            row["uom"] = row.get("uom") or item.get("stock_uom")
            factors = {u["uom"]: _d(u["conversion_factor"]) for u in item.get("uoms") or []}
            row["conversion_factor"] = factors[row["uom"]]
            row["stock_qty"] = _d(row["qty"]) * row["conversion_factor"]
            row["amount"] = _money(_d(row["qty"]) * _d(row.get("rate")))
            row["rate"] = _d(row.get("rate"))
            row["qty"] = _d(row["qty"])
            row["idx"] = i
            row.setdefault("name", f"{doc['name']}-{i:03d}")
            row.setdefault("doctype", f"{doctype} Item")
            if doctype == "Sales Order":
                row.setdefault("delivered_qty", Decimal("0"))
                row.setdefault("delivery_date", doc.get("delivery_date"))
                row.setdefault("warehouse", doc.get("set_warehouse"))
            total += row["amount"]
            qty += row["qty"]
        doc.update(total_qty=qty, total=_money(total), net_total=_money(total),
                   total_taxes_and_charges=Decimal("0.00"), grand_total=_money(total),
                   rounded_total=_money(total))
        if doctype == "Purchase Invoice":
            doc["outstanding_amount"] = _money(total)

    def insert(self, doctype, doc):
        if doctype not in WRITE_KINDS.values():
            raise NotDispatched("write_not_allowed", f"{doctype} is not a document this desk may create.", status=403)
        doc = copy.deepcopy(doc)
        if int(doc.get("docstatus") or 0) != 0:
            raise Refused("erp_validation_failed", "A new document must be a draft.", status=422)
        fault = self._fault("insert")
        if fault == "unreachable":
            raise NotDispatched("erp_unavailable", "Could not connect to the ERP. Nothing was sent.", status=503)
        if fault == "lose_before_commit":
            raise WriteUncertain()
        self._validate(doctype, doc)
        marker = doc.get("custom_hubzoid_operation_id")
        with _LOCK, closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if marker and db.execute("SELECT 1 FROM sample_docs WHERE doctype=? AND marker=?",
                                         (doctype, marker)).fetchone():
                    raise Refused("duplicate_found", "A document with this Hubzoid operation id already exists.",
                                  status=409, exc_type="UniqueValidationError")
                prefix = _SERIES[doctype]
                existing = [n for n in self._all(doctype) if n.startswith(prefix)]
                number = max([int(n[len(prefix):]) for n in existing if n[len(prefix):].isdigit()] + [0]) + 1
                doc["name"] = f"{prefix}{number:05d}"
                doc["doctype"] = doctype
                doc["docstatus"] = 0
                doc["status"] = "Draft"
                doc["owner"] = USER
                doc["creation"] = doc["modified"] = _now()
                for f in ("per_delivered", "per_billed"):
                    if doctype == "Sales Order":
                        doc[f] = Decimal("0")
                if doctype == "Sales Order":
                    doc.update(delivery_status="Not Delivered", billing_status="Not Billed")
                self._compute(doctype, doc)
                db.execute("INSERT INTO sample_docs (doctype, name, marker, doc_json) VALUES (?,?,?,?)",
                           (doctype, doc["name"], marker, json.dumps(doc, default=str)))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        if fault == "commit_then_lose":
            raise WriteUncertain()
        return json.loads(json.dumps(doc, default=str), parse_float=Decimal)

    def submit(self, doc):
        doctype = doc.get("doctype")
        if doctype not in WRITE_KINDS.values():
            raise NotDispatched("write_not_allowed", "Only Sales Orders and Purchase Invoices can be submitted.",
                                status=403)
        fault = self._fault("submit")
        if fault == "unreachable":
            raise NotDispatched("erp_unavailable", "Could not connect to the ERP. Nothing was sent.", status=503)
        if fault == "lose_before_commit":
            raise WriteUncertain()
        with _LOCK, closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT doc_json FROM sample_docs WHERE doctype=? AND name=?",
                                 (doctype, doc.get("name"))).fetchone()
                if row is None:
                    raise Refused("not_found", "The draft does not exist (only drafts this desk saved can be "
                                  "submitted in the sample).", status=404)
                current = json.loads(row[0], parse_float=Decimal)
                if str(current.get("modified")) != str(doc.get("modified")):
                    raise Refused("stale_preview", "The document was modified after you opened it. Refresh to "
                                  "get the latest document.", status=409, exc_type="TimestampMismatchError")
                if int(current.get("docstatus") or 0) != 0:
                    raise Refused("erp_validation_failed", "Only a draft can be submitted.", status=422)
                new = copy.deepcopy(doc)
                for key in ("name", "creation", "owner", "custom_hubzoid_operation_id"):
                    new[key] = current.get(key)
                self._validate(doctype, new)
                self._compute(doctype, new)
                new["docstatus"] = 1
                new["status"] = "To Deliver and Bill" if doctype == "Sales Order" else "Unpaid"
                new["modified"] = _now()
                db.execute("UPDATE sample_docs SET doc_json=? WHERE doctype=? AND name=?",
                           (json.dumps(new, default=str), doctype, new["name"]))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        if fault == "commit_then_lose":
            raise WriteUncertain()
        return json.loads(json.dumps(new, default=str), parse_float=Decimal)

    def edit_draft(self, doctype: str, name: str, changes: dict) -> dict:
        """A person editing the draft in the ERP (sample only, for tests and
        demos of a stale approval). Updates `modified`."""
        with _LOCK, closing(self._db()) as db:
            row = db.execute("SELECT doc_json FROM sample_docs WHERE doctype=? AND name=?", (doctype, name)).fetchone()
            if row is None:
                raise ERPError("not_found", "No such draft.", status=404)
            doc = json.loads(row[0], parse_float=Decimal)
            if int(doc.get("docstatus") or 0) != 0:
                raise ERPError("erp_validation_failed", "Only a draft can be edited.", status=422)
            for key, value in changes.items():
                if key in _STATUS_FIELDS or key in ("name", "doctype"):
                    continue
                if key == "items":
                    doc["items"] = value
                else:
                    doc[key] = value
            self._compute(doctype, doc)
            doc["modified"] = _now()
            db.execute("UPDATE sample_docs SET doc_json=? WHERE doctype=? AND name=?",
                       (json.dumps(doc, default=str), doctype, name))
        return doc
