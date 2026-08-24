"""The one HTTP path to a Frappe site, with the network rules in one place.

* The origin comes from the applied configuration only, never a model or a
  message. Requests go to fixed `/api/...` paths on that origin.
* Redirects are never followed, so a credential never leaves for another host.
* Every connection resolves the host itself and connects to an address it has
  just checked (loopback, link-local, metadata, multicast and private networks
  are refused for a live site unless an administrator listed the network), so
  DNS rebinding cannot swap the address after the check. TLS still verifies
  the site's own hostname.
* Responses are capped at 5 MiB and must be JSON (or a PDF for a print).
* Reads retry at most `read_attempts` times on 429, 502 to 504 and dropped
  connections, honouring Retry-After up to 60 seconds. Writes never retry
  here: the caller's operation ledger owns that decision.
* A write whose request left and whose answer is lost, 5xx or unreadable is
  `WriteUncertain`, never "failed".
"""
from __future__ import annotations

import ipaddress
import json
import random
import re
import socket
import threading
import time
from decimal import Decimal
from html import unescape
from typing import Callable

import httpcore
import httpx

from .errors import ERPError, NotDispatched, Refused, WriteUncertain

MAX_RESPONSE = 5 * 1024 * 1024
MAX_REQUEST = 1024 * 1024
MAX_CONCURRENT = 4
_METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


def _allowed(ip: ipaddress._BaseAddress, *, loopback_only: bool, private: list) -> bool:
    if loopback_only:
        return ip.is_loopback
    if ip in _METADATA or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified \
            or ip.is_reserved:
        return False
    if ip.is_private or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")):
        return any(ip in net for net in private)
    return True


