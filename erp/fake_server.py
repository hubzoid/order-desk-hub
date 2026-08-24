"""A deterministic fake ERPNext site on loopback, for tests and demos.

    hubzoid-erp fake serve --dataset distributor-v1 --port 8719

It speaks Frappe's REST wire format (the same paths, JSON shapes, error bodies
and status codes as a Frappe 15 site), backed by the same documents and rules
as the sample backend, so the real HTTP adapter runs unchanged against it.
It is a labelled synthetic test double: it binds to 127.0.0.1 only and
accepts one published fixture token, `FAKE_TOKEN`, which is not a secret.

Faults for the recovery tests are queued with `POST /__fake/faults`:
`{"faults": [{"op": "insert" | "submit" | "read", "fault": NAME, "seconds": N}]}`
where NAME is one of: status_401, status_403, status_429, status_500,
malformed_json, redirect, slow, disconnect_before_commit,
commit_then_disconnect, delayed_commit (answers 504 at once and commits
`seconds` later).
"""
from __future__ import annotations

import argparse
import json
import socket
import tempfile
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .backends.sample import SampleTransport
from .errors import ERPError

FAKE_TOKEN = "synthetic-fake-key:synthetic-fake-secret"
FAKE_USER = "fake-integration@synthetic-meridian.example"
#: The fake site has the bootstrap applied and no draft-save hooks.
CUSTOM_FIELDS = [{"name": f"{dt}-custom_hubzoid_operation_id", "dt": dt, "fieldname": "custom_hubzoid_operation_id",
                  "fieldtype": "Data", "unique": 1, "no_copy": 1, "read_only": 1, "length": 140}
                 for dt in ("Sales Order", "Purchase Invoice")]
HOOK_DOCTYPES = ("Server Script", "Webhook", "Notification", "Workflow", "Assignment Rule", "Auto Repeat")


def _enc(v):
    if isinstance(v, Decimal):
        return float(v) if v == v.to_integral_value() or len(str(v)) < 16 else str(v)
    raise TypeError


def _frappe_error(exc_type: str, message: str) -> dict:
    return {"exc_type": exc_type, "exception": f"frappe.exceptions.{exc_type}: {message}",
            "_server_messages": json.dumps([json.dumps({"message": message, "indicator": "red"})])}


class FakeSite:
    def __init__(self, dataset_dir: Path, state_dir: Path | None = None):
        self._tmp = None
        if state_dir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="hubzoid-fake-erp-")
            state_dir = Path(self._tmp.name)
        self.store = SampleTransport(dataset_dir, state_dir)
        self.faults: list[dict] = []
        self.lock = threading.Lock()
        self.requests: list[dict] = []

    def take_fault(self, op: str) -> dict | None:
        with self.lock:
            for i, f in enumerate(self.faults):
                if f.get("op") == op:
                    return self.faults.pop(i)
        return None


