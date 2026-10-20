"""One object a hub uses to reach its ERP: configuration, reads, writes, doctor.

    erp = ERPService.for_hub(hub_dir)
    erp.reader().get_record("customers", "SYN-CUST-001", ctx)
    erp.writer().create_draft("sales_order", payload, external_ref)

The service owns where state lives (`<hub>/.hubzoid/data/erp/`), which
dataset a sample backend reads, how credentials resolve (the SDK's secret
store when present, never a file) and the cursor key. It never imports a
model runtime.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from pathlib import Path

from . import config as config_lib
from .backends import build
from .errors import ERPError
from .read import ERPReader

DATASETS_ROOT = Path(__file__).resolve().parent / "datasets"


def _sdk_secret_resolver(hub_dir: Path):
    def resolve(ref: str) -> str | None:
        try:
            from hubzoid import secretstore
        except ImportError:  # the module used outside a Hubzoid install: environment only
            return (os.environ.get(ref[4:]) or None) if ref.startswith("env:") else None
        try:
            return secretstore.resolve(ref, hub_dir)
        except Exception:  # noqa: BLE001 - an unset or unreadable secret is missing, never a crash
            return None
    return resolve


class ERPService:
    def __init__(self, hub_dir, state_dir, *, secret_resolver=None, deployment_id: str | None = None,
                 resolver=None, datasets_root: Path | None = None):
        self.hub_dir = Path(hub_dir)
        self.state_dir = Path(state_dir)
        self.secret_resolver = secret_resolver or _sdk_secret_resolver(self.hub_dir)
        self.resolver = resolver
        self.datasets_root = Path(datasets_root) if datasets_root else self._datasets_root()
        self.deployment_id = deployment_id or self._deployment_id()

    @classmethod
    def for_hub(cls, hub_dir, **kw) -> "ERPService":
        hub_dir = Path(hub_dir)
        from hubzoid.datadir import state

        return cls(hub_dir, state(hub_dir) / "data" / "erp", **kw)

    def _datasets_root(self) -> Path:
        installed = self.hub_dir / "raw_data" / "synthetic"
        return installed if installed.is_dir() else DATASETS_ROOT

    def _deployment_id(self) -> str:
        """A random id for this deployment's data folder, kept with the data,
        so a copied hub never reuses another's operation markers."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "deployment-id"
        if not path.is_file():
            with tempfile.NamedTemporaryFile(mode="w", dir=self.state_dir, prefix=".deployment-id-", delete=False) as f:
                f.write(secrets.token_hex(16))
                f.flush()
                os.fsync(f.fileno())
                tmp = Path(f.name)
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
            finally:
                tmp.unlink(missing_ok=True)
        return path.read_text().strip()

    def _cursor_key(self) -> bytes:
        return hashlib.sha256(("cursor\n" + self.deployment_id).encode()).digest()

    # -- configuration --------------------------------------------------------------------------
    def applied(self):
        applied = config_lib.current(self.hub_dir, self.state_dir)
        if applied is None:
            raise ERPError("config_missing", "This hub has no connectors/erpnext.yaml. Run `hubzoid-erp configure . "
                           "--backend sample --dataset distributor-v1` to start with the synthetic sample.",
                           status=503)
        return applied

    def config(self):
        return self.applied().config

    # -- clients ----------------------------------------------------------------------------------
    def transport(self, *, submit: bool = False):
        return build(self.config(), state_dir=self.state_dir, data_root=self.datasets_root,
                     credential_resolver=self.secret_resolver, submit=submit, resolver=self.resolver)

    def reader(self, transport=None) -> ERPReader:
        return ERPReader(self.config(), transport or self.transport(), cursor_key=self._cursor_key())

    def writer(self, transport=None):
        """The write contract. Imported here, so a read-only copy of this module
        (the Customer Desk's) imports this file without write.py."""
        from .write import DraftWriter

        cfg = self.config()
        rt = transport or self.transport()
        submit_factory = (lambda: self.transport(submit=True)) if cfg.backend == "frappe_rest" else None
        return DraftWriter(cfg, rt, submit_factory, deployment_id=self.deployment_id)

    def doctor(self, **kw) -> dict:
        from .doctor import run

        return run(self, **kw)
