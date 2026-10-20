"""Desk doctor: every setup problem at once, each with the next step.

Model access is reported on its own: the sample, the rules and the ERP can be
checked and inspected without a model, and only extraction needs one.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import yaml


def _c(cid, status, summary, recovery=None, **details):
    out = {"id": cid, "status": status, "summary": summary}
    if recovery:
        out["recovery"] = recovery
    if details:
        out["details"] = details
    return out


def check_rules(hub: Path) -> dict:
    from erp.service import ERPService

    from . import policy
    from .service import Desk
    if not Desk(hub).erp_enabled():
        try:
            rules = Desk(hub).record_rules()
            return _c("desk.rules", "ok", "ERP-free record rules valid.", digest=rules.digest())
        except Exception as exc:
            return _c("desk.rules", "fail", str(exc), "Review knowledge/record-rules.md.")

    try:
        company = ERPService.for_hub(hub).config().company
    except Exception:  # noqa: BLE001
        company = "Synthetic Meridian Distribution"
    try:
        r = policy.load(hub, company)
    except policy.PolicyError as exc:
        return _c("desk.rules", "fail", exc.message, "Fix the file and line named, then hubzoid-desk rules check .",
                  code=exc.code, rows=exc.rows)
    return _c("desk.rules", "ok", f"Rules valid: {len(r.order['rule_ids'])} order rules, {len(r.invoice['rule_ids'])} "
              f"invoice rules, {len(r.aliases)} alias rows, {len(r.unit_words)} unit words.", digest=r.digest)


def check_erp(hub: Path) -> list[dict]:
    from .service import Desk
    if not Desk(hub).erp_enabled():
        return [_c("desk.erp", "info", "ERP addon is off. Records, review and all four reports need no ERP configuration.")]
    from erp.service import ERPService

    try:
        d = ERPService.for_hub(hub).doctor(probe_resources=True)
    except Exception as exc:  # noqa: BLE001
        return [_c("desk.erp", "fail", f"ERP check failed ({type(exc).__name__}).", "hubzoid-erp doctor . for detail")]
    failing = [c for c in d["checks"] if c["status"] == "fail"]
    label = "SYNTHETIC DATA, " if d.get("synthetic") else ""
    out = [_c("desk.erp", "fail" if not d["ready_read"] else "ok",
              f"{label}ERP backend {d.get('backend')}: ready_read {d['ready_read']}, ready_write {d['ready_write']}, "
              f"ready_submit {d['ready_submit']}." + (f" {failing[0]['summary']}" if failing else ""),
              failing[0].get("recovery") if failing else None, checks=d["checks"])]
    return out


def check_write_mode(hub: Path) -> dict:
    from .service import Desk
    if not Desk(hub).erp_enabled():
        return _c("desk.write_mode", "info", "Inbox-only mode hides ERP writes.")
    from erp.service import ERPService

    try:
        from hubzoid.work_items import config as wi

        mode = wi.mode_for(wi.load(hub), "erp.submit")
    except Exception as exc:  # noqa: BLE001
        return _c("desk.write_mode", "fail", f"work-items.yaml is not valid ({getattr(exc, 'message', exc)}).",
                  "Fix work-items.yaml (hubzoid work-items config .)")
    try:
        erp = ERPService.for_hub(hub).doctor(probe_resources=False)
    except Exception:  # noqa: BLE001
        erp = {"ready_write": False, "ready_submit": False, "backend": "?"}
    if mode == "off":
        return _c("desk.write_mode", "info", "erp.submit is off: the desk prepares proposals and writes nothing to "
                  "the ERP.", "When doctor shows ready_write, set erp.submit: draft in work-items.yaml.")
    if not erp.get("ready_write"):
        return _c("desk.write_mode", "fail", f"erp.submit is {mode} but the ERP connection is not ready for writes.",
                  "Run hubzoid-erp doctor . and fix each failing check, or set erp.submit back to off.")
    if mode == "approve" and not erp.get("ready_submit"):
        return _c("desk.write_mode", "fail", "erp.submit is approve but no submit credential is ready.",
                  "Set submit_credential_ref in connectors/erpnext.yaml, or use draft mode.")
    note = " Submission waits for the requester's approval of the exact draft." if mode == "approve" else \
        " A person submits each draft in the ERP."
    return _c("desk.write_mode", "ok", f"erp.submit is {mode} on the {erp.get('backend')} backend.{note}")


def check_safety(hub: Path) -> dict:
    path = hub / "hitl.yaml"
    if not path.is_file():
        return _c("desk.approval_card", "fail", "hitl.yaml is missing, so the chat approval tool would run without a "
                  "card.", "Restore hitl.yaml from the hub (desk_approve_submission: {ask: always}).")
    data = yaml.safe_load(path.read_text()) or {}
    tools = data.get("tools") or {}
    bad = [t for t in ("desk_approve_submission", "desk_deny_submission")
           if (tools.get(t) or {}).get("ask") != "always" or (tools.get(t) or {}).get("allow_always")]
    if bad:
        return _c("desk.approval_card", "fail", f"{', '.join(bad)} must be ask: always without allow_always.",
                  "Set them back in hitl.yaml.")
    return _c("desk.approval_card", "ok", "Submission approvals in chat need a person's card every time.")


def check_parser() -> dict:
    if importlib.util.find_spec("docling") is None:
        return _c("desk.parser", "warn", "Docling is not installed: email text and spreadsheets work, PDFs and images "
                  "do not.", "pip install 'order-invoice-desk-hub[parsing]', then hubzoid-desk parser prefetch")
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    models = list(cache.glob("models--docling-project--*")) if cache.is_dir() else []
    if not models:
        return _c("desk.parser", "warn", "Docling's model assets are not on this machine yet.",
                  "hubzoid-desk parser prefetch")
    return _c("desk.parser", "ok", f"Docling installed with {len(models)} model sets cached. Parsing runs offline, one "
              "document at a time.")


def check_model(hub: Path) -> dict:
    defn = hub / "agents" / "desk-extractor" / "AGENTS.md"
    if not defn.is_file():
        return _c("desk.model", "fail", "The desk-extractor station is missing.", "Restore agents/desk-extractor/.")
    from hubzoid.stations.definitions import load
    try:
        station = load(hub, "desk-extractor")
        from shutil import which
        command = "codex" if station.engine == "codex-local" else "claude"
        available = bool(which(command))
        return _c("desk.model", "ok" if available else "warn", f"Extractor uses {station.engine}; {command} installed: {available}. Login still needs runtime qualification.",
                  "hubzoid stations doctor .")
    except Exception as exc:
        return _c("desk.model", "warn", f"Extractor configuration needs verification ({type(exc).__name__}).", "hubzoid stations doctor .")


def check_engine() -> dict:
    flags = [os.environ.get(k, "") for k in ("HUBZOID_SCHEDULES", "HUBZOID_GATEWAY")]
    if not any(v.strip().lower() in ("1", "true", "yes", "on") for v in flags):
        return _c("desk.engine", "warn", "HUBZOID_SCHEDULES is not set in this shell, so a standalone hub started from "
                  "here runs no desk workflows: messages wait in the queue.",
                  "Add HUBZOID_SCHEDULES=1 to the hub's .env (a gateway sets it for you), then restart hubzoid run.")
    return _c("desk.engine", "ok", "Desk workflows run on this box (HUBZOID_SCHEDULES).")


def check_channels(hub: Path) -> dict:
    files = sorted((hub / "channels").glob("*.yaml")) if (hub / "channels").is_dir() else []
    desk = []
    for f in files:
        try:
            data = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError:
            continue
        if data.get("workflow") == "order_invoice_intake":
            desk.append(f"{data.get('id')} ({data.get('provider')}{', on' if data.get('enabled') else ', off'})")
    if not desk:
        return _c("desk.intake", "info", "No intake channel starts the desk yet. Uploads still work.",
                  "hubzoid-desk sample install . for the sample, or add channels/<id>.yaml with workflow: "
                  "order_invoice_intake.")
    return _c("desk.intake", "ok", "Intake channels: " + ", ".join(desk) + ".")


def _load_env(hub: Path) -> None:
    """Read only the HUBZOID_SCHEDULES and HUBZOID_GATEWAY names from the hub's .env, as `hubzoid run` would."""
    env = hub / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("HUBZOID_SCHEDULES", "HUBZOID_GATEWAY") and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def check_sdk(hub: Path) -> dict:
    import json
    import subprocess
    import hubzoid
    lock = hub / "hub.lock.json"
    if not lock.is_file():
        return _c("desk.sdk", "warn", "No SDK lock. Use the tested immutable SDK revision before qualification.")
    expected = json.loads(lock.read_text())["sdk"]["commit"]
    package = Path(hubzoid.__file__).resolve().parent
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=package, capture_output=True, text=True)
    actual = result.stdout.strip() if result.returncode == 0 else None
    return _c("desk.sdk", "ok" if actual == expected else "fail", "Installed SDK matches the lock." if actual == expected
              else "Installed SDK revision differs from hub.lock.json or cannot be verified.",
              "Install the exact tested SDK commit, then rerun doctor. A moving branch is not a qualification pin.",
              expected=expected, actual=actual)


def run(hub: Path) -> dict:
    hub = Path(hub)
    _load_env(hub)
    checks = [check_sdk(hub), check_rules(hub), *check_erp(hub), check_write_mode(hub), check_safety(hub), check_engine(),
              check_channels(hub), check_parser(), check_model(hub)]
    return {"checks": checks, "ok": not any(c["status"] == "fail" for c in checks)}
