"""ERP doctor: every setup problem at once, each with the next command.

Readiness is three separate answers:

* `ready_read`: the configuration is applied, the site answers as the
  integration user, the company, warehouses and price lists exist and every
  resource the desks use is readable.
* `ready_write`: reads are ready, writes are enabled, the unique operation id
  field is installed on both documents, and nothing on the site acts on a
  draft save (no Server Script, Webhook, Notification, Workflow, Assignment
  Rule or Auto Repeat on Sales Order or Purchase Invoice, and no app beyond
  frappe and erpnext unless an administrator qualified it). Anything that
  cannot be verified keeps live writes unready.
* `ready_submit`: writes are ready and the separate submit credential is set.

The sample backend has no network and no hooks: it is ready by construction,
and says so.
"""
from __future__ import annotations

from .errors import ERPError
from .models import ERPContext
from .resources import RESOURCES

HOOKS = (
    ("Server Script", [["script_type", "=", "DocType Event"], ["disabled", "=", 0]], "reference_doctype"),
    ("Webhook", [["enabled", "=", 1]], "webhook_doctype"),
    ("Notification", [["enabled", "=", 1]], "document_type"),
    ("Workflow", [["is_active", "=", 1]], "document_type"),
    ("Assignment Rule", [["disabled", "=", 0]], "document_type"),
    ("Auto Repeat", [["disabled", "=", 0]], "reference_doctype"),
)
WRITE_DOCTYPES = ["Sales Order", "Purchase Invoice"]
#: Frappe and ERPNext major versions this connector has passed the live contract suite on (tests/live/).
#: Other versions get a warning to run the suite against a test copy of the site first.
QUALIFIED: dict[str, tuple[str, ...]] = {"frappe": (), "erpnext": ()}


def hook_findings(transport, unqualified_apps=()) -> tuple[list[str], list[str]]:
    """What on the site could act on a draft save of the two documents the
    desk writes: hooks found, and what could not be checked."""
    found, unverifiable = [], []
    for doctype, base, field in HOOKS:
        try:
            rows = transport.list_docs(doctype, filters=base + [[field, "in", WRITE_DOCTYPES]], fields=[field],
                                       start=0, limit=20)
            found += [f"{doctype} {r['name']}" for r in rows]
        except ERPError as exc:
            if exc.code == "not_found":
                continue          # the DocType is not installed on this site
            unverifiable.append(f"{doctype} ({exc.code})")
    if unqualified_apps:
        unverifiable.append("apps " + ", ".join(unqualified_apps))
    return found, unverifiable


def unqualified_apps(transport, cfg) -> list[str]:
    if cfg.backend != "frappe_rest":
        return []
    return sorted(set(transport.versions()) - {"frappe", "erpnext"} - set(cfg.qualified_apps))


def _check(cid, status, summary, recovery=None, **details):
    out = {"id": cid, "status": status, "summary": summary}
    if recovery:
        out["recovery"] = recovery
    if details:
        out["details"] = details
    return out


