"""The hub-owned ERP write contract: three calls and nothing else.

    writer = DraftWriter(cfg, read_transport, write_transport_factory, deployment_id=...)
    writer.create_draft("sales_order", payload, external_ref)  # an inert draft, docstatus 0
    writer.get("sales_order", name)                             # reread, with digests
    writer.submit_approved("sales_order", name, approved_digest)

Safety rules, the same on every backend:

* Only Sales Order and Purchase Invoice drafts. The payload is typed: status
  fields (docstatus, status, per_* and the like) are stripped and reported,
  `name`, `owner` and any unknown field are refused, docstatus is forced to 0.
* Company, currency, warehouses and price lists must equal the applied
  configuration. A message can never choose them.
* Duplicates are prevented twice: the external reference becomes a unique
  marker in `custom_hubzoid_operation_id` (the same reference never creates a
  second document, even after a lost answer), and the business reference (the
  customer's PO number, the supplier's bill number) is searched first.
* After every create the document is read back and compared with the
  proposal: any difference in lines, unit conversion, currency or totals is
  `needs_review`, never silently accepted, rounded or repaired. A document
  found again by its marker (a deduplicated create, or reconciliation after a
  lost answer) goes through the same checks (`recover`).
* On a live site the marker field must be unique before anything is inserted:
  a lost answer is retried only because ERPNext itself refuses a second
  document with the same marker.
* Submit rereads the draft, recomputes its approval digest (content, name,
  `modified` version, docstatus) and refuses unless it equals the approved
  digest. The submit sends that exact document, so Frappe's own `modified`
  check refuses it if anything changed in between. It uses its own credential
  and is never retried here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import digest as dg
from .errors import ERPError, NotDispatched, Refused, WriteUncertain, writes_disabled
from .resources import WRITE_KINDS

MAX_LINES = 200
#: Stripped and reported: the ERP owns them, and docstatus is always sent as 0.
STATUS_FIELDS = frozenset({"docstatus", "status", "workflow_state", "per_delivered", "per_billed", "per_received",
                           "delivery_status", "billing_status", "outstanding_amount", "is_return",
                           "submit_on_creation", "_submit", "submitted"})
#: Refused: identity, amendment and internal keys (Frappe would honour or act on
#: some of them, and silently drop unknown ones).
REFUSED_FIELDS = frozenset({"name", "__newname", "owner", "creation", "modified", "modified_by", "naming_series",
                            "doctype", "amended_from", "amendment_date", "flags", "__islocal", "__unsaved",
                            "custom_hubzoid_operation_id", "parent", "parenttype", "parentfield", "idx"})


def _decimal(value, *, positive: bool, field: str) -> str:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number") from None
    if not d.is_finite() or (positive and d <= 0) or (not positive and d < 0):
        raise ValueError(f"{field} must be {'positive' if positive else 'zero or more'}")
    if isinstance(value, float):
        raise ValueError(f"{field} must be a decimal string, not a float")
    return format(d.normalize(), "f")


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_code: str = Field(min_length=1, max_length=140)
    qty: str
    uom: str = Field(min_length=1, max_length=140)
    rate: str
    warehouse: str | None = None
    source_ref: str | None = Field(default=None, max_length=500)   # kept by the desk, not sent to the ERP
    #: The unit conversion the proposal used (stock units per `uom`). Never
    #: sent: ERPNext sets its own from the item, and the reread must match it.
    expected_conversion_factor: str | None = None

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v):
        return _decimal(v, positive=True, field="qty")

    @field_validator("expected_conversion_factor", mode="before")
    @classmethod
    def _factor(cls, v):
        return None if v is None else _decimal(v, positive=True, field="expected_conversion_factor")

    @field_validator("rate", mode="before")
    @classmethod
    def _rate(cls, v):
        return _decimal(v, positive=False, field="rate")


class SalesOrderLine(_Line):
    delivery_date: date | None = None


class PurchaseInvoiceLine(_Line):
    purchase_order: str | None = None
    po_detail: str | None = None
    purchase_receipt: str | None = None
    pr_detail: str | None = None


class SalesOrderDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    customer: str = Field(min_length=1, max_length=140)
    po_no: str = Field(min_length=1, max_length=140)
    transaction_date: date
    delivery_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    selling_price_list: str
    set_warehouse: str
    items: list[SalesOrderLine] = Field(min_length=1, max_length=MAX_LINES)


class PurchaseInvoiceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    supplier: str = Field(min_length=1, max_length=140)
    bill_no: str = Field(min_length=1, max_length=140)
    bill_date: date
    posting_date: date
    due_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    buying_price_list: str
    set_warehouse: str | None = None
    items: list[PurchaseInvoiceLine] = Field(min_length=1, max_length=MAX_LINES)


MODELS = {"sales_order": SalesOrderDraft, "purchase_invoice": PurchaseInvoiceDraft}
PARTY = {"sales_order": ("customer", "po_no"), "purchase_invoice": ("supplier", "bill_no")}


def _kind(kind: str) -> str:
    if kind not in WRITE_KINDS:
        raise ERPError("write_not_allowed", f"{kind!r} is not a document this desk may draft. Kinds: "
                       "sales_order, purchase_invoice.", status=403)
    return WRITE_KINDS[kind]


def clean_payload(kind: str, payload: dict) -> tuple[BaseModel, list[str]]:
    """Validate a proposed payload. Returns the typed model and the status
    fields that were stripped."""
    _kind(kind)
    if not isinstance(payload, dict):
        raise ERPError("invalid_payload", "The draft payload must be an object.", status=422)
    stripped = sorted(k for k in payload if k in STATUS_FIELDS)
    refused = sorted(k for k in payload if k in REFUSED_FIELDS)
    if refused:
        raise ERPError("invalid_payload", f"A draft cannot set {', '.join(refused)}. The ERP assigns them.",
                       status=422, fields=refused)
    data = {k: v for k, v in payload.items() if k not in STATUS_FIELDS}
    if isinstance(data.get("items"), list):
        data["items"] = [dict(r) if isinstance(r, dict) else r for r in data["items"]]
    for row in data.get("items") or []:
        if isinstance(row, dict):
            for k in list(row):
                if k in STATUS_FIELDS:
                    stripped.append(f"items[].{k}")
                    row.pop(k)
                elif k in REFUSED_FIELDS or k in ("amount", "conversion_factor", "stock_qty"):
                    raise ERPError("invalid_payload", f"A draft line cannot set {k}. The ERP computes it.",
                                   status=422)
    try:
        return MODELS[kind].model_validate(data), sorted(set(stripped))
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ERPError("invalid_payload", f"{loc}: {first['msg'].removeprefix('Value error, ')}", status=422,
                       field=loc) from None


class DraftWriter:
    def __init__(self, cfg, read_transport, submit_transport_factory: Callable[[], object] | None = None, *,
                 deployment_id: str, write_transport=None):
        self.cfg = cfg
        self.rt = read_transport
        self.wt = write_transport or read_transport
        self.submit_transport_factory = submit_transport_factory
        self.deployment_id = deployment_id

    # -- helpers --------------------------------------------------------------------------------
    def marker(self, external_ref: str) -> str:
        if not isinstance(external_ref, str) or not external_ref.strip() or len(external_ref) > 512:
            raise ERPError("invalid_payload", "external_ref is required: the stable logical operation key.",
                           status=422)
        return dg.operation_marker(self.deployment_id, self.cfg.site_fingerprint(), self.cfg.company, external_ref)

    def _check_config(self, model) -> None:
        cfg = self.cfg
        if model.company != cfg.company:
            raise ERPError("company_denied", f"This connector writes only to {cfg.company}.", status=403)
        if model.currency != cfg.currency:
            raise ERPError("erp_validation_failed", f"The currency must be {cfg.currency}. Hubzoid never "
                           "converts currencies.", status=422)
        price_list = getattr(model, "selling_price_list", None) or getattr(model, "buying_price_list", None)
        expected = cfg.selling_price_list if isinstance(model, SalesOrderDraft) else cfg.buying_price_list
        if price_list != expected:
            raise ERPError("erp_validation_failed", f"The price list must be {expected}.", status=422)
        warehouses = {model.set_warehouse} if getattr(model, "set_warehouse", None) else set()
        warehouses |= {line.warehouse for line in model.items if line.warehouse}
        bad = sorted(w for w in warehouses if w not in cfg.warehouses)
        if bad:
            raise ERPError("erp_validation_failed", f"Warehouse {', '.join(bad)} is not allowed for this desk.",
                           status=422)

    def _to_doc(self, kind: str, model, marker: str) -> dict:
        data = model.model_dump(mode="json", exclude_none=True)
        for row in data["items"]:
            row.pop("source_ref", None)
            row.pop("expected_conversion_factor", None)
            if kind == "sales_order":
                row.setdefault("delivery_date", data["delivery_date"])
                row.setdefault("warehouse", data["set_warehouse"])
            elif data.get("set_warehouse"):
                row.setdefault("warehouse", data["set_warehouse"])
        doc = {"doctype": WRITE_KINDS[kind], "docstatus": 0, "custom_hubzoid_operation_id": marker, **data}
        if kind == "purchase_invoice":
            doc["update_stock"] = 0
        return doc

    def _by_marker(self, doctype: str, marker: str) -> list[dict]:
        rows = self.rt.list_docs(doctype, filters=[["custom_hubzoid_operation_id", "=", marker]],
                                 fields=["docstatus"], start=0, limit=5)
        return [self.rt.get_doc(doctype, r["name"]) for r in rows]

    def _expected(self, kind: str, model) -> dict:
        """What the proposal says, in the document's own shape, before the
        ERP computes anything: used to compare the reread."""
        data = model.model_dump(mode="json", exclude_none=True)
        lines = []
        for i, row in enumerate(data["items"], 1):
            line = {"idx": i, **{k: v for k, v in row.items() if k not in ("source_ref", "expected_conversion_factor")}}
            if row.get("expected_conversion_factor") is not None:
                line["conversion_factor"] = row["expected_conversion_factor"]
            if kind == "sales_order":
                line.setdefault("delivery_date", data["delivery_date"])
                line.setdefault("warehouse", data["set_warehouse"])
            elif data.get("set_warehouse"):
                line.setdefault("warehouse", data["set_warehouse"])
            line["amount"] = str((Decimal(row["qty"]) * Decimal(row["rate"])).quantize(Decimal("0.01")))
            lines.append(line)
        total = sum((Decimal(l["amount"]) for l in lines), Decimal("0"))
        return {"doctype": WRITE_KINDS[kind], **{k: v for k, v in data.items() if k != "items"}, "items": lines,
                "total": str(total), "grand_total": str(total)}

    def _differences(self, expected: dict, observed: dict) -> list[dict]:
        diffs = []
        want, got = dg.content(expected), dg.content(observed)
        for field, value in want["header"].items():
            if value is not None and got["header"].get(field) != value:
                diffs.append({"field": field, "proposed": value, "erp": got["header"].get(field)})
        if len(want["lines"]) != len(got["lines"]):
            diffs.append({"field": "lines", "proposed": len(want["lines"]), "erp": len(got["lines"])})
        for i, (w, g) in enumerate(zip(want["lines"], got["lines"]), 1):
            for field, value in w.items():
                if value is not None and g.get(field) != value:
                    diffs.append({"field": f"line {i}.{field}", "proposed": value, "erp": g.get(field)})
        for field in ("grand_total",):
            if dg.dec_str(want["totals"].get(field) or "0") != dg.dec_str(got["totals"].get(field) or "0"):
                diffs.append({"field": field, "proposed": want["totals"].get(field),
                              "erp": got["totals"].get(field),
                              "note": "The ERP computed a different total (taxes, discounts or rounding). "
                                      "Review it in the ERP before going on."})
        return diffs

    def view(self, kind: str, doc: dict, **extra) -> dict:
        party_field, ref_field = PARTY[kind]
        c = dg.content(doc)
        return {
            "kind": kind, "doctype": doc.get("doctype"), "name": doc.get("name"),
            "docstatus": int(doc.get("docstatus") or 0), "status": doc.get("status"),
            "erp_modified": str(doc.get("modified")), "company": doc.get("company"),
            "party": doc.get(party_field), "business_reference": doc.get(ref_field),
            "currency": doc.get("currency"), "header": c["header"], "lines": c["lines"], "totals": c["totals"],
            "content_digest": dg.content_digest(doc), "approval_digest": dg.approval_digest(doc),
            "marker": doc.get("custom_hubzoid_operation_id"), "backend": getattr(self.rt, "name", ""),
            "synthetic": self.cfg.synthetic, "link": self.link(doc), **extra,
        }

    def link(self, doc: dict) -> str | None:
        if self.cfg.backend == "sample" or not doc.get("name"):
            return None
        slug = doc["doctype"].lower().replace(" ", "-")
        return f"{self.cfg.origin}/app/{slug}/{doc['name']}"

    def require_unique_marker(self, kind: str) -> None:
        """Raises unless the ERP itself refuses a second document with the same
        marker: the condition for ever retrying a create whose answer was lost."""
        self._require_unique_marker(_kind(kind))

    def _require_unique_marker(self, doctype: str) -> None:
        """A lost answer is retried only because the ERP refuses a second
        document with the same marker. On a site, prove that before writing."""
        if self.cfg.backend == "sample":
            return                     # a unique index in the sample's own store
        cache = self.__dict__.setdefault("_unique_checked", {})
        if cache.get(doctype):
            return
        from .bootstrap import inspect

        state = inspect(self.rt).get(doctype)
        if state != "ok":
            raise ERPError("erp_schema_unsupported", f"custom_hubzoid_operation_id is {state or 'missing'} on "
                           f"{doctype}. It must exist and be unique before the desk writes, so a lost answer can "
                           "never become two documents. An ERP administrator runs hubzoid-erp bootstrap.",
                           status=409, state=state)
        cache[doctype] = True

    def _business_clash(self, doctype: str, party_field: str, ref_field: str, party: str, ref: str) -> list[dict]:
        """Every live document of this party with the same business reference,
        compared after normalising (case, width, spacing). Reads every page:
        a clash after the first page must not be missed."""
        page, start, out = 500, 0, []
        while True:
            rows = self.rt.list_docs(doctype, filters=[[party_field, "=", party], ["company", "=", self.cfg.company],
                                                       ["docstatus", "!=", 2]],
                                     fields=[ref_field, "docstatus"], start=start, limit=page)
            out += [r for r in rows if dg.normalize_reference(r.get(ref_field)) == ref]
            if len(rows) < page:
                return out
            start += page
            if start >= 50000:
                raise ERPError("erp_validation_failed", f"This {party_field} has more than 50,000 {doctype} "
                               "documents, so the duplicate check cannot be completed. Nothing was written.",
                               status=409)

    def recover(self, kind: str, payload: dict, external_ref: str) -> dict:
        """What the ERP holds for this operation after a lost or interrupted
        create, checked exactly as a fresh create is: inert, carrying the
        marker, and equal to the proposal.

        Returns {"status": ..., "view": ..., "names": [...]} where status is
        `absent` (no document carries the marker), `applied` (one inert draft
        that matches, possibly with an ERP-computed total to review),
        `different` (one document whose content differs), `not_inert` (one
        document that is no longer a draft) or `several` (an incident)."""
        doctype = _kind(kind)
        model, _ = clean_payload(kind, payload)
        expected = self._expected(kind, model)
        found = [d for d in self._by_marker(doctype, self.marker(external_ref)) if d]
        if not found:
            return {"status": "absent", "view": None, "names": []}
        if len(found) > 1:
            return {"status": "several", "view": None, "names": [d["name"] for d in found]}
        doc = found[0]
        diffs = self._differences(expected, doc)
        if int(doc.get("docstatus") or 0) != 0:
            return {"status": "not_inert", "names": [doc["name"]],
                    "view": self.view(kind, doc, deduplicated=True, needs_review=True, differences=diffs + [
                        {"field": "docstatus", "proposed": 0, "erp": int(doc.get("docstatus") or 0)}])}
        if any(d["field"] != "grand_total" for d in diffs):
            return {"status": "different", "names": [doc["name"]],
                    "view": self.view(kind, doc, deduplicated=True, needs_review=True, differences=diffs)}
        return {"status": "applied", "names": [doc["name"]],
                "view": self.view(kind, doc, deduplicated=True, needs_review=bool(diffs), differences=diffs)}

    # -- the contract ---------------------------------------------------------------------------
    def create_draft(self, kind: str, payload: dict, external_ref: str) -> dict:
        doctype = _kind(kind)
        if not self.cfg.writes_enabled:
            raise writes_disabled()
        model, stripped = clean_payload(kind, payload)
        self._check_config(model)
        marker = self.marker(external_ref)
        party_field, ref_field = PARTY[kind]
        expected = self._expected(kind, model)
        # 1. The same operation already created a document: return it, never a second one.
        existing = [d for d in self._by_marker(doctype, marker) if d]
        if len(existing) > 1:
            raise ERPError("reconcile_conflict", "More than one ERP document carries this operation id. An "
                           "administrator must investigate before anything else is written.", status=409,
                           names=[d["name"] for d in existing])
        if existing:
            doc = existing[0]
            if int(doc.get("docstatus") or 0) != 0:
                raise ERPError("draft_not_inert", f"{doctype} {doc['name']} for this operation is no longer a draft "
                               f"(docstatus {doc.get('docstatus')}). Review it in the ERP.", status=409,
                               name=doc["name"])
            diffs = self._differences(expected, doc)
            if any(d["field"] != "grand_total" for d in diffs):
                raise ERPError("operation_conflict", "This operation already created a different document. "
                               "Review it in the ERP. A new document needs a new proposal.", status=409,
                               name=doc["name"])
            return self.view(kind, doc, deduplicated=True, differences=diffs, needs_review=bool(diffs),
                             stripped=stripped)
        # 2. A document with the same business reference already exists.
        ref = dg.normalize_reference(getattr(model, ref_field))
        clash = self._business_clash(doctype, party_field, ref_field, getattr(model, party_field), ref)
        if clash:
            raise ERPError("duplicate_found", f"{doctype} {clash[0]['name']} already has {ref_field} "
                           f"{getattr(model, ref_field)} for this {party_field}. Review it instead of creating "
                           "another.", status=409, name=clash[0]["name"],
                           business_key=[self.cfg.company, getattr(model, party_field), ref])
        # 3. Create, then read back. Only where the marker is proven unique.
        self._require_unique_marker(doctype)
        doc = self._to_doc(kind, model, marker)
        try:
            created = self.wt.insert(doctype, doc)
        except Refused as exc:
            if exc.code == "duplicate_found":
                # The unique marker collided: a concurrent attempt won. Read again.
                again = [d for d in self._by_marker(doctype, marker) if d]
                if len(again) == 1 and not [d for d in self._differences(expected, again[0])
                                            if d["field"] != "grand_total"]:
                    return self.view(kind, again[0], deduplicated=True, differences=[], needs_review=False,
                                     stripped=stripped)
            raise
        name = created.get("name")
        reread = self.rt.get_doc(doctype, name) if name else None
        if reread is None:
            raise WriteUncertain("The ERP answered the create but the draft could not be read back. Reconcile "
                                 "before retrying.")
        if int(reread.get("docstatus") or 0) != 0:
            raise ERPError("draft_not_inert", f"{doctype} {name} was saved with docstatus "
                           f"{reread.get('docstatus')}, not as a draft. Writes must stay disabled until an "
                           "administrator checks the site's hooks. Nothing will be cancelled automatically.",
                           status=500, name=name, critical=True)
        if reread.get("custom_hubzoid_operation_id") != marker:
            raise ERPError("erp_schema_unsupported", "The saved draft does not carry the Hubzoid operation id. "
                           "Install the custom field (hubzoid-erp bootstrap) before creating drafts.",
                           status=500, name=name)
        diffs = self._differences(expected, reread)
        return self.view(kind, reread, deduplicated=False, differences=diffs, needs_review=bool(diffs),
                         stripped=stripped)

    def _workflow_active(self, transport, doctype: str) -> bool:
        if self.cfg.backend == "sample":
            return False
        try:
            rows = transport.list_docs("Workflow", filters=[["document_type", "=", doctype], ["is_active", "=", 1]],
                                       fields=["document_type"], start=0, limit=1)
        except ERPError as exc:
            if exc.code == "not_found":
                return False
            raise ERPError("submit_not_allowed", "Cannot check whether an ERPNext Workflow governs this document. "
                           "Give the submit user read access to Workflow.", status=409) from None
        return bool(rows)

    def get(self, kind: str, name: str) -> dict:
        doctype = _kind(kind)
        doc = self.rt.get_doc(doctype, name)
        if doc is None or doc.get("company") != self.cfg.company:
            raise ERPError("not_found", f"No {doctype} {name} here.", status=404)
        return self.view(kind, doc)

    def find(self, kind: str, external_ref: str) -> list[dict]:
        """Every document this operation created (for reconciliation): none,
        one, or (an incident) more than one."""
        doctype = _kind(kind)
        return [self.view(kind, d) for d in self._by_marker(doctype, self.marker(external_ref)) if d]

    def submit_approved(self, kind: str, name: str, approved_digest: str) -> dict:
        doctype = _kind(kind)
        if not self.cfg.writes_enabled:
            raise writes_disabled()
        if not isinstance(approved_digest, str) or not approved_digest.startswith("sha256:"):
            raise ERPError("invalid_payload", "approved_digest is the sha256: digest the person approved.",
                           status=422)
        # Reread and submit with the same (submit) credential, and post exactly the
        # document just read: frappe.client.submit saves the posted copy and
        # compares its `modified` under a row lock.
        transport = self.submit_transport_factory() if self.submit_transport_factory else self.wt
        try:
            doc = transport.get_doc(doctype, name)
            if doc is None or doc.get("company") != self.cfg.company:
                raise ERPError("not_found", f"No {doctype} {name} here.", status=404)
            status = int(doc.get("docstatus") or 0)
            if status == 1:
                raise ERPError("already_submitted", f"{doctype} {name} is already submitted.", status=409,
                               content_digest=dg.content_digest(doc))
            if status != 0:
                raise ERPError("erp_validation_failed", f"{doctype} {name} is cancelled and cannot be submitted.",
                               status=409)
            if not doc.get("custom_hubzoid_operation_id"):
                raise ERPError("submit_not_allowed", f"{doctype} {name} was not created by this desk, so it is "
                               "submitted in the ERP by a person.", status=403)
            current = dg.approval_digest(doc)
            if current != approved_digest:
                raise ERPError("approval_stale", "The draft changed in the ERP after it was approved (its lines, "
                               "totals or version). Review the current draft and approve it again.", status=409,
                               current_digest=current)
            if self._workflow_active(transport, doctype):
                raise ERPError("submit_not_allowed", f"{doctype} has an active ERPNext Workflow. Submit it through "
                               "that workflow in the ERP.", status=409)
            before = dg.content_digest(doc)
            transport.submit(doc)
        except Refused as exc:
            if exc.code == "stale_preview":
                raise ERPError("approval_stale", "The ERP refused the submit because the draft changed after "
                               "it was read. Review it and approve again.", status=409) from None
            raise
        finally:
            close = getattr(transport, "close", None)
            if close and transport is not self.wt and transport is not self.rt:
                close()
        after = self.rt.get_doc(doctype, name)
        if after is None or int(after.get("docstatus") or 0) != 1:
            raise WriteUncertain("The ERP answered the submit but the document does not read back as "
                                 "submitted. Reconcile before retrying.")
        changed = dg.content_digest(after) != before
        return self.view(kind, after, submitted_content_matches=not changed, needs_review=changed)


__all__ = ["DraftWriter", "clean_payload", "MODELS", "NotDispatched", "Refused", "WriteUncertain"]
