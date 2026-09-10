"""`hubzoid-desk`: the Order & Invoice Desk from the command line.

    hubzoid-desk doctor HUB
    hubzoid-desk sample install HUB [--quarantine]    hubzoid-desk sample load HUB [--set extras|adversarial] [--event ID]
    hubzoid-desk bindings list|import|revoke HUB ...
    hubzoid-desk list HUB [--state S] [--mine]        hubzoid-desk show HUB CASE [--html FILE]
    hubzoid-desk claim|release HUB CASE --expected-revision N
    hubzoid-desk correct HUB CASE --expected-revision N --set PATH=VALUE ... | --file corrections.json
    hubzoid-desk rerun HUB CASE --reason TEXT
    hubzoid-desk draft HUB CASE --expected-revision N --digest sha256:...
    hubzoid-desk submit-request HUB CASE --expected-revision N --digest sha256:... --erp-version V
    hubzoid-desk reconcile HUB CASE                   hubzoid-desk cancel HUB CASE --expected-revision N --reason TEXT
    hubzoid-desk upload HUB FILE [--party Customer:ID] hubzoid-desk preview HUB FILE [--party ...]
    hubzoid-desk reassign HUB CASE --to EMAIL --expected-revision N --reason TEXT
    hubzoid-desk health HUB      hubzoid-desk rules check HUB      hubzoid-desk parser prefetch
    hubzoid-desk page HUB [--output FILE]

Every command takes `--json` (the same objects as the chat tools) and exits
0 done, 1 failed, 2 invalid, 3 denied, 4 conflict, 5 unavailable, 6 waiting
or uncertain. Actions a person takes (claim, correct, draft, submit-request,
cancel) run as the local owner in local owner mode. With sign-in on, a person
takes them in the chat or the Console, as themselves. Approving a submission
is never a desk command: it is the requester's decision in the Console, the
chat card, or `hubzoid operations approve`.
"""
from __future__ import annotations

import getpass
import json
import socket
import sys
import time
from pathlib import Path

import typer

from .service import Desk, DeskError
from .store import StoreError

app = typer.Typer(help="Order & Invoice Desk: cases from orders and supplier invoices to ERP drafts.",
                  no_args_is_help=True, add_completion=False)
sample_app = typer.Typer(help="The synthetic distributor sample.", no_args_is_help=True)
bind_app = typer.Typer(help="Sender bindings: which address or phone is which customer or supplier.",
                       no_args_is_help=True)
rules_app = typer.Typer(help="The desk's rule files.", no_args_is_help=True)
parser_app = typer.Typer(help="The document parser (Docling).", no_args_is_help=True)
app.add_typer(sample_app, name="sample")
app.add_typer(bind_app, name="bindings")
app.add_typer(rules_app, name="rules")
app.add_typer(parser_app, name="parser")
HUB = typer.Argument(Path("."), help="The hub folder.")
CASE = typer.Argument(..., help="The case number (CASE-000003) or id.")
EXIT = {400: 2, 404: 2, 413: 2, 422: 2, 401: 3, 403: 3, 409: 4, 429: 5, 503: 5}


def _desk(hub: Path) -> Desk:
    from . import sample_channels

    if not (hub / "AGENTS.md").is_file():
        raise DeskError("hub_not_found", f"{hub} is not a hub folder (no AGENTS.md).", status=400)
    sample_channels.register()
    return Desk(hub.resolve())


def _reader() -> str:
    return f"cli:{getpass.getuser()}@{socket.gethostname()}"


def _person(desk: Desk) -> str:
    return desk._local_owner()


def _out(data, as_json: bool, human=None):
    if as_json or human is None:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        human(data)


def _fail(exc, as_json: bool):
    detail = exc.detail() if hasattr(exc, "detail") else {"code": getattr(exc, "code", "failed"), "message": str(exc)}
    if as_json:
        typer.echo(json.dumps({"detail": detail}, indent=2, default=str))
    else:
        typer.echo(f"{detail['code']}: {detail['message']}", err=True)
    status = getattr(exc, "status", 500)
    raise typer.Exit(6 if detail.get("code") in ("erp_write_ambiguous",) else EXIT.get(status, 1))


def _run(as_json, fn, human=None):
    try:
        _out(fn(), as_json, human)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - every error has a code and a next step
        if hasattr(exc, "code"):
            _fail(exc, as_json)
        raise


