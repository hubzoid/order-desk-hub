"""Typed, allowlisted ERP reads: the part both desks share.

    reader = ERPReader(cfg, transport, cursor_key=...)
    reader.list_records("sales_orders", {"customer": "SYN-CUST-001"}, ctx)
    reader.get_record("items", "SYN-ITEM-001", ctx)
    reader.get_print_pdf("sales_invoices", "ACC-SINV-2000-00001", ctx)

Rules enforced here for every backend:

* only the resources and fields in `erp.resources`;
* transactional records only for the configured company, stock only for the
  configured warehouses;
* `party_scope` hides every record of another party (`not_found`, never a
  hint that it exists);
* filters are built here, never passed through from a caller;
* cursors are bound to the resource, filters, site and caller.
"""
from __future__ import annotations

import hashlib

from .errors import ERPError, not_found
from .models import ERPContext, Page, make_cursor, normalize, now_utc, read_cursor
from .resources import RESOURCES, Resource
from .resources import get as get_resource

MAX_PDF = 5 * 1024 * 1024


class ERPReader:
    def __init__(self, cfg, transport, *, cursor_key: bytes, clock=now_utc):
        self.cfg = cfg
        self.transport = transport
        self.cursor_key = cursor_key
        self.clock = clock

    # -- scope ----------------------------------------------------------------------------------
    def _check_access(self, resource: Resource, ctx: ERPContext):
        if not ctx.allows(resource.name):
            raise ERPError("erp_permission_denied", f"This desk is not allowed to read {resource.name}.", status=403)
        if ctx.party_scope and resource.party_kind and resource.party_kind != ctx.party_scope[0]:
            # A customer-scoped case never sees suppliers' documents, and the reverse.
            raise not_found(resource.name.rstrip("s"))

    def _scope_filters(self, resource: Resource, ctx: ERPContext) -> list:
        out = []
        if resource.company_field:
            out.append([resource.company_field, "=", self.cfg.company])
        if resource.warehouse_field:
            out.append([resource.warehouse_field, "in", list(self.cfg.warehouses)])
        if ctx.party_scope and resource.party_field:
            out.append([resource.party_field, "=", ctx.party_scope[1]])
        if ctx.party_scope and resource.name == "prices":
            kind, party = ctx.party_scope
            if kind == "customer":
                out.append(["selling", "=", 1])
            else:
                out.append(["buying", "=", 1])
        return out

    def _visible(self, resource: Resource, doc: dict, ctx: ERPContext) -> bool:
        if resource.company_field and doc.get(resource.company_field) != self.cfg.company:
            return False
        if resource.warehouse_field and doc.get(resource.warehouse_field) not in self.cfg.warehouses:
            return False
        if ctx.party_scope:
            kind, party = ctx.party_scope
            if resource.party_field and doc.get(resource.party_field) != party:
                return False
            if resource.name == "prices":
                other = "supplier" if kind == "customer" else "customer"
                if doc.get(other) or (doc.get(kind) not in (None, "", party)):
                    return False
        return True

    def _query_filters(self, resource: Resource, query: dict) -> list:
        out = []
        for key, value in (query or {}).items():
            if value is None or value == "":
                continue
            if key in resource.filters:
                out.append([key, "=", value])
            elif key == "search" and resource.search_field:
                text = str(value).replace("%", "").strip()[:100]
                if text:
                    out.append([resource.search_field, "like", f"%{text}%"])
            elif key == "ids":
                ids = [str(v) for v in (value if isinstance(value, (list, tuple)) else [value])][:100]
                out.append(["name", "in", ids])
            elif key == "date_from" and resource.date_field:
                out.append([resource.date_field, ">=", str(value)[:10]])
            elif key == "date_to" and resource.date_field:
                out.append([resource.date_field, "<=", str(value)[:10]])
            elif key == "billed_receipt_row" and resource.name == "purchase_invoices":
                out.append(["Purchase Invoice Item", "pr_detail", "=", str(value)])
            elif key == "billed_order_row" and resource.name == "purchase_invoices":
                out.append(["Purchase Invoice Item", "po_detail", "=", str(value)])
            else:
                raise ERPError("invalid_filter", f"{resource.name} cannot be filtered by {key!r}. Filters: "
                               f"{', '.join(resource.filters + ('search', 'ids', 'date_from', 'date_to'))}.",
                               status=400)
        return out

    # -- reads ----------------------------------------------------------------------------------
    def list_records(self, resource: str, query: dict | None, ctx: ERPContext, *, cursor: str | None = None,
                     limit: int | None = None) -> Page:
        res = get_resource(resource)
        self._check_access(res, ctx)
        filters = self._scope_filters(res, ctx) + self._query_filters(res, query or {})
        size = max(1, min(int(limit or self.cfg.page_size), 100))
        binding = {"r": res.name, "f": filters, "s": self.cfg.site_fingerprint(), "p": ctx.principal,
                   "scope": list(ctx.party_scope) if ctx.party_scope else None}
        start = read_cursor(self.cursor_key, binding, cursor) if cursor else 0
        observed = self.clock()
        list_fields = [f for f in res.fields if f != "item_code" or res.doctype != "Item"]
        docs = self.transport.list_docs(res.doctype, filters=filters, fields=list_fields, start=start,
                                        limit=size + 1)
        records = [normalize(res, d, backend=self.transport.name, synthetic=self.cfg.synthetic,
                             observed_at=observed, full=False)
                   for d in docs[:size] if self._visible(res, d, ctx)]
        nxt = make_cursor(self.cursor_key, binding, start + size) if len(docs) > size else None
        return Page(records=records, next_cursor=nxt)

    def get_record(self, resource: str, record_id: str, ctx: ERPContext) -> dict:
        res = get_resource(resource)
        self._check_access(res, ctx)
        if not isinstance(record_id, str) or not record_id or len(record_id) > 140:
            raise not_found(res.name.rstrip("s"))
        doc = self.transport.get_doc(res.doctype, record_id)
        if doc is None or not self._visible(res, doc, ctx):
            raise not_found(res.name.rstrip("s"))
        return normalize(res, doc, backend=self.transport.name, synthetic=self.cfg.synthetic,
                         observed_at=self.clock())

    def get_print_pdf(self, resource: str, record_id: str, ctx: ERPContext,
                      print_format: str | None = None) -> dict:
        """The official print of a submitted Sales Invoice. The format comes
        from the server's configuration only."""
        if resource != "sales_invoices":
            raise ERPError("print_not_allowed", "Only submitted Sales Invoices can be printed here.", status=403)
        if print_format is not None and print_format != self.cfg.print_format:
            raise ERPError("print_not_allowed", "The print format is a server setting (print_format in "
                           "connectors/erpnext.yaml), not a request choice.", status=403)
        record = self.get_record(resource, record_id, ctx)
        if record["fields"].get("docstatus") != 1:
            raise not_found("submitted sales invoice")
        data = self.transport.print_pdf("Sales Invoice", record_id, self.cfg.print_format)
        if len(data) > MAX_PDF:
            raise ERPError("protocol_error", "The invoice print is larger than 5 MiB.", status=502)
        if not data.startswith(b"%PDF-"):
            raise ERPError("protocol_error", "The ERP did not return a PDF for the invoice print.", status=502)
        return {"resource": resource, "id": record_id, "content_type": "application/pdf", "bytes": data,
                "size": len(data), "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "print_format": self.cfg.print_format or "Standard", "backend": self.transport.name,
                "synthetic": self.cfg.synthetic, "observed_at": self.clock()}

    def all_records(self, resource: str, query: dict | None, ctx: ERPContext, *, max_records: int = 1000) -> list:
        """Follow cursors to the end, deduplicating by id (pagination is an
        observed traversal, not a snapshot)."""
        out, seen, cursor = [], set(), None
        while True:
            page = self.list_records(resource, query, ctx, cursor=cursor, limit=100)
            for r in page.records:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    out.append(r)
            if not page.next_cursor or len(out) >= max_records:
                return out
            cursor = page.next_cursor


__all__ = ["ERPReader", "ERPContext", "RESOURCES"]
