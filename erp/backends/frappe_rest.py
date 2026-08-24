"""Frappe REST: the same client for the fake server and a live ERPNext site.

Reads: `GET /api/resource/<DocType>` and `/<name>`, and three whitelisted
methods (`frappe.auth.get_logged_user`, `frappe.utils.change_log.get_versions`,
`frappe.utils.print_format.download_pdf`). Writes: `POST /api/resource/<DocType>`
for Sales Order and Purchase Invoice only, and
`POST /api/method/frappe.client.submit`. No other method can be called.
"""
from __future__ import annotations

import json
from urllib.parse import quote

from ..errors import ERPError, NotDispatched
from ..http import FrappeHTTP
from ..resources import WRITE_KINDS

_WRITABLE = frozenset(WRITE_KINDS.values())


class FrappeTransport:
    def __init__(self, cfg, *, credential: str | None, resolver=None, sleep=None, verify=True):
        self.name = cfg.backend
        kwargs = {"sleep": sleep} if sleep is not None else {}
        self.http = FrappeHTTP(cfg.origin, credential=credential, timeout=cfg.timeout_seconds,
                               read_attempts=cfg.read_attempts, loopback_only=cfg.backend == "fake_rest",
                               private_cidrs=cfg.private_network_cidrs, resolver=resolver, verify=verify,
                               **kwargs)

    def close(self):
        self.http.close()

    # -- reads ----------------------------------------------------------------------------------
    def list_docs(self, doctype, *, filters, fields, start, limit):
        params = {"fields": json.dumps(["name", "modified", *fields]),
                  "filters": json.dumps(filters, default=str), "limit_start": start,
                  "limit_page_length": limit, "order_by": "name asc"}
        body = self.http.get(f"/api/resource/{quote(doctype)}", params)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise ERPError("protocol_error", "The ERP list answer had no data list.", status=502)
        return data

    def get_doc(self, doctype, name):
        try:
            body = self.http.get(f"/api/resource/{quote(doctype)}/{quote(str(name), safe='')}")
        except ERPError as exc:
            if exc.code == "not_found":
                return None
            raise
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ERPError("protocol_error", "The ERP record answer had no data object.", status=502)
        return data

    def print_pdf(self, doctype, name, print_format):
        params = {"doctype": doctype, "name": name, "no_letterhead": 1}
        if print_format:
            params["format"] = print_format
        return self.http.get("/api/method/frappe.utils.print_format.download_pdf", params, raw=True)

    def logged_user(self) -> str:
        body = self.http.get("/api/method/frappe.auth.get_logged_user")
        return str((body or {}).get("message") or "")

    def versions(self) -> dict:
        body = self.http.get("/api/method/frappe.utils.change_log.get_versions")
        message = (body or {}).get("message") or {}
        return {app: str((info or {}).get("version") or "") for app, info in message.items()} \
            if isinstance(message, dict) else {}

    # -- the two writes -------------------------------------------------------------------------
    def insert(self, doctype, doc):
        if doctype not in _WRITABLE:
            raise NotDispatched("write_not_allowed", f"{doctype} is not a document this desk may create.",
                                status=403)
        body = self.http.post(f"/api/resource/{quote(doctype)}", doc)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            from ..errors import WriteUncertain
            raise WriteUncertain("The ERP accepted the request but the answer had no document. It may have "
                                 "been saved.")
        return data

    def submit(self, doc):
        if doc.get("doctype") not in _WRITABLE:
            raise NotDispatched("write_not_allowed", "Only Sales Orders and Purchase Invoices can be submitted.",
                                status=403)
        body = self.http.post("/api/method/frappe.client.submit", {"doc": doc})
        data = body.get("message") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            from ..errors import WriteUncertain
            raise WriteUncertain("The ERP answer to the submit had no document. It may have been submitted.")
        return data