def run(service, *, probe_resources: bool = True) -> dict:
    checks = []
    try:
        applied = service.applied()
    except ERPError as exc:
        return {"ready_read": False, "ready_write": False, "ready_submit": False,
                "checks": [_check("config", "fail", exc.message, "hubzoid-erp configure . --backend sample "
                                  "--dataset distributor-v1")]}
    cfg = applied.config
    checks.append(_check("config", "warn" if applied.drift else "ok",
                         applied.drift or f"Revision {applied.revision} applied ({cfg.backend}).",
                         "Apply the file with hubzoid-erp configure --file" if applied.drift else None,
                         revision=applied.revision, digest=applied.digest))
    label = {"sample": "SYNTHETIC DATA. Sample backend: no network, no live ERP.",
             "fake_rest": "SYNTHETIC DATA. Fake REST test server on loopback, not a real ERP.",
             "frappe_rest": (f"LOCAL TEST SITE {cfg.origin}: a disposable ERPNext with synthetic data, never "
                             "production." if cfg.local_test_site else f"Live ERPNext site {cfg.origin}.")}[cfg.backend]
    checks.append(_check("backend", "info", label, backend=cfg.backend, synthetic=cfg.synthetic))
    ready_read = True
    try:
        transport = service.transport()
    except ERPError as exc:
        checks.append(_check("credential", "fail", exc.message, "A credential administrator runs: hubzoid secrets "
                             f"set . --ref {cfg.credential_ref or 'secret:hub/<hub>/erpnext-api'} --stdin --apply"))
        return {"ready_read": False, "ready_write": False, "ready_submit": False, "backend": cfg.backend,
                "synthetic": cfg.synthetic, "company": cfg.company, "checks": checks}
    try:
        user = transport.logged_user()
        checks.append(_check("identity", "ok", f"Connected as {user}.", user=user))
        versions = transport.versions()
        if cfg.backend == "frappe_rest":
            bad = [f"{app} {v}" for app, majors in QUALIFIED.items()
                   if not str(versions.get(app, "")).split(".")[0] in majors]
            extra = sorted(set(versions) - {"frappe", "erpnext"} - set(cfg.qualified_apps))
            checks.append(_check("versions", "warn" if bad else "ok",
                                 f"Versions: {', '.join(f'{a} {v}' for a, v in sorted(versions.items()))}."
                                 + (f" Check on a test copy before enabling writes: {', '.join(bad)}." if bad else ""),
                                 "Run the live contract suite against a test copy of this site before enabling "
                                 "writes (testing/erpnext/README.md)" if bad else None,
                                 versions=versions, unqualified_apps=extra))
        else:
            extra = []
        # company, warehouses, price lists
        missing = []
        for doctype, name in [("Company", cfg.company), *[("Warehouse", w) for w in cfg.warehouses],
                              ("Price List", cfg.selling_price_list), ("Price List", cfg.buying_price_list)]:
            try:
                if transport.get_doc(doctype, name) is None:
                    missing.append(f"{doctype} {name}")
            except ERPError as exc:
                if exc.code == "erp_permission_denied":
                    missing.append(f"{doctype} {name} (not readable)")
                else:
                    raise
        if missing:
            ready_read = False
            checks.append(_check("company", "fail", f"Missing or unreadable: {', '.join(missing)}.",
                                 "Fix company, warehouses and price lists in connectors/erpnext.yaml, or grant the "
                                 "integration user read access to them."))
        else:
            checks.append(_check("company", "ok", f"Company {cfg.company}, warehouses {', '.join(cfg.warehouses)} and "
                                 "both price lists exist."))
        if probe_resources:
            reader = service.reader(transport)
            ctx = ERPContext(hub_id="doctor", principal="doctor")
            unreadable = []
            for name in RESOURCES:
                try:
                    reader.list_records(name, {}, ctx, limit=1)
                except ERPError as exc:
                    unreadable.append(f"{name} ({exc.code})")
            if unreadable:
                ready_read = False
                checks.append(_check("resources", "fail", f"Not readable: {', '.join(unreadable)}.",
                                     "Grant the integration user read permission on these DocTypes "
                                     "(docs/erpnext-setup.md lists them)."))
            else:
                checks.append(_check("resources", "ok", f"All {len(RESOURCES)} resources are readable."))
    except ERPError as exc:
        ready_read = False
        checks.append(_check("connection", "fail", exc.message, _recovery(exc)))
        _close(transport)
        return {"ready_read": False, "ready_write": False, "ready_submit": False, "backend": cfg.backend,
                "synthetic": cfg.synthetic, "company": cfg.company, "checks": checks}

    # -- write prerequisites --------------------------------------------------------------------
    write_ok = ready_read
    if cfg.backend == "sample":
        checks.append(_check("write_schema", "ok", "Sample backend: unique operation ids and inert drafts are "
                             "built in."))
        checks.append(_check("draft_hooks", "ok", "Sample backend: no hooks act on a draft save."))
    else:
        from .bootstrap import inspect

        state = inspect(transport)
        if all(v == "ok" for v in state.values()):
            checks.append(_check("write_schema", "ok", "custom_hubzoid_operation_id is unique on Sales Order and "
                                 "Purchase Invoice."))
        else:
            write_ok = False
            checks.append(_check("write_schema", "fail", "The unique operation id field is "
                                 + ", ".join(f"{k}: {v}" for k, v in state.items()) + ".",
                                 "An ERP administrator runs hubzoid-erp bootstrap export, reviews it, then "
                                 "hubzoid-erp bootstrap apply with an administrator credential.", state=state))
        found, unverifiable = hook_findings(transport, extra)
        if found or unverifiable:
            write_ok = False
            checks.append(_check("draft_hooks", "fail",
                                 ("Acts on Sales Order or Purchase Invoice: " + ", ".join(found) + ". " if found else "")
                                 + ("Cannot verify: " + ", ".join(unverifiable) + "." if unverifiable else ""),
                                 "Check each one cannot submit, send, notify or post on a draft save. Disable it, or "
                                 "grant the integration user read access so doctor can see it. Qualified extra apps "
                                 "go in qualified_apps.", found=found, unverifiable=unverifiable))
        else:
            checks.append(_check("draft_hooks", "ok", "No server script, webhook, notification, workflow, assignment "
                                 "rule or auto repeat acts on a draft save."))
    if not cfg.writes_enabled:
        checks.append(_check("writes", "info", "ERP writes are disabled (writes_enabled: false). Reads only.",
                             "When ready_write prerequisites pass: hubzoid-erp configure . --set writes_enabled=true "
                             "--expected-revision N"))
    ready_write = write_ok and cfg.writes_enabled
    submit_ok = ready_write and (cfg.backend != "frappe_rest" or bool(cfg.submit_credential_ref))
    if cfg.backend == "frappe_rest" and cfg.writes_enabled:
        if not cfg.submit_credential_ref:
            checks.append(_check("submit_credential", "info", "No submit credential: approve-mode submission is "
                                 "unavailable. Drafts still work.",
                                 "Set submit_credential_ref to a separate ERP user that may only submit these two "
                                 "documents."))
        else:
            available = bool(service.secret_resolver(cfg.submit_credential_ref))
            submit_ok = submit_ok and available
            checks.append(_check("submit_credential", "ok" if available else "fail",
                                 f"Submit credential {cfg.submit_credential_ref} is "
                                 + ("set." if available else "not set."),
                                 None if available else "A credential administrator sets it with hubzoid secrets set."))
    _close(transport)
    return {"ready_read": ready_read, "ready_write": ready_write, "ready_submit": submit_ok,
            "backend": cfg.backend, "synthetic": cfg.synthetic, "company": cfg.company, "site": cfg.origin,
            "writes_enabled": cfg.writes_enabled, "checks": checks}


def _recovery(exc: ERPError) -> str:
    return {
        "erp_auth_failed": "Rotate the integration user's API key and set it with hubzoid secrets set.",
        "erp_permission_denied": "Grant the integration user the read permission doctor names.",
        "erp_unavailable": "Check the site is up and site_url is right, then run doctor again.",
        "protocol_error": "Check site_url points at the ERPNext site itself (no proxy page or redirect).",
    }.get(exc.code, "Run hubzoid-erp doctor . --json for details.")


def _close(transport):
    close = getattr(transport, "close", None)
    if close:
        close()