def _table(data):
    for c in data["cases"]:
        q = len(c.get("questions") or [])
        typer.echo(f"{c['number']}  {c['state']:<20} {c.get('kind') or '':<17} {(c['party'] or {}).get('id') or '-':<13} "
                   f"{c.get('reference') or '':<14} {c.get('total') or '':>9}  {c.get('owner') or ''}"
                   + (f"  {q} question(s)" if q else ""))
    typer.echo("  ".join(f"{k}: {v}" for k, v in sorted(data["counts"].items())))


@app.command()
def doctor(hub: Path = HUB, as_json: bool = typer.Option(False, "--json")):
    """Every desk prerequisite at once: rules, ERP, write mode, channels, model access."""
    from .doctor import run

    def human(d):
        for c in d["checks"]:
            typer.echo(f"[{c['status']:>4}] {c['id']}: {c['summary']}")
            if c.get("recovery") and c["status"] in ("fail", "warn"):
                typer.echo(f"       next: {c['recovery']}")
    try:
        result = run(hub.resolve())
    except Exception as exc:  # noqa: BLE001
        _fail(exc, as_json)
    _out(result, as_json, human)
    if any(c["status"] == "fail" for c in result["checks"]):
        raise typer.Exit(1)


@sample_app.command("install")
def sample_install(hub: Path = HUB, quarantine: bool = typer.Option(False, "--quarantine",
                   help="Quarantine every sample message for a person to release in Intake"),
                   as_json: bool = typer.Option(False, "--json")):
    """Install the labelled synthetic sample: dataset, ERP sample config, sender bindings, two sample channels."""
    from erp import config as erp_config
    from erp.cli import sample_install as erp_sample_install  # noqa: F401

    from . import sample

    def go():
        d = _desk(hub)
        out = {"synthetic": True}
        if erp_config.current(d.hub_dir, d.erp.state_dir) is None:
            cfg = erp_config.ERPConfig.model_validate(erp_config.SAMPLE_DEFAULT)
            erp_config.apply(d.hub_dir, d.erp.state_dir, cfg, expected_revision="absent", actor="sample-install")
        import shutil

        from erp.service import DATASETS_ROOT
        dst = d.hub_dir / "raw_data" / "synthetic" / "distributor-v1"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(DATASETS_ROOT / "distributor-v1", dst)
        out["dataset"] = str(dst)
        out["bindings"] = sample.import_bindings(d, sample.dataset_dir(d) / "bindings.csv", actor="sample-install",
                                                 synthetic=True)
        out["channels"] = sample.install_channels(d, actor="sample-install",
                                                  trust="quarantine" if quarantine else "bound_senders")
        return out
    _run(as_json, go)


@sample_app.command("load")
def sample_load(hub: Path = HUB, which: str = typer.Option("order-invoice-desk", "--set"),
                event: list[str] = typer.Option(None, "--event"), as_json: bool = typer.Option(False, "--json")):
    """Send the sample messages in through the sample channels (the hub must be running)."""
    from . import sample

    def human(rows):
        for r in rows:
            typer.echo(f"{r['event']}: {r['channel']} {r['status']}{' (duplicate delivery)' if r['duplicate'] else ''}"
                       f", trust {r['trust']}")
        typer.echo("SYNTHETIC DATA. The running hub starts one intake work item per admitted message.")
    _run(as_json, lambda: sample.load(_desk(hub), which=which, events=event or None), human)


@bind_app.command("list")
def bindings_list(hub: Path = HUB, as_json: bool = typer.Option(False, "--json")):
    def go():
        d = _desk(hub)
        d.require(_reader(), "desk_manage", "Listing bindings")
        return {"bindings": d.store.bindings(d.erp.config().company)}
    _run(as_json, go)


@bind_app.command("import")
def bindings_import(hub: Path = HUB, file: Path = typer.Option(..., "--file"),
                    synthetic: bool = typer.Option(False, "--synthetic"), as_json: bool = typer.Option(False, "--json")):
    """Import company,channel_kind,sender,party_type,party_id,source_ref rows."""
    from . import sample

    _run(as_json, lambda: sample.import_bindings(_desk(hub), file, actor=_reader(), synthetic=synthetic))


