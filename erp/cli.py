"""`hubzoid-erp`: the ERP connector from the command line.

    hubzoid-erp configure HUB [--backend B] [--dataset D] [--file F] [--set KEY=JSON ...] --expected-revision N|absent
    hubzoid-erp sample install HUB [--dataset distributor-v1]
    hubzoid-erp doctor HUB
    hubzoid-erp read HUB --resource R [--id ID] [--filter KEY=VALUE ...] [--limit N] [--cursor C]
    hubzoid-erp print HUB --id SALES_INVOICE --output FILE
    hubzoid-erp draft show HUB --kind sales_order|purchase_invoice --name NAME
    hubzoid-erp draft find HUB --kind K --external-ref REF
    hubzoid-erp fake serve [--dataset distributor-v1] [--port 8719]
    hubzoid-erp bootstrap export|verify HUB, bootstrap apply HUB --manifest F --admin-credential-ref REF

Every command takes `--json` and prints the API object, or the error object
`{"detail": {code, message, ...}}`. Exit codes: 0 done, 1 failed, 2 invalid,
3 denied, 4 conflict, 5 unavailable, 6 an uncertain write that needs
reconciliation. Drafts are created and submitted only through the desk's
recorded operations (`hubzoid-desk draft` and `submit-request`), never here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import typer

from . import config as config_lib
from .errors import ERPError, exit_code
from .models import ERPContext
from .service import DATASETS_ROOT, ERPService

app = typer.Typer(help="Hubzoid ERP connector (ERPNext): configure, doctor, read and the sample dataset.",
                  no_args_is_help=True, add_completion=False)
sample_app = typer.Typer(help="The labelled synthetic dataset.", no_args_is_help=True)
fake_app = typer.Typer(help="The fake ERPNext site on loopback (synthetic test double).", no_args_is_help=True)
draft_app = typer.Typer(help="Read back drafts the desk created (read only).", no_args_is_help=True)
boot_app = typer.Typer(help="The administrator schema step (the unique operation id field).", no_args_is_help=True)
app.add_typer(sample_app, name="sample")
app.add_typer(fake_app, name="fake")
app.add_typer(draft_app, name="draft")
app.add_typer(boot_app, name="bootstrap")
HUB = typer.Argument(Path("."), help="The hub folder.")


def _out(data, as_json: bool, human=None):
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
    elif human:
        human(data)
    else:
        typer.echo(json.dumps(data, indent=2, default=str))


def _fail(exc: ERPError, as_json: bool):
    if as_json:
        typer.echo(json.dumps({"detail": exc.detail()}, indent=2, default=str))
    else:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
    raise typer.Exit(exit_code(exc))


def _service(hub: Path) -> ERPService:
    if not hub.is_dir():
        raise ERPError("hub_not_found", f"{hub} is not a hub folder.", status=400)
    return ERPService.for_hub(hub.resolve())


def _ctx() -> ERPContext:
    import getpass
    import socket

    return ERPContext(hub_id="cli", principal=f"cli:{getpass.getuser()}@{socket.gethostname()}")


@app.command()
def configure(hub: Path = HUB, backend: str = typer.Option(None, "--backend"),
              dataset: str = typer.Option(None, "--dataset"), file: Path = typer.Option(None, "--file"),
              set_: list[str] = typer.Option(None, "--set", help="KEY=JSON_VALUE, repeatable"),
              site_url: str = typer.Option(None, "--site-url"),
              expected_revision: str = typer.Option(None, "--expected-revision",
                                                    help="The revision you read, or 'absent' for a first apply"),
              check: bool = typer.Option(False, "--check", help="Validate and show, change nothing"),
              as_json: bool = typer.Option(False, "--json")):
    """Validate and apply connectors/erpnext.yaml (a new revision)."""
    try:
        svc = _service(hub)
        current = config_lib.current(svc.hub_dir, svc.state_dir)
        if file is not None:
            cfg = config_lib.parse(file.read_text(encoding="utf-8"), str(file))
        else:
            base = current.config if current else config_lib.ERPConfig.model_validate(config_lib.SAMPLE_DEFAULT)
            changes = {}
            if backend:
                changes["backend"] = backend
            if dataset:
                changes["dataset"] = dataset
            if site_url:
                changes["site_url"] = site_url
            for item in set_ or []:
                key, _, raw = item.partition("=")
                try:
                    changes[key.strip()] = json.loads(raw)
                except json.JSONDecodeError:
                    changes[key.strip()] = raw
            cfg = config_lib.with_changes(base, changes)
        if check:
            return _out({"valid": True, "config": cfg.model_dump(mode="json"), "digest": cfg.digest(),
                         "current_revision": current.revision if current else None}, as_json)
        expected = expected_revision
        if expected is None:
            raise ERPError("validation_failed", "Pass --expected-revision (the revision you read, or 'absent').",
                           status=422, current_revision=current.revision if current else None)
        if current is not None and expected == "absent":
            raise ERPError("revision_conflict", "A configuration is already applied.", status=409,
                           current_revision=current.revision)
        applied = config_lib.apply(svc.hub_dir, svc.state_dir, cfg,
                                   expected_revision="absent" if expected == "absent" and current is None
                                   else int(expected), actor="cli")
        _out({"revision": applied.revision, "digest": applied.digest, "config": cfg.model_dump(mode="json")},
             as_json, lambda d: typer.echo(f"Applied revision {d['revision']}: backend {cfg.backend}, company "
                                           f"{cfg.company}, writes {'enabled' if cfg.writes_enabled else 'disabled'}."))
    except ERPError as exc:
        _fail(exc, as_json)


@app.command()
def doctor(hub: Path = HUB, as_json: bool = typer.Option(False, "--json")):
    """Every ERP setup problem at once, each with its next step."""
    try:
        result = _service(hub).doctor()
    except ERPError as exc:
        _fail(exc, as_json)

    def human(d):
        for c in d["checks"]:
            typer.echo(f"[{c['status']:>4}] {c['id']}: {c['summary']}")
            if c.get("recovery") and c["status"] in ("fail", "warn"):
                typer.echo(f"       next: {c['recovery']}")
        typer.echo(f"ready_read={d['ready_read']} ready_write={d['ready_write']} ready_submit={d['ready_submit']}")
    _out(result, as_json, human)
    if not result["ready_read"]:
        raise typer.Exit(1)


@sample_app.command("install")
def sample_install(hub: Path = HUB, dataset: str = typer.Option("distributor-v1", "--dataset"),
                   as_json: bool = typer.Option(False, "--json")):
    """Copy the immutable dataset into raw_data/synthetic/<dataset>/. A repeat
    with the same bytes is a no-op; changed local files are never overwritten."""
    try:
        if dataset not in config_lib.DATASETS:
            raise ERPError("config_invalid", f"Unknown dataset {dataset!r}.", status=422)
        src = DATASETS_ROOT / dataset
        dst = hub.resolve() / "raw_data" / "synthetic" / dataset
        manifest = json.loads((src / "manifest.json").read_text())
        if dst.exists():
            changed = []
            for rel, digest in manifest["files"].items():
                path = dst / rel
                if not path.is_file() or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    changed.append(rel)
            if changed:
                raise ERPError("sample_modified", f"{len(changed)} sample files differ from {dataset} (for example "
                               f"{changed[0]}). They were left unchanged. Move them aside to reinstall.", status=409,
                               files=changed[:20])
            status = "unchanged"
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            status = "installed"
        manifest_hash = "sha256:" + hashlib.sha256((src / "manifest.json").read_bytes()).hexdigest()
        _out({"dataset": dataset, "status": status, "path": str(dst), "manifest_sha256": manifest_hash,
              "files": len(manifest["files"]), "synthetic": True}, as_json,
             lambda d: typer.echo(f"SYNTHETIC DATA: {dataset} {d['status']} at {d['path']} ({d['files']} files)."))
    except ERPError as exc:
        _fail(exc, as_json)


@app.command()
def read(hub: Path = HUB, resource: str = typer.Option(..., "--resource"), id: str = typer.Option(None, "--id"),
         filter_: list[str] = typer.Option(None, "--filter", help="KEY=VALUE, repeatable"),
         limit: int = typer.Option(25, "--limit"), cursor: str = typer.Option(None, "--cursor"),
         as_json: bool = typer.Option(False, "--json")):
    """Read one record or a page of records (allowlisted fields only)."""
    try:
        reader = _service(hub).reader()
        if id:
            return _out(reader.get_record(resource, id, _ctx()), as_json)
        query = dict(f.split("=", 1) for f in (filter_ or []))
        page = reader.list_records(resource, query, _ctx(), cursor=cursor, limit=limit)
        _out({"records": page.records, "next_cursor": page.next_cursor}, as_json)
    except ERPError as exc:
        _fail(exc, as_json)


@app.command("print")
def print_cmd(hub: Path = HUB, id: str = typer.Option(..., "--id"), output: Path = typer.Option(..., "--output"),
              as_json: bool = typer.Option(False, "--json")):
    """Save the official print of a submitted Sales Invoice."""
    try:
        pdf = _service(hub).reader().get_print_pdf("sales_invoices", id, _ctx())
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(pdf.pop("bytes"))
        _out(dict(pdf, path=str(output)), as_json)
    except ERPError as exc:
        _fail(exc, as_json)


@draft_app.command("show")
def draft_show(hub: Path = HUB, kind: str = typer.Option(..., "--kind"), name: str = typer.Option(..., "--name"),
               as_json: bool = typer.Option(False, "--json")):
    try:
        _out(_service(hub).writer().get(kind, name), as_json)
    except ERPError as exc:
        _fail(exc, as_json)


@draft_app.command("find")
def draft_find(hub: Path = HUB, kind: str = typer.Option(..., "--kind"),
               external_ref: str = typer.Option(..., "--external-ref"), as_json: bool = typer.Option(False, "--json")):
    """Every document one operation created (for reconciliation)."""
    try:
        _out({"documents": _service(hub).writer().find(kind, external_ref)}, as_json)
    except ERPError as exc:
        _fail(exc, as_json)


@fake_app.command("serve")
def fake_serve(dataset: str = typer.Option("distributor-v1", "--dataset"), port: int = typer.Option(8719, "--port"),
               state_dir: Path = typer.Option(None, "--state-dir")):
    """Serve the synthetic fake ERPNext site on 127.0.0.1."""
    from .fake_server import main

    args = ["--dataset-dir", str(DATASETS_ROOT / dataset), "--port", str(port)]
    if state_dir:
        args += ["--state-dir", str(state_dir)]
    raise typer.Exit(main(args))


@boot_app.command("export")
def boot_export(hub: Path = HUB, output: Path = typer.Option(..., "--output"), as_json: bool = typer.Option(False, "--json")):
    from .bootstrap import manifest

    m = manifest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(m, indent=2) + "\n")
    _out({"path": str(output), "digest": m["digest"], "fields": len(m["fields"])}, as_json)


@boot_app.command("verify")
def boot_verify(hub: Path = HUB, as_json: bool = typer.Option(False, "--json")):
    from .bootstrap import inspect

    try:
        transport = _service(hub).transport()
        _out({"fields": inspect(transport)}, as_json)
    except ERPError as exc:
        _fail(exc, as_json)


@boot_app.command("apply")
def boot_apply(hub: Path = HUB, manifest_file: Path = typer.Option(..., "--manifest"),
               admin_credential_ref: str = typer.Option(..., "--admin-credential-ref"),
               as_json: bool = typer.Option(False, "--json")):
    """Create the two custom fields with an ERP administrator's credential.
    The credential is a reference (env: or secret:), never a value."""
    from .bootstrap import apply
    from .http import FrappeHTTP

    try:
        svc = _service(hub)
        cfg = svc.config()
        if cfg.backend == "sample":
            raise ERPError("bootstrap_not_needed", "The sample backend needs no schema change.", status=400)
        token = svc.secret_resolver(admin_credential_ref)
        if not token:
            raise ERPError("secret_missing", f"{admin_credential_ref} is not set.", status=503)
        http = FrappeHTTP(cfg.origin, credential=token, timeout=cfg.timeout_seconds, read_attempts=1,
                          loopback_only=cfg.backend == "fake_rest", private_cidrs=cfg.private_network_cidrs)
        m = json.loads(manifest_file.read_text())
        _out({"results": apply(http, expected_digest=m.get("digest", ""))}, as_json)
    except ERPError as exc:
        _fail(exc, as_json)


def main():
    app()


if __name__ == "__main__":
    sys.exit(main())