def make_handler(site: FakeSite):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FakeFrappe/15"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):   # quiet
            return

        # -- plumbing ------------------------------------------------------------------------
        def _send(self, status: int, body=None, *, content_type="application/json", raw: bytes | None = None,
                  headers: dict | None = None):
            data = raw if raw is not None else json.dumps(body, default=_enc).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _drop(self):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.close_connection = True

        def _authorized(self) -> bool:
            if self.headers.get("Authorization") != f"token {FAKE_TOKEN}":
                self._send(401, _frappe_error("AuthenticationError", "Invalid authentication token"))
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}", parse_float=Decimal) if n else {}

        def _apply_fault(self, fault: dict | None) -> bool:
            """True when the fault produced the whole answer."""
            if not fault:
                return False
            name = fault["fault"]
            if name == "slow":
                time.sleep(float(fault.get("seconds", 2)))
                return False
            if name.startswith("status_"):
                code = int(name.split("_")[1])
                exc = {401: "AuthenticationError", 403: "PermissionError", 429: "TooManyRequestsError",
                       500: "InternalServerError"}[code]
                headers = {"Retry-After": "1"} if code == 429 else None
                self._send(code, _frappe_error(exc, f"Injected {code}"), headers=headers)
                return True
            if name == "malformed_json":
                self._send(200, raw=b"{not json", content_type="application/json")
                return True
            if name == "redirect":
                self._send(302, raw=b"", headers={"Location": "https://elsewhere.example/api"})
                return True
            if name == "disconnect_before_commit":
                self._drop()
                return True
            return False

        # -- routes --------------------------------------------------------------------------
        def do_GET(self):
            parts = urlsplit(self.path)
            query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
            site.requests.append({"method": "GET", "path": parts.path})
            if not self._authorized():
                return
            if self._apply_fault(site.take_fault("read")):
                return
            path = unquote(parts.path)
            try:
                if path == "/api/method/frappe.auth.get_logged_user":
                    return self._send(200, {"message": FAKE_USER})
                if path == "/api/method/frappe.utils.change_log.get_versions":
                    return self._send(200, {"message": {"frappe": {"version": "15.0.0-fake"},
                                                        "erpnext": {"version": "15.0.0-fake"}}})
                if path == "/api/method/frappe.utils.print_format.download_pdf":
                    data = site.store.print_pdf(query.get("doctype", ""), query.get("name", ""), query.get("format"))
                    return self._send(200, raw=data, content_type="application/pdf")
                if path.startswith("/api/resource/"):
                    rest = path[len("/api/resource/"):]
                    doctype, _, name = rest.partition("/")
                    if doctype == "Custom Field":
                        filters = json.loads(query.get("filters") or "[]")
                        rows = [r for r in CUSTOM_FIELDS if _match(r, filters)]
                        return self._send(200, {"data": rows})
                    if doctype in HOOK_DOCTYPES:
                        return self._send(200, {"data": []})
                    if name:
                        doc = site.store.get_doc(doctype, name)
                        if doc is None:
                            return self._send(404, _frappe_error("DoesNotExistError", f"{doctype} {name} not found"))
                        return self._send(200, {"data": doc})
                    fields = json.loads(query.get("fields") or '["name"]')
                    filters = json.loads(query.get("filters") or "[]")
                    rows = site.store.list_docs(doctype, filters=filters, fields=[f for f in fields if f != "name"],
                                                start=int(query.get("limit_start") or 0),
                                                limit=min(int(query.get("limit_page_length") or 20), 500))
                    return self._send(200, {"data": rows})
            except ERPError as exc:
                return self._send(exc.status if exc.status < 500 else 417, _frappe_error("ValidationError", exc.message))
            self._send(404, _frappe_error("DoesNotExistError", "Not found"))

        def do_POST(self):
            parts = urlsplit(self.path)
            path = unquote(parts.path)
            site.requests.append({"method": "POST", "path": parts.path})
            if path == "/__fake/faults":
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self._send(403, {"error": "loopback only"})
                body = self._body()
                with site.lock:
                    site.faults.extend(body.get("faults") or [])
                return self._send(200, {"queued": len(site.faults)})
            if path == "/__fake/edit":
                body = self._body()
                doc = site.store.edit_draft(body["doctype"], body["name"], body.get("changes") or {})
                return self._send(200, {"data": doc})
            if path == "/__fake/reset":
                site.store.reset_state()
                return self._send(200, {"reset": True})
            if not self._authorized():
                return
            op = "submit" if path == "/api/method/frappe.client.submit" else "insert"
            fault = site.take_fault(op)
            if self._apply_fault(fault):
                return
            try:
                body = self._body()
                if path == "/api/method/frappe.client.submit":
                    result = site.store.submit(body.get("doc") or {})
                    key = "message"
                elif path.startswith("/api/resource/"):
                    doctype = path[len("/api/resource/"):]
                    if fault and fault["fault"] == "delayed_commit":
                        seconds = float(fault.get("seconds", 2))
                        threading.Timer(seconds, lambda: site.store.insert(doctype, body)).start()
                        return self._send(504, _frappe_error("GatewayTimeout", "Upstream timed out"))
                    result = site.store.insert(doctype, body)
                    key = "data"
                else:
                    return self._send(403, _frappe_error("PermissionError", "Not whitelisted"))
            except ERPError as exc:
                exc_type = exc.fields.get("exc_type") or {"duplicate_found": "UniqueValidationError",
                                                          "stale_preview": "TimestampMismatchError",
                                                          "not_found": "DoesNotExistError",
                                                          "write_not_allowed": "PermissionError"}.get(exc.code,
                                                                                                  "ValidationError")
                status = {"DoesNotExistError": 404, "PermissionError": 403}.get(exc_type, 417)
                return self._send(status, _frappe_error(exc_type, exc.message))
            if fault and fault["fault"] == "commit_then_disconnect":
                return self._drop()
            self._send(200, {key: result})

    return Handler


def _match(row, filters) -> bool:
    from .filters import matches

    return matches(row, filters)


class FakeServer:
    """Run the fake site in a background thread (tests) or the foreground (CLI)."""

    def __init__(self, dataset_dir: Path, *, port: int = 0, state_dir: Path | None = None):
        self.site = FakeSite(Path(dataset_dir), state_dir)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.site))
        self.httpd.daemon_threads = True
        self.thread: threading.Thread | None = None

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def start(self) -> "FakeServer":
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the fake ERPNext site (synthetic, loopback only).")
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--port", type=int, default=8719)
    ap.add_argument("--state-dir", default=None)
    args = ap.parse_args(argv)
    server = FakeServer(Path(args.dataset_dir), port=args.port,
                        state_dir=Path(args.state_dir) if args.state_dir else None)
    print(f"SYNTHETIC fake ERPNext site on {server.origin} (token: published fixture value, not a secret)",
          flush=True)
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