@bind_app.command("revoke")
def bindings_revoke(hub: Path = HUB, channel_kind: str = typer.Option(..., "--channel-kind"),
                    sender: str = typer.Option(..., "--sender"), as_json: bool = typer.Option(False, "--json")):
    def go():
        d = _desk(hub)
        return {"revoked": d.store.revoke_binding(company=d.erp.config().company, channel_kind=channel_kind,
                                                  sender=sender, actor=_reader())}
    _run(as_json, go)


@app.command("list")
def list_cmd(hub: Path = HUB, state: str = typer.Option(None, "--state"), mine: bool = typer.Option(False, "--mine"),
             limit: int = typer.Option(50, "--limit"), as_json: bool = typer.Option(False, "--json")):
    """The queue, newest first."""
    def go():
        d = _desk(hub)
        actor = _person(d) if mine else _reader()
        return d.list(actor, state=state, mine=mine, limit=limit)
    _run(as_json, go, _table)


@app.command()
def show(hub: Path = HUB, case: str = CASE, html: Path = typer.Option(None, "--html", help="Also write the case page"),
         as_json: bool = typer.Option(False, "--json")):
    """One case with its sources, proposal, questions, ERP draft and history."""
    from . import render
    from .paths import output_dir

    def go():
        d = _desk(hub)
        view = d.show(_reader(), case)
        target = html or output_dir(d.hub_dir, "cases") / f"{view['number']}.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render.case_page(view), encoding="utf-8")
        view["page"] = str(target)
        return view

    def human(v):
        typer.echo(f"{v['number']} {v['state']} {v.get('kind') or ''} {(v['party'] or {}).get('id') or ''} "
                   f"ref {v.get('reference') or '-'} rev {v['revision']} proposal r{v.get('proposal_revision')} "
                   f"{v.get('digest') or ''}")
        for line in ((v.get("proposal") or {}).get("evidence") or {}).get("lines", []):
            src = (line.get("source") or {})
            typer.echo(f"  {line['n']}. [{src.get('loc')}] {src.get('text')}")
            if line.get("item") and line.get("amount"):
                typer.echo(f"     -> {line['item']['code']} {(line.get('unit') or {}).get('qty')} "
                           f"{(line.get('unit') or {}).get('uom')} @ {(line.get('rate') or {}).get('value')} = {line.get('amount')}")
            else:
                typer.echo(f"     -> {(line.get('item') or {}).get('code') or 'item'}: open question below")
        for q in v.get("questions") or []:
            typer.echo(f"  ? {q['message']} ({q['field']})")
        if v.get("erp"):
            typer.echo(f"  ERP {v['erp']['name']} docstatus {v['erp']['docstatus']} version {v['erp']['modified']} "
                       f"approval {v['erp']['approval_digest']}")
        typer.echo(f"  actions: {', '.join(v['actions']) or 'none'} · page {v['page']}")
    _run(as_json, go, human)


@app.command()
def claim(hub: Path = HUB, case: str = CASE, expected_revision: int = typer.Option(..., "--expected-revision"),
          as_json: bool = typer.Option(False, "--json")):
    def go():
        d = _desk(hub)
        return d.claim(_person(d), case, expected_revision=expected_revision)
    _run(as_json, go)


@app.command()
def release(hub: Path = HUB, case: str = CASE, expected_revision: int = typer.Option(..., "--expected-revision"),
            reason: str = typer.Option("", "--reason"), as_json: bool = typer.Option(False, "--json")):
    def go():
        d = _desk(hub)
        return d.release(_person(d), case, expected_revision=expected_revision, reason=reason)
    _run(as_json, go)


@app.command()
def reassign(hub: Path = HUB, case: str = CASE, to: str = typer.Option(..., "--to"),
             expected_revision: int = typer.Option(..., "--expected-revision"),
             reason: str = typer.Option(..., "--reason"), as_json: bool = typer.Option(False, "--json")):
    def go():
        d = _desk(hub)
        return d.reassign(_person(d), case, to=to, expected_revision=expected_revision, reason=reason)
    _run(as_json, go)


