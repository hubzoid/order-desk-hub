"""Transports: the raw document access each backend gives.

A read transport (`ReadTransport`) lists and gets Frappe documents and
downloads a print. A write transport (`WriteTransport`) has exactly two
operations: insert one document of an allowed DocType, and submit one exact
document. Everything above them (allowlists, scope, deduplication, digests,
version checks) lives in `erp.read` and `erp.write`, so every backend obeys
the same rules.

* `sample`: the immutable dataset plus a local state file for drafts the
  desk saves. No network.
* `fake_rest`: the real Frappe REST client against `erp.fake_server` on
  loopback. Labelled synthetic.
* `frappe_rest`: the real Frappe REST client against a live site.
"""
from __future__ import annotations

from typing import Protocol


class ReadTransport(Protocol):
    name: str

    def list_docs(self, doctype: str, *, filters: list, fields: list[str], start: int, limit: int) -> list[dict]: ...

    def get_doc(self, doctype: str, name: str) -> dict | None: ...

    def print_pdf(self, doctype: str, name: str, print_format: str | None) -> bytes: ...

    def logged_user(self) -> str: ...

    def versions(self) -> dict: ...


class WriteTransport(Protocol):
    def insert(self, doctype: str, doc: dict) -> dict: ...

    def submit(self, doc: dict) -> dict: ...


def build(cfg, *, state_dir, data_root, credential_resolver=None, submit=False, resolver=None, sleep=None):
    """The transport for an applied configuration. `submit=True` resolves the
    separate submit credential, and only the approved submit asks for it."""
    from ..errors import ERPError

    if cfg.backend == "sample":
        from .sample import SampleTransport

        return SampleTransport(data_root / cfg.dataset, state_dir)
    from .frappe_rest import FrappeTransport

    ref = cfg.submit_credential_ref if submit else cfg.credential_ref
    credential = None
    if cfg.backend == "fake_rest":
        # The fake server is a labelled local test double. Its token is a
        # published fixture value, not a secret (erp/fake_server.py).
        from ..fake_server import FAKE_TOKEN

        credential = FAKE_TOKEN
        if ref and credential_resolver is not None:
            credential = credential_resolver(ref) or FAKE_TOKEN
    else:
        if not ref:
            raise ERPError("secret_missing", "submit_credential_ref is not set. Submission needs its own "
                           "credential, set by a credential administrator." if submit else
                           "credential_ref is not set.", status=503)
        if credential_resolver is None:
            raise ERPError("secret_missing", "No credential resolver is available here.", status=503)
        credential = credential_resolver(ref)
        if not credential:
            raise ERPError("secret_missing", f"The credential {ref} is not set. A credential administrator sets "
                           f"it with `hubzoid secrets set` (never in chat or a file).", status=503,
                           credential_ref=ref)
    kwargs = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return FrappeTransport(cfg, credential=credential, resolver=resolver, **kwargs)