class _PinnedBackend(httpcore.SyncBackend):
    """Resolve, check, then connect to the checked address."""

    def __init__(self, *, loopback_only: bool, private: list, resolver: Callable | None = None):
        super().__init__()
        self.loopback_only = loopback_only
        self.private = private
        self.resolver = resolver or socket.getaddrinfo

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = self.resolver(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise httpcore.ConnectError(f"could not resolve {host}") from None
        addresses = []
        for info in infos:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
            if not _allowed(ip, loopback_only=self.loopback_only, private=self.private):
                raise httpcore.ConnectError(f"{host} resolves to an address this connector may not reach")
            addresses.append(str(ip))
        if not addresses:
            raise httpcore.ConnectError(f"{host} has no address")
        return super().connect_tcp(addresses[0], port, timeout=timeout, local_address=local_address,
                                   socket_options=socket_options)


class _Transport(httpx.HTTPTransport):
    def __init__(self, backend: _PinnedBackend, verify=True):
        super().__init__(verify=verify, trust_env=False, retries=0)
        ssl_context = self._pool._ssl_context
        self._pool = httpcore.ConnectionPool(ssl_context=ssl_context, max_connections=MAX_CONCURRENT,
                                             max_keepalive_connections=MAX_CONCURRENT, retries=0,
                                             network_backend=backend)


def _strip(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def frappe_message(resp: httpx.Response) -> tuple[str, str]:
    """(exception type, a safe one-line message) from a Frappe error body.
    Tracebacks and SQL are never passed on."""
    exc_type, message = "", ""
    try:
        body = json.loads(resp.content[:MAX_RESPONSE])
    except (ValueError, UnicodeDecodeError):
        return "", f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        exc_type = str(body.get("exc_type") or "")[:80]
        raw = body.get("_server_messages")
        if raw:
            try:
                msgs = [json.loads(m).get("message", "") if isinstance(m, str) else "" for m in json.loads(raw)]
                message = "; ".join(_strip(m) for m in msgs if m)
            except (ValueError, AttributeError, TypeError):
                message = ""
        if not message and isinstance(body.get("message"), str):
            message = _strip(body["message"])
        if not message and body.get("exception"):
            message = _strip(str(body["exception"]).split(":", 1)[-1])
    message = re.sub(r"(?i)(traceback|select |insert |update |delete from).*", "", message)[:300].strip()
    return exc_type, message or f"HTTP {resp.status_code}"


class FrappeHTTP:
    """A bounded client for one site. `credential` is resolved by the caller
    just before the client is built and never logged."""

    def __init__(self, origin: str, *, credential: str | None, timeout: int, read_attempts: int,
                 loopback_only: bool, private_cidrs: list[str] = (), resolver: Callable | None = None,
                 sleep: Callable[[float], None] = time.sleep, verify=True):
        self.origin = origin.rstrip("/")
        self.read_attempts = read_attempts
        self.sleep = sleep
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)
        private = [ipaddress.ip_network(c, strict=False) for c in private_cidrs]
        backend = _PinnedBackend(loopback_only=loopback_only, private=private, resolver=resolver)
        headers = {"Accept": "application/json"}
        if credential:
            headers["Authorization"] = f"token {credential}"
        self.client = httpx.Client(base_url=self.origin, transport=_Transport(backend, verify=verify),
                                   timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
                                   follow_redirects=False, headers=headers, trust_env=False)

    def close(self):
        self.client.close()

    # -- low level ------------------------------------------------------------------------------
    def _read_body(self, resp: httpx.Response) -> bytes:
        size = 0
        chunks = []
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise ERPError("protocol_error", "The ERP answer was larger than 5 MiB.", status=502)
            chunks.append(chunk)
        return b"".join(chunks)

    def _check_redirect(self, resp: httpx.Response):
        if 300 <= resp.status_code < 400:
            raise ERPError("protocol_error", "The ERP answered with a redirect, which is never followed. "
                           "Set site_url to the site's final https origin.", status=502)

    def get(self, path: str, params: dict | None = None, *, raw: bool = False):
        """A read, retried within its limit. Returns parsed JSON (Decimals for
        numbers) or raw bytes."""
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._slots, self.client.stream("GET", path, params=params) as resp:
                    self._check_redirect(resp)
                    if resp.status_code in (429, 502, 503, 504) and attempt < self.read_attempts:
                        wait = self._retry_after(resp, attempt)
                        resp.close()
                        self.sleep(wait)
                        continue
                    body = self._read_body(resp)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if attempt < self.read_attempts:
                    self.sleep(self._backoff(attempt))
                    continue
                raise ERPError("erp_unavailable", f"The ERP did not answer ({type(exc).__name__}). Safe reads "
                               "can be retried.", status=503, retryable=True) from None
            return self._decode(resp, body, raw=raw)

    def _decode(self, resp, body: bytes, *, raw: bool):
        status = resp.status_code
        if status == 401:
            raise ERPError("erp_auth_failed", "The ERP refused the credential. Rotate the integration user's API "
                           "key with `hubzoid secrets set` and run doctor.", status=401)
        if status == 403:
            _, msg = frappe_message(httpx.Response(status, content=body))
            raise ERPError("erp_permission_denied", f"The integration user may not do this: {msg}. Ask the ERP "
                           "administrator for the narrow permission listed in docs/erpnext-setup.md.", status=403)
        if status == 404:
            raise ERPError("not_found", "No such record, or it is outside what this desk may see.", status=404)
        if status == 429:
            raise ERPError("erp_rate_limited", "The ERP is limiting requests. Try again shortly.", status=429,
                           retryable=True, retry_after=self._retry_after(resp, 1))
        if status >= 500:
            raise ERPError("erp_unavailable", f"The ERP failed with HTTP {status}. Safe reads can be retried.",
                           status=503, retryable=True)
        if status >= 400:
            exc_type, msg = frappe_message(httpx.Response(status, content=body))
            raise ERPError("erp_request_failed", f"The ERP refused the read: {msg}", status=502,
                           exc_type=exc_type or None)
        if raw:
            return body
        try:
            return json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeDecodeError):
            raise ERPError("protocol_error", "The ERP answer was not JSON.", status=502) from None

    def post(self, path: str, payload: dict):
        """One write. Never retried here. Raises NotDispatched (it never left),
        Refused (definitely not saved) or WriteUncertain (it may have saved)."""
        body = json.dumps(payload, default=_json_default, separators=(",", ":")).encode()
        if len(body) > MAX_REQUEST:
            raise NotDispatched("payload_too_large", "The document is larger than 1 MiB.", status=413)
        try:
            with self._slots, self.client.stream("POST", path, content=body,
                                                 headers={"Content-Type": "application/json"}) as resp:
                if 300 <= resp.status_code < 400:
                    raise WriteUncertain("The ERP answered a write with a redirect. The write may have "
                                         "applied. Reconcile before retrying.")
                try:
                    data = self._read_body(resp)
                except ERPError:
                    raise WriteUncertain("The ERP answer to the write was too large to read.") from None
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise NotDispatched("erp_unavailable", f"Could not connect to the ERP ({type(exc).__name__}). "
                                "Nothing was sent.", status=503, retryable=True) from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise WriteUncertain(f"The connection ended before the ERP answered ({type(exc).__name__}). "
                                 "The write may have applied. Reconcile before retrying.") from None
        status = resp.status_code
        if status >= 500:
            raise WriteUncertain(f"The ERP failed with HTTP {status} after the write was sent. It may have "
                                 "applied. Reconcile before retrying.")
        if status in (401, 403):
            _, msg = frappe_message(httpx.Response(status, content=data))
            raise Refused("erp_permission_denied" if status == 403 else "erp_auth_failed",
                          f"The ERP refused the write: {msg}", status=status)
        if status == 429:
            raise Refused("erp_rate_limited", "The ERP is limiting requests. Nothing was saved. Try again "
                          "shortly.", status=429, retryable=True)
        if status >= 400:
            exc_type, msg = frappe_message(httpx.Response(status, content=data))
            code = {"DuplicateEntryError": "duplicate_found", "UniqueValidationError": "duplicate_found",
                    "TimestampMismatchError": "stale_preview"}.get(exc_type, "erp_validation_failed")
            raise Refused(code, f"The ERP refused the document: {msg}", status=409 if code != "erp_validation_failed"
                          else 422, exc_type=exc_type or None)
        try:
            parsed = json.loads(data, parse_float=Decimal)
        except (ValueError, UnicodeDecodeError):
            raise WriteUncertain("The ERP answer to the write was not JSON. It may have applied.") from None
        return parsed

    def _retry_after(self, resp, attempt) -> float:
        value = resp.headers.get("retry-after")
        try:
            return min(60.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return self._backoff(attempt)

    def _backoff(self, attempt: int) -> float:
        return min(8.0, 0.5 * (2 ** (attempt - 1))) * (0.5 + random.random() / 2)


def _json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"{type(value).__name__} is not JSON")