@app.command()
def correct(hub: Path = HUB, case: str = CASE, expected_revision: int = typer.Option(..., "--expected-revision"),
            set_: list[str] = typer.Option(None, "--set", help="PATH=VALUE, for example lines[1].item_code=SYN-ITEM-009"),
            file: Path = typer.Option(None, "--file", help="JSON: {fields: [{path, value, source_ref}]}"),
            as_json: bool = typer.Option(False, "--json")):
    """A case correction (a new proposal revision). Never a shared rule."""
    def go():
        d = _desk(hub)
        fields = []
        if file:
            fields = json.loads(file.read_text())["fields"]
        for item in set_ or []:
            path, _, value = item.partition("=")
            fields.append({"path": path.strip(), "value": value.strip()})
        if not fields:
            raise DeskError("validation_failed", "Give --set PATH=VALUE or --file.", status=422)
        return d.correct(_person(d), case, expected_revision=expected_revision, fields=fields)
    _run(as_json, go)


@app.command()
def rerun(hub: Path = HUB, case: str = CASE, reason: str = typer.Option(..., "--reason"),
          as_json: bool = typer.Option(False, "--json")):
    """Match the case's document again with the current rule files."""
    def go():
        d = _desk(hub)
        return d.rerun(_person(d), case, reason=reason)
    _run(as_json, go)


def _wait_case(d: Desk, case_id: str, until: tuple, seconds: float) -> dict:
    deadline = time.time() + seconds
    view = d.show(_reader(), case_id)
    while time.time() < deadline and view["state"] not in until:
        time.sleep(1.0)
        view = d.show(_reader(), case_id)
    return view


@app.command()
def draft(hub: Path = HUB, case: str = CASE, expected_revision: int = typer.Option(..., "--expected-revision"),
          digest: str = typer.Option(..., "--digest"), wait: int = typer.Option(60, "--wait"),
          as_json: bool = typer.Option(False, "--json")):
    """Save the case's proposal as an inert ERP draft (a recorded operation)."""
    def go():
        d = _desk(hub)
        out = d.request_draft(_person(d), case, expected_revision=expected_revision, digest=digest)
        view = _wait_case(d, out["case_id"], ("erp_draft_saved", "error", "ambiguous"), wait)
        return dict(out, state=view["state"], erp=view.get("erp"), error=view.get("error"))
    _run(as_json, go)


@app.command("submit-request")
def submit_request(hub: Path = HUB, case: str = CASE, expected_revision: int = typer.Option(..., "--expected-revision"),
                   digest: str = typer.Option(..., "--digest"), erp_version: str = typer.Option(None, "--erp-version"),
                   wait: int = typer.Option(30, "--wait"), as_json: bool = typer.Option(False, "--json")):
    """Ask to submit a saved ERP draft (approve mode). Then the requester approves the exact draft."""
    def go():
        from hubzoid.work_items import store as wi_store

        d = _desk(hub)
        out = d.request_submit(_person(d), case, expected_revision=expected_revision, digest=digest,
                               erp_version=erp_version)
        view = _wait_case(d, out["case_id"], ("waiting_approval", "done", "error", "ambiguous"), wait)
        ops = wi_store.list_ops(d.hub_dir, d.hub_id, item_id=out["work_item_id"])
        waiting = [o for o in ops if o["state"] == "awaiting_approval"]
        out = dict(out, state=view["state"])
        if waiting:
            out["approval"] = {"operation_id": waiting[0]["id"], "digest": waiting[0]["args_digest"],
                               "approve": f"hubzoid operations approve {d.hub_dir} {waiting[0]['id']} --digest "
                                          f"{waiting[0]['args_digest']}  (or the Console Work Items screen, or the "
                                          "chat card)"}
        return out
    try:
        _out(go(), as_json)
    except Exception as exc:  # noqa: BLE001
        if hasattr(exc, "code"):
            _fail(exc, as_json)
        raise
    raise typer.Exit(6)      # waiting for a person


@app.command()
def reconcile(hub: Path = HUB, case: str = CASE, as_json: bool = typer.Option(False, "--json")):
    """Read the ERP for an uncertain write. Writes nothing."""
    def go():
        d = _desk(hub)
        return d.reconcile(_person(d), case)
    _run(as_json, go)


@app.command()
def cancel(hub: Path = HUB, case: str = CASE, expected_revision: int = typer.Option(..., "--expected-revision"),
           reason: str = typer.Option(..., "--reason"), as_json: bool = typer.Option(False, "--json")):
    def go():
        d = _desk(hub)
        return d.cancel(_person(d), case, expected_revision=expected_revision, reason=reason)
    _run(as_json, go)


