"""`connectors/erpnext.yaml`: the one source of truth for the ERP connection.

    version: 1
    backend: sample              # sample | fake_rest | frappe_rest
    dataset: distributor-v1      # sample and fake_rest only
    site_url: null               # fake_rest: http://127.0.0.1:PORT. frappe_rest: https://...
    credential_ref: null         # secret:hub/<hub>/<name> or env:<NAME>, never the value
    submit_credential_ref: null  # resolved only inside the approved submit
    company: Synthetic Meridian Distribution
    currency: USD
    warehouses: [SYN-MAIN - SMD]
    selling_price_list: SYN-RETAIL
    buying_price_list: SYN-BUYING
    writes_enabled: false
    timeout_seconds: 20
    read_attempts: 3
    page_size: 50
    private_network_cidrs: []
    qualified_apps: []           # extra Frappe apps an administrator has checked for draft-save effects
    print_format: null           # the Sales Invoice print format for invoice copies (server setting)
    local_test_site: false       # frappe_rest only: allow http://127.0.0.1:PORT for a disposable local test site

Unknown keys fail. Company, currency, price lists and warehouses are explicit
identifiers, never inferred from an incoming message. A file edit becomes
active only through `configure` (CLI or API), which validates it and records a
revision. A bad or unapplied file leaves the last applied configuration in
force, and doctor reports the drift.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import ERPError, config_invalid

FILE = Path("connectors") / "erpnext.yaml"
BACKENDS = ("sample", "fake_rest", "frappe_rest")
DATASETS = ("distributor-v1",)
_REF_RE = re.compile(r"^(secret:hub/[a-z0-9][a-z0-9_-]{0,63}/[a-z0-9][a-z0-9_.-]{0,127}|env:[A-Z_][A-Z0-9_]{0,127})$")
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


class ERPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    backend: Literal["sample", "fake_rest", "frappe_rest"] = "sample"
    dataset: str | None = "distributor-v1"
    site_url: str | None = None
    credential_ref: str | None = None
    submit_credential_ref: str | None = None
    company: str = Field(min_length=1, max_length=140)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    warehouses: list[str] = Field(min_length=1, max_length=20)
    selling_price_list: str = Field(min_length=1, max_length=140)
    buying_price_list: str = Field(min_length=1, max_length=140)
    writes_enabled: bool = False
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    read_attempts: int = Field(default=3, ge=1, le=3)
    page_size: int = Field(default=50, ge=1, le=100)
    private_network_cidrs: list[str] = Field(default_factory=list, max_length=20)
    qualified_apps: list[str] = Field(default_factory=list, max_length=20)
    print_format: str | None = Field(default=None, max_length=140)
    local_test_site: bool = False

    @field_validator("credential_ref", "submit_credential_ref")
    @classmethod
    def _ref(cls, v):
        if v is not None and not _REF_RE.match(v):
            raise ValueError("a credential reference is secret:hub/<hub>/<name> or env:<NAME>, never a value")
        return v

    @field_validator("private_network_cidrs")
    @classmethod
    def _cidrs(cls, v):
        for cidr in v:
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                raise ValueError(f"{cidr!r} is not a network like 10.20.0.0/16") from None
            if net.is_loopback or net.is_link_local or net.is_multicast or net.is_unspecified:
                raise ValueError(f"{cidr} is a loopback, link-local or special network and can never be allowed")
        return v

    @field_validator("qualified_apps")
    @classmethod
    def _apps(cls, v):
        for app in v:
            if not re.match(r"^[a-z][a-z0-9_]{0,63}$", app):
                raise ValueError(f"{app!r} is not a Frappe app name")
        return v

    @model_validator(mode="after")
    def _backend(self):
        if self.local_test_site and self.backend != "frappe_rest":
            raise ValueError("local_test_site is only for backend frappe_rest")
        if self.backend in ("sample", "fake_rest"):
            if self.dataset not in DATASETS:
                raise ValueError(f"backend {self.backend} needs dataset: one of {', '.join(DATASETS)}")
        elif self.dataset is not None:
            raise ValueError("dataset is only for the sample and fake_rest backends. Remove it for a live site")
        if self.backend == "sample":
            if self.site_url or self.credential_ref or self.submit_credential_ref:
                raise ValueError("the sample backend has no site_url or credentials")
            return self
        if not self.site_url:
            raise ValueError(f"backend {self.backend} needs site_url")
        parts = urlsplit(self.site_url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("site_url cannot carry a user, password, query or fragment")
        if parts.path not in ("", "/"):
            raise ValueError("site_url is the site's origin only, for example https://erp.example.com")
        host = (parts.hostname or "").lower()
        if self.backend == "fake_rest":
            if parts.scheme != "http" or host not in _LOOPBACK or not parts.port:
                raise ValueError("fake_rest runs on this machine: site_url is http://127.0.0.1:<port>")
        elif self.local_test_site:
            if parts.scheme != "http" or host not in _LOOPBACK or not parts.port:
                raise ValueError("local_test_site is a disposable ERPNext on this machine: site_url is "
                                 "http://127.0.0.1:<port>")
        else:
            if parts.scheme != "https":
                raise ValueError("a live ERPNext site_url must use https")
            if host in _LOOPBACK:
                raise ValueError("a live site cannot be on loopback. Use fake_rest for a local test server, or "
                                 "local_test_site: true for a disposable ERPNext test container")
            if not self.credential_ref:
                raise ValueError("backend frappe_rest needs credential_ref (the integration user's API key)")
        return self

    @property
    def origin(self) -> str | None:
        return self.site_url.rstrip("/") if self.site_url else None

    @property
    def synthetic(self) -> bool:
        return self.backend in ("sample", "fake_rest") or self.local_test_site

    def site_fingerprint(self) -> str:
        """Stable identity of the ERP site and company: changes when either does."""
        where = self.origin if self.backend != "sample" else f"sample:{self.dataset}"
        return "sha256:" + hashlib.sha256(f"{where}\n{self.company}".encode()).hexdigest()

    def digest(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


SAMPLE_DEFAULT = {
    "version": 1, "backend": "sample", "dataset": "distributor-v1", "site_url": None,
    "credential_ref": None, "submit_credential_ref": None,
    "company": "Synthetic Meridian Distribution", "currency": "USD", "warehouses": ["SYN-MAIN - SMD"],
    "selling_price_list": "SYN-RETAIL", "buying_price_list": "SYN-BUYING", "writes_enabled": False,
    "timeout_seconds": 20, "read_attempts": 3, "page_size": 50, "private_network_cidrs": [],
    "qualified_apps": [], "print_format": None, "local_test_site": False,
}


def _line_of(text: str, key: str) -> int | None:
    for n, line in enumerate(text.splitlines(), 1):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            return n
    return None


def parse(text: str, source: str = str(FILE)) -> ERPConfig:
    """Validate the file's text. Errors name the file, line and field, never a
    credential value."""
    try:
        data = yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise ERPError("config_invalid", f"{source}:{(mark.line + 1) if mark else '?'}: not valid YAML",
                       status=422, file=source) from None
    if not isinstance(data, dict):
        raise ERPError("config_invalid", f"{source}: the file must be a mapping starting with version: 1",
                       status=422, file=source)
    try:
        return ERPConfig.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "(file)"
        key = str(first["loc"][0]) if first["loc"] else ""
        line = _line_of(text, key) if key else None
        msg = first["msg"].removeprefix("Value error, ")
        where = f"{source}:{line}" if line else source
        raise ERPError("config_invalid", f"{where}: {loc}: {msg}", status=422, file=source, line=line,
                       field=loc, allowed=list(BACKENDS) if key == "backend" else None) from None


def dump(cfg: ERPConfig | dict) -> str:
    data = cfg.model_dump(mode="json") if isinstance(cfg, ERPConfig) else dict(cfg)
    ordered = {k: data[k] for k in SAMPLE_DEFAULT if k in data}
    return yaml.safe_dump(ordered, sort_keys=False, default_flow_style=None)


# -- applied revisions --------------------------------------------------------------------------

def _state_db(state_dir: Path) -> sqlite3.Connection:
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / "erp.sqlite", timeout=30, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS erp_config_revisions (revision INTEGER PRIMARY KEY, "
                 "digest TEXT NOT NULL, config_json TEXT NOT NULL, actor TEXT, applied_at REAL NOT NULL)")
    return conn


class Applied(BaseModel):
    revision: int
    config: ERPConfig
    digest: str
    file_digest: str | None
    drift: str | None = None      # why the file on disk is not what is in force


def file_path(hub_dir) -> Path:
    return Path(hub_dir) / FILE


def current(hub_dir, state_dir) -> Applied | None:
    """The configuration in force: the last applied revision. When nothing was
    ever applied, a valid file on disk is applied as revision 1 (a fresh hub)."""
    hub_dir, state_dir = Path(hub_dir), Path(state_dir)
    path = file_path(hub_dir)
    file_text = path.read_text(encoding="utf-8") if path.is_file() else None
    file_digest = "sha256:" + hashlib.sha256(file_text.encode()).hexdigest() if file_text is not None else None
    with closing(_state_db(state_dir)) as db:
        row = db.execute("SELECT revision, digest, config_json FROM erp_config_revisions "
                         "ORDER BY revision DESC LIMIT 1").fetchone()
    if row is None:
        if file_text is None:
            return None
        cfg = parse(file_text, str(FILE))
        return apply(hub_dir, state_dir, cfg, expected_revision="absent", actor="hub-load", write_file=False)
    cfg = ERPConfig.model_validate(json.loads(row[2]))
    drift = None
    if file_text is None:
        drift = "connectors/erpnext.yaml is missing. The last applied configuration stays in force."
    else:
        try:
            on_disk = parse(file_text, str(FILE))
            if on_disk.digest() != cfg.digest():
                drift = ("connectors/erpnext.yaml differs from the applied revision. Apply it with "
                         "`hubzoid-erp configure . --file connectors/erpnext.yaml --expected-revision "
                         f"{row[0]}`.")
        except ERPError as exc:
            drift = f"connectors/erpnext.yaml is invalid ({exc.message}). The last applied revision stays in force."
    return Applied(revision=row[0], config=cfg, digest=row[1], file_digest=file_digest, drift=drift)


def apply(hub_dir, state_dir, cfg: ERPConfig, *, expected_revision, actor: str,
          write_file: bool = True) -> Applied:
    """Record `cfg` as the next revision. `expected_revision` is the revision
    the caller read, or "absent" for a first apply. A mismatch is a 409."""
    hub_dir, state_dir = Path(hub_dir), Path(state_dir)
    with closing(_state_db(state_dir)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT MAX(revision) FROM erp_config_revisions").fetchone()
            latest = row[0]
            if expected_revision == "absent":
                if latest is not None:
                    raise ERPError("revision_conflict", "A configuration is already applied. Read it and pass "
                                   "--expected-revision.", status=409, current_revision=latest)
            elif expected_revision is None or int(expected_revision) != (latest or 0):
                raise ERPError("revision_conflict", "Someone changed the ERP configuration since you read it. "
                               "Reload, review the change and apply again.", status=409,
                               current_revision=latest or 0)
            revision = (latest or 0) + 1
            db.execute("INSERT INTO erp_config_revisions (revision, digest, config_json, actor, applied_at) "
                       "VALUES (?,?,?,?,?)", (revision, cfg.digest(), json.dumps(cfg.model_dump(mode="json")),
                                             actor, time.time()))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    text = dump(cfg)
    if write_file:
        path = file_path(hub_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return Applied(revision=revision, config=cfg, digest=cfg.digest(),
                   file_digest="sha256:" + hashlib.sha256(text.encode()).hexdigest())


def with_changes(cfg: ERPConfig, changes: dict) -> ERPConfig:
    data = cfg.model_dump(mode="json")
    for key, value in changes.items():
        if key not in SAMPLE_DEFAULT:
            raise config_invalid(key, f"unknown key {key!r}")
        data[key] = value
    if data.get("backend") == "frappe_rest" and "dataset" not in changes:
        data["dataset"] = None
    try:
        return ERPConfig.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise config_invalid(".".join(str(p) for p in first["loc"]) or "(file)",
                             first["msg"].removeprefix("Value error, ")) from None
