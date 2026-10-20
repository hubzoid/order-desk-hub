"""Inspectable local-host setup preview/apply. Never handles credential values."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from .records import RULE_FILE, LEGACY_RULE_FILE, RecordRules, read_rule_text, rule_text
from .store import StoreError
from .paths import hub_key


def preview(hub_dir, *, company: str, runtime: str, mailbox: str = "", client_id: str = "",
            client_secret_ref: str = "", channel: str = "orders-mail", label_ids: list[str] | None = None) -> dict:
    hub = Path(hub_dir)
    if runtime not in ("claude-local", "codex-local"):
        raise StoreError("runtime_invalid", "Choose claude-local or codex-local.", status=422)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", channel):
        raise StoreError("channel_invalid", "Use a lowercase channel id.", status=422)
    if mailbox and ("@" not in mailbox or not client_id or not client_secret_ref.startswith(("secret:", "env:"))):
        raise StoreError("gmail_config_invalid", "Provide mailbox, customer-owned OAuth client id and a secret reference, never its value.", status=422)
    labels = label_ids if label_ids is not None else ["INBOX"]
    if not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise StoreError("gmail_labels_invalid", "Choose existing Gmail label IDs, or explicitly review whole-INBOX ongoing retention.", status=422)
    files = {}
    rules = hub / RULE_FILE
    legacy = hub / LEGACY_RULE_FILE
    if not rules.exists():
        initial = read_rule_text(legacy.read_text()) if legacy.exists() else RecordRules(company=company)
        if initial.company == "My company":
            initial = initial.model_copy(update={"company": company})
        files[RULE_FILE] = rule_text(initial)
    elif read_rule_text(rules.read_text()).company == "My company":
        initial = read_rule_text(rules.read_text()).model_copy(update={"company": company})
        files[RULE_FILE] = rule_text(initial)
    settings_path = hub / "hub.settings.yaml"
    settings = yaml.safe_load(settings_path.read_text()) if settings_path.is_file() else {"version": 1, "values": {}, "secrets": {}}
    settings.setdefault("values", {})["DESK_ERP_ENABLED"] = False
    sources = settings["values"].get("DESK_SOURCES", [])
    if not isinstance(sources, list) or any(not isinstance(value, str) for value in sources):
        raise StoreError("sources_invalid", "DESK_SOURCES must be a list. Preserve and review the existing source configuration.", status=422)
    settings["values"]["DESK_SOURCES"] = list(dict.fromkeys([*sources, "upload", "email"]))
    files["hub.settings.yaml"] = yaml.safe_dump(settings, sort_keys=False)
    main = hub / "AGENTS.md"
    if main.is_file():
        text = main.read_text()
        if text.startswith("---\n"):
            front, body = text[4:].split("\n---", 1)
            if re.search(r"(?m)^model:", front):
                front = re.sub(r"(?m)^model:.*$", f"model: {runtime}", front)
            else:
                front += f"\nmodel: {runtime}"
            files["AGENTS.md"] = "---\n" + front + "\n---" + body
    agent = hub / "agents/desk-extractor/AGENTS.md"
    if agent.is_file():
        text = agent.read_text()
        text = re.sub(r"(?m)^engine:.*$", f"engine: {runtime}", text)
        text = re.sub(r"(?m)^model:.*$", "model: sonnet" if runtime == "claude-local" else "model: gpt-6.1-sol", text)
        files[str(agent.relative_to(hub))] = text
    if mailbox:
        rel = f"channels/{channel}.yaml"
        if not (hub / rel).exists():
            files[rel] = yaml.safe_dump({"version": 1, "id": channel, "provider": "email", "enabled": True,
                "workflow": "order_invoice_intake", "run_as": "workflow", "provider_config": {
                "transport": "gmail", "mailbox": mailbox, "gmail": {"client_id": client_id,
                "client_secret_ref": client_secret_ref, "oauth_ref": f"secret:channel/{hub_key(hub)}/{channel}/gmail-oauth",
                "label_ids": labels}, "sender_policy": {"allowed": [], "authentication": "aligned_dkim",
                "unknown": "quarantine"}}}, sort_keys=False)
    ongoing_scope = {"mailbox": mailbox, "label_ids": labels, "history_query_filters_recurring_poll": False}
    existing_channel = hub / f"channels/{channel}.yaml"
    if existing_channel.is_file():
        actual = (yaml.safe_load(existing_channel.read_text()) or {}).get("provider_config", {})
        ongoing_scope["mailbox"] = actual.get("mailbox")
        ongoing_scope["label_ids"] = (actual.get("gmail") or {}).get("label_ids", ["INBOX"])
    ongoing_scope["retention"] = "Selected ongoing message bodies and attachments are retained. Whole INBOX means all newly admitted INBOX mail, not just the historical keyword query."
    edits = []
    for rel, text in files.items():
        path = hub / rel
        if not path.resolve().is_relative_to(hub.resolve()):
            raise StoreError("path_denied", "Setup files must stay inside the hub.", status=403)
        old = path.read_text() if path.is_file() else None
        if old != text:
            edits.append({"path": rel, "before": old, "text": text})
    digest = "sha256:" + hashlib.sha256(json.dumps(edits, sort_keys=True).encode()).hexdigest()
    scope = "desk_source_" + hashlib.sha256((channel + (":" + ongoing_scope["mailbox"].lower() if ongoing_scope["mailbox"] else "")).encode()).hexdigest()[:16]
    return {"digest": digest, "edits": edits, "runtime": runtime, "erp_enabled": False,
            "source_grant": scope, "ongoing_scope": ongoing_scope, "costs": "Uses selected installed runtime authentication. Qualify unattended/hosted auth separately.",
            "handoffs": ["Set OAuth secret through hubzoid secrets, then apply the channel and complete external Google consent.",
                         "Use exact sender allowlist or explicit intake quarantine review. A sender is not a member.",
                         f"An authorized host administrator grants desk_view, desk_operate, wf_order_invoice_intake and {scope} to the mailbox owner.",
                         "Review recurring mailbox/label scope separately: history query keywords do not filter future polling. Select a dedicated business mailbox/existing labels, or explicitly approve whole-INBOX retention.",
                         "OAuth automatically saves tokens in the channel-scoped oauth_ref. Do not set that token slot by hand.",
                         "Preview bounded historical import, then apply that exact digest. No ERP connection is needed."]}


def apply(hub_dir, plan: dict, digest: str) -> dict:
    from hubzoid.datadir import hub_read_only
    hub = Path(hub_dir)
    if hub_read_only(hub):
        raise StoreError("source_read_only", "Run setup in the authorized builder checkout, review the files, then redeploy.", status=409)
    if plan["digest"] != digest:
        raise StoreError("stale_preview", "Setup changed. Review the new preview and use its digest.", status=409)
    # Validate every expected original before writing any file. A repeated
    # apply of the same plan is harmless if its exact target bytes remain.
    for edit in plan["edits"]:
        path = hub / edit["path"]
        old = path.read_text() if path.is_file() else None
        if old not in (edit["before"], edit["text"]):
            raise StoreError("setup_conflict", f"{edit['path']} changed. Preview again, preserving the edit.", status=409)
    for edit in plan["edits"]:
        path = hub / edit["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(edit["text"])
    return {"applied": digest, "files": [e["path"] for e in plan["edits"]], "handoffs": plan["handoffs"],
            "authority": "trusted local host authoring; no business approvals or secrets were created"}