@app.command()
def upload(hub: Path = HUB, file: Path = typer.Argument(...), party: str = typer.Option(None, "--party"),
           note: str = typer.Option("", "--note"), as_json: bool = typer.Option(False, "--json")):
    """Send a file to the desk as a new case (the hub must be running)."""
    from .uploads import submit_path

    def go():
        d = _desk(hub)
        return submit_path(d, _person(d), file, party=party, note=note)
    _run(as_json, go)


@app.command()
def preview(hub: Path = HUB, file: Path = typer.Argument(...), party: str = typer.Option(None, "--party"),
            wait: int = typer.Option(300, "--wait"), as_json: bool = typer.Option(False, "--json")):
    """Parse, extract, match and validate a file without making a queue case or any ERP write."""
    from .uploads import submit_path

    def go():
        from hubzoid.work_items import store as wi_store

        d = _desk(hub)
        out = submit_path(d, _person(d), file, party=party, preview=True)
        deadline = time.time() + wait
        item = wi_store.get_item(d.hub_dir, out["work_item_id"])
        while time.time() < deadline and item["state"] not in wi_store.TERMINAL:
            time.sleep(2)
            item = wi_store.get_item(d.hub_dir, out["work_item_id"])
        result = (item.get("result_json") or {}).get("result") or {}
        case = d.store.get_case(result["case_id"]) if result.get("case_id") else None
        prop = d.store.proposal(case["id"]) if case else None
        return {"work_item": {"id": item["id"], "state": item["state"], "error": item.get("error")},
                "state": result.get("state"), "questions": result.get("questions"),
                "payload": (prop or {}).get("payload"), "evidence": (prop or {}).get("evidence"),
                "erp_writes": 0, "validation_level": "local_and_reads"}
    _run(as_json, go)


@app.command()
def health(hub: Path = HUB, as_json: bool = typer.Option(False, "--json")):
    """Queue health: per-state counts and ages, duplicates, uncertain writes, errors, ERP readiness."""
    _run(as_json, lambda: _desk(hub).health(_reader()))


@app.command()
def page(hub: Path = HUB, output: Path = typer.Option(None, "--output"), as_json: bool = typer.Option(False, "--json")):
    """Write the queue as an HTML page."""
    from . import render
    from .paths import output_dir

    def go():
        d = _desk(hub)
        target = output or output_dir(d.hub_dir) / "queue.html"
        target.write_text(render.queue_page(d.list(_reader(), limit=100)), encoding="utf-8")
        return {"page": str(target)}
    _run(as_json, go)


@rules_app.command("check")
def rules_check(hub: Path = HUB, as_json: bool = typer.Option(False, "--json")):
    """Validate the policy files, alias rows and unit words."""
    from . import policy

    def go():
        d = _desk(hub)
        try:
            r = d.rules()
        except policy.PolicyError as exc:
            raise DeskError(exc.code, exc.message, status=422, **{k: v for k, v in exc.detail().items()
                                                                 if k not in ("code", "message")}) from None
        return {"valid": True, "digest": r.digest, "aliases": len(r.aliases), "unit_words": len(r.unit_words),
                "order_rules": r.order["rule_ids"], "invoice_rules": r.invoice["rule_ids"]}
    _run(as_json, go)


@parser_app.command("prefetch")
def parser_prefetch(as_json: bool = typer.Option(False, "--json")):
    """Download Docling's layout, table and OCR model assets once, so parsing runs offline."""
    import os
    import subprocess

    def go():
        env = dict(os.environ)
        env.pop("HF_HUB_OFFLINE", None)
        done = subprocess.run([sys.executable, "-m", "desk.prefetch"], env=env, capture_output=True, text=True,
                              timeout=1800, cwd=str(Path(__file__).resolve().parents[1]))
        if done.returncode != 0:
            raise DeskError("parser_unavailable", (done.stderr or done.stdout).strip().splitlines()[-1][:300]
                            if (done.stderr or done.stdout).strip() else "prefetch failed", status=503)
        return json.loads(done.stdout.strip().splitlines()[-1])
    _run(as_json, go)


def main():
    app()


if __name__ == "__main__":
    main()
