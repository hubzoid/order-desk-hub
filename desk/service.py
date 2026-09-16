"""The desk service: every case action, shared by the workflows, the chat
tools and the `hubzoid-desk` CLI, so every surface gets the same answer.

Who may do what (Hubzoid grants, `hubzoid grant EMAIL PERMISSION HUB`):

| Grant | Allows |
|---|---|
| `desk_view` | see the queue and cases |
| `desk_operate` | claim, release, correct, rerun and save an ERP draft for a case they own |
| `desk_submit` | ask for submission of an ERP draft they own (approve mode only) |
| `desk_manage` | reassign, bind senders, pause intake, collect reconciliation evidence |

A hub administrator (`manage_access`) holds all four. Approving a submission
is never a grant: only the person who asked for it approves the exact draft
(the SDK's work-item approval). The local operator in local owner mode is the
hub owner.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from erp import digest as erp_digest
from erp.errors import ERPError
from erp.models import ERPContext
from erp.service import ERPService

from . import documents as docs_lib
from . import extract as extract_lib
from . import policy as policy_lib
from . import propose as propose_lib
from .paths import hub_key, output_dir
from .store import OPEN, Store, StoreError, case_number

log = logging.getLogger("hubzoid.desk")

INTAKE_WORKFLOW = "order_invoice_intake"
CASE_WORKFLOW = "order_invoice_case"
PERMISSIONS = ("desk_view", "desk_operate", "desk_submit", "desk_manage")
SETTING_DEFAULTS = {
    "DESK_SOURCES": ["upload", "email", "whatsapp"], "DESK_INTAKE_ENABLED": True,
    "DESK_POLICY_FILES": [policy_lib.ORDER_POLICY, policy_lib.INVOICE_POLICY],
    "DESK_ALIASES_FILE": policy_lib.ALIASES, "DESK_MAX_FILE_BYTES": 20971520, "DESK_MAX_PAGES": 100,
    "DESK_MAX_LINES": 200, "DESK_PARSE_TIMEOUT_SECONDS": 180,
}
CHANNEL_KIND = {"email": "email", "sample_email": "email", "whatsapp_cloud": "whatsapp", "sample_whatsapp": "whatsapp",
                "upload": "upload"}


class DeskError(StoreError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Desk:
    def __init__(self, hub_dir, *, erp: ERPService | None = None, fetch=None, grants=None):
        self.hub_dir = Path(hub_dir)
        self.hub_id = hub_key(self.hub_dir)
        self.store = Store(self.hub_dir)
        self.erp = erp or ERPService.for_hub(self.hub_dir)
        self._fetch = fetch            # tests: callable(attachment dict) -> bytes
        self._grants = grants          # tests: callable(actor, permission) -> bool

    # -- settings, rules, permissions ---------------------------------------------------------------
    def settings(self) -> dict:
        out = dict(SETTING_DEFAULTS)
        try:
            from hubzoid.hubs import settings as sdk_settings

            for k, v in sdk_settings.effective(self.hub_dir).items():
                if k in out and v is not None:
                    out[k] = v
        except Exception:  # noqa: BLE001 - outside a Hubzoid install the defaults apply
            pass
        return out

    def limits(self) -> docs_lib.Limits:
        s = self.settings()
        return docs_lib.Limits(max_file_bytes=int(s["DESK_MAX_FILE_BYTES"]), max_pages=int(s["DESK_MAX_PAGES"]),
                               timeout_seconds=int(s["DESK_PARSE_TIMEOUT_SECONDS"]))

    def rules(self) -> policy_lib.Rules:
        return policy_lib.load(self.hub_dir, self.erp.config().company)

    def can(self, actor: str, permission: str) -> bool:
        if self._grants is not None:
            return bool(self._grants(actor, permission))
        if not actor:
            return False
        if actor.startswith("cli:"):
            return True       # the operator at this machine, with the server's authority
        try:
            from hubzoid.access.session import LOCAL_OWNER
            from hubzoid.workflows.identity import local_quickstart

            if actor == LOCAL_OWNER and local_quickstart(self.hub_dir):
                return True   # local owner mode: the one local account owns the hub
        except Exception:  # noqa: BLE001
            pass
        try:
            from hubzoid.access import store_for

            gs = store_for(self.hub_dir)
            return bool(gs.can(actor, self.hub_id, permission) or gs.can(actor, self.hub_id, "manage_access")
                        or gs.can(actor, "*", "manage_access"))
        except Exception:  # noqa: BLE001 - unreadable access data grants nothing
            return False

    def require(self, actor: str, permission: str, what: str):
        if not self.can(actor, permission):
            raise DeskError("permission_denied", f"{what} needs the {permission} grant on this hub. Ask an "
                            f"administrator: hubzoid grant {actor or 'EMAIL'} {permission} .", status=403)

    def erp_ctx(self, case: dict | None, actor: str = "desk") -> ERPContext:
        scope = None
        if case and case.get("party_type") and case.get("party_id"):
            scope = (case["party_type"].lower(), case["party_id"])
        return ERPContext(hub_id=self.hub_id, principal=f"desk:{self.hub_id}", requester=actor,
                          party_scope=scope)

    # -- intake ---------------------------------------------------------------------------------------
    def _source_event(self, payload: dict) -> tuple[str, str]:
        if payload.get("intake_id"):
            return "intake", f"intake:{payload['intake_id']}"
        if payload.get("upload_id"):
            return "upload", f"upload:{payload['upload_id']}"
        raise DeskError("invalid_request", "The intake input has no intake_id or upload_id.", status=422)

    def _party_from(self, payload: dict, company: str) -> tuple[str | None, str | None, str | None]:
        """(party type, party id, how it was bound). Only a server-recorded
        binding or an authorized operator binds a party, never message text."""
        if payload.get("upload_id"):
            party = payload.get("party") or {}
            if party.get("party_type") in ("Customer", "Supplier") and party.get("party_id"):
                return party["party_type"], party["party_id"], f"chosen by {payload.get('uploaded_by')}"
            return None, None, None
        kind = CHANNEL_KIND.get(payload.get("provider") or "", payload.get("provider"))
        sender = (payload.get("sender") or {}).get("external_id") or ""
        row = self.store.binding(company, kind, sender)
        if row:
            return row["party_type"], row["party_id"], f"binding {kind} {row['source_ref']}"
        return None, None, None

    def _received_at(self, payload: dict) -> str:
        """When the message was sent or received: the email's Date header, the
        WhatsApp timestamp, else when intake recorded it."""
        if payload.get("received_at"):
            return payload["received_at"]
        if payload.get("intake_id"):
            try:
                from hubzoid.intake import service as intake

                item = intake.get(self.hub_dir, payload["intake_id"])
                data = (intake.envelope(self.hub_dir, item).get("provider_data") or {}) if item else {}
                if data.get("date"):
                    import email.utils

                    dt = email.utils.parsedate_to_datetime(data["date"]).astimezone(timezone.utc)
                    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                if data.get("provider_timestamp"):
                    dt = datetime.fromtimestamp(int(data["provider_timestamp"]), timezone.utc)
                    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                if item:
                    dt = datetime.fromtimestamp(float(item["created"]), timezone.utc)
                    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            except Exception:  # noqa: BLE001 - the time of reading is the fallback
                log.warning("desk: could not read the intake time of %s", payload.get("intake_id"))
        return _now_iso()

    def open_case(self, payload: dict, *, work_item_id: str | None, preview: bool = False) -> tuple[dict, bool]:
        source, event = self._source_event(payload)
        cfg = self.erp.config()
        ptype, pid, bound = self._party_from(payload, cfg.company)
        sender = payload.get("sender") or {}
        return self.store.create_case(
            hub_id=self.hub_id, source=source, source_event_id=event, intake_id=payload.get("intake_id"),
            triage_work_item_id=work_item_id, channel=payload.get("channel") or source,
            sender_json={"external_id": sender.get("external_id"),
                         "display_name": sender.get("display_name") or sender.get("display"),
                         "provider": payload.get("provider")},
            received_at=self._received_at(payload), company=cfg.company, party_type=ptype,
            party_id=pid, party_bound_by=bound, preview=1 if preview else 0)

    def _attachment_bytes(self, att: dict) -> bytes:
        if self._fetch is not None:
            return self._fetch(att)
        if att.get("attachment_id") and not att.get("owner"):
            from hubzoid.intake import service as intake

            _, data = intake.attachment_bytes(self.hub_dir, att["attachment_id"])
            return data
        from hubzoid.intake.service import files_for

        actor = SimpleNamespace(id=att["owner"], email="", groups=frozenset(), name="")
        _, data = files_for(self.hub_dir).content(actor, att["file_id"])
        return data

    def stage_parse(self, payload: dict, *, work_item_id: str | None, preview: bool = False) -> dict:
        """Open (or find) the case and read every document into elements."""
        settings = self.settings()
        case, created = self.open_case(payload, work_item_id=work_item_id, preview=preview)
        if not created and case.get("proposal_revision") and case["triage_work_item_id"] != work_item_id:
            return {"case_id": case["id"], "stop": "already_triaged", "state": case["state"]}
        source = case["source"] if case["source"] == "upload" else CHANNEL_KIND.get(payload.get("provider"), "email")
        if source not in settings["DESK_SOURCES"]:
            self._error(case, "source_disabled", f"{source} is not an enabled desk source (DESK_SOURCES).")
            return {"case_id": case["id"], "stop": "source_disabled"}
        if not settings["DESK_INTAKE_ENABLED"]:
            self.store.update_case(case["id"], error_code="intake_paused", event="case.paused", actor="intake",
                                   error_message="Intake is paused (DESK_INTAKE_ENABLED: false). Restart this work "
                                   "item once intake is on.")
            return {"case_id": case["id"], "stop": "intake_paused"}
        parsed = [docs_lib.message_elements(payload.get("text") or "", label="message")]
        meta = [{"ordinal": 0, "label": "message", "filename": None, "media_type": "text/plain",
                 "parse_status": "parsed", "parser": "text", "elements": len(parsed[0].elements)}]
        problems = []
        for i, att in enumerate(payload.get("attachments") or [], 1):
            label = att.get("filename") or f"attachment-{i}"
            row = {"ordinal": i, "label": label, "filename": label, "media_type": att.get("media_type"),
                   "sha256": att.get("sha256"), "size": att.get("bytes"),
                   "file_ref": att.get("attachment_id") or att.get("file_id")}
            try:
                data = self._attachment_bytes(att)
                if att.get("sha256") and docs_lib.sha256(data) != att["sha256"]:
                    raise docs_lib.ParseError("parse_failed", f"{label} does not match the hash recorded at intake.")
                result = docs_lib.parse_file(data, label, att.get("media_type"), limits=self.limits())
                parsed.append(result)
                row.update(parse_status="parsed", parser=result.parser, sha256=docs_lib.sha256(data), size=len(data))
            except docs_lib.ParseError as exc:
                row.update(parse_status="failed", error_code=exc.code)
                problems.append({"document": label, "code": exc.code, "message": exc.message})
            except Exception as exc:  # noqa: BLE001 - a missing attachment is a case error, not a crash
                row.update(parse_status="failed", error_code="attachment_unavailable")
                problems.append({"document": label, "code": "attachment_unavailable",
                                 "message": f"{label} could not be read ({type(exc).__name__})."})
            meta.append(row)
        elements = docs_lib.number(parsed)
        by_doc: dict[int, list] = {}
        for e in elements:
            by_doc.setdefault(e["doc"] - 1, []).append(e)
        doc_index = 0
        for row in meta:
            els = []
            if row["parse_status"] == "parsed":
                els = by_doc.get(doc_index, [])
                doc_index += 1
            self.store.add_document(case["id"], ordinal=row["ordinal"], label=row["label"],
                                    filename=row.get("filename"), media_type=row.get("media_type"),
                                    sha256=row.get("sha256"), size=row.get("size"), file_ref=row.get("file_ref"),
                                    parser=row.get("parser"), parse_status=row["parse_status"],
                                    error_code=row.get("error_code"), elements_json=els)
        if problems and (payload.get("attachments") or []) and \
                all(r["parse_status"] == "failed" for r in meta[1:]):
            code = problems[0]["code"]
            self._error(case, code, problems[0]["message"], problems=problems)
            return {"case_id": case["id"], "stop": code, "problems": problems}
        if not elements:
            self._error(case, "empty_document", "The message has no text and no readable attachment.")
            return {"case_id": case["id"], "stop": "empty_document"}
        self.store.update_case(case["id"], event="case.parsed", actor="intake",
                               payload={"documents": len(meta), "elements": len(elements), "problems": problems})
        return {"case_id": case["id"], "stop": None, "elements": elements, "problems": problems,
                "party_type": case.get("party_type"), "company": case["company"]}

    def extraction_task(self, parsed: dict) -> str:
        return extract_lib.task_text(parsed["elements"], company=parsed["company"], sender_kind=parsed.get("party_type"))

    def stage_match(self, case_id: str, station_output, *, elements: list[dict], actor: str = "intake",
                    parse_problems: list | None = None, overrides: dict | None = None, reason: str = "triage",
                    extraction: dict | None = None) -> dict:
        """Check the extraction against its sources, match and validate, then
        record proposal revision N and the case's new state."""
        case = self.store.get_case(case_id)
        if reason == "triage" and case.get("proposal_revision"):
            # A replay of the same triage after a crash between the write and its saved stage.
            return self._summary(case)
        try:
            ex = extract_lib.parse_output(extraction if extraction is not None else station_output)
        except ValueError as exc:
            self._error(case, "extraction_invalid", f"The extractor's answer was not usable: {exc}")
            return self._summary(self.store.get_case(case_id))
        verified = extract_lib.verify(ex, elements)
        try:
            rules = self.rules()
        except policy_lib.PolicyError as exc:
            self._error(case, exc.code, exc.message, **exc.detail())
            return self._summary(self.store.get_case(case_id))
        cfg = self.erp.config()
        received = (case.get("received_at") or _now_iso())[:10]
        pctx = propose_lib.Context(
            company=cfg.company, currency=cfg.currency, warehouse=cfg.warehouses[0],
            selling_price_list=cfg.selling_price_list, buying_price_list=cfg.buying_price_list,
            party_type=case.get("party_type"), party_id=case.get("party_id"),
            received_date=date.fromisoformat(received), rules=rules, reader=self.erp.reader(),
            erp_ctx=self.erp_ctx(case), overrides=overrides or {})
        max_lines = int(self.settings()["DESK_MAX_LINES"])
        if len(verified.extraction.lines) > max_lines:
            self._error(case, "too_many_lines", f"The document has {len(verified.extraction.lines)} lines. The limit "
                        f"is {max_lines} per draft. Split it.")
            return self._summary(self.store.get_case(case_id))
        try:
            proposal = propose_lib.build(pctx, verified.extraction, verified.problems, elements)
        except ERPError as exc:
            self._error(case, exc.code, exc.message)
            return self._summary(self.store.get_case(case_id))
        prop = proposal.to_dict()
        for p in parse_problems or []:
            prop["warnings"].append({"code": p["code"], "message": p["message"]})
        state, extra = self._decide(case, proposal)
        with self.store.tx() as c:
            rev = self.store.add_proposal(case_id, created_by=actor, reason=reason, conn=c, kind=proposal.kind,
                                          payload_json=proposal.payload, digest=prop["digest"],
                                          evidence_json={"header": proposal.header, "lines": proposal.lines,
                                                         "totals": proposal.totals},
                                          questions_json=proposal.questions, warnings_json=prop["warnings"],
                                          extraction_json=verified.extraction.model_dump(mode="json"),
                                          corrections_json=overrides or {}, policy_digest=rules.digest,
                                          erp_snapshot_json=proposal.erp_versions)
            case = self.store.update_case(
                case_id, conn=c, state=state, kind=proposal.kind, proposal_revision=rev, policy_digest=rules.digest,
                business_reference=(proposal.header.get("reference") or {}).get("value"),
                question_json=proposal.questions or None, error_code=None, error_message=None,
                flags_json={"warnings": [w["code"] for w in prop["warnings"]]}, event=f"case.{state}", actor=actor,
                payload={"proposal_revision": rev, "questions": len(proposal.questions)}, **extra)
        return self._summary(case)

    def _decide(self, case: dict, proposal) -> tuple[str, dict]:
        if proposal.duplicate:
            return "duplicate", {"duplicate_of": proposal.duplicate["erp_record"]}
        if proposal.content_key and case.get("party_id"):
            content = erp_digest.sha(proposal.content_key)
            held = self.store.claim_business_key(hub_id=self.hub_id, company=case["company"], kind=proposal.kind,
                                                 party_id=case["party_id"],
                                                 reference=proposal.content_key["reference"], case_id=case["id"],
                                                 content_digest=content)
            if held["case_id"] != case["id"]:
                other = self.store.get_case(held["case_id"])
                if held["content_digest"] == content:
                    return "duplicate", {"duplicate_of": case_number(other) if other else held["case_id"]}
                proposal.questions.append(propose_lib.Question(
                    "duplicate_conflict", f"{case_number(other) if other else 'Another case'} already has this "
                    f"reference with different lines. Which one is right?", "reference",
                    answer_type="text").to_dict())
                proposal.payload = None
        if proposal.questions:
            return "needs_clarification", {}
        return ("draft_ready" if case.get("owner") else "needs_owner"), {}

    def _error(self, case: dict, code: str, message: str, **extra):
        self.store.update_case(case["id"], state="error", error_code=code, error_message=message[:2000],
                               event="case.failed", actor="desk", payload={"code": code, **extra})

    # -- reading ----------------------------------------------------------------------------------------
    def _summary(self, case: dict) -> dict:
        prop = self.store.proposal(case["id"], case.get("proposal_revision")) if case.get("proposal_revision") else None
        return {
            "case_id": case["id"], "number": case_number(case), "state": case["state"], "kind": case.get("kind"),
            "party": {"type": case.get("party_type"), "id": case.get("party_id")}, "owner": case.get("owner"),
            "reference": case.get("business_reference"), "revision": case["revision"],
            "proposal_revision": case.get("proposal_revision"), "questions": case.get("question_json") or [],
            "erp": {"doctype": case.get("erp_doctype"), "name": case.get("erp_name"),
                    "docstatus": case.get("erp_docstatus"), "modified": case.get("erp_modified"),
                    "approval_digest": case.get("approval_digest")} if case.get("erp_name") else None,
            "duplicate_of": case.get("duplicate_of"), "error": {"code": case["error_code"], "message":
                                                                case.get("error_message")} if case.get("error_code") else None,
            "total": (prop or {}).get("evidence", {}).get("totals", {}).get("computed") if prop else None,
            "digest": (prop or {}).get("digest"), "received_at": case.get("received_at"),
            "channel": case.get("channel"), "sender": (case.get("sender_json") or {}).get("external_id"),
            "updated_at": case["updated_at"], "warnings": (case.get("flags_json") or {}).get("warnings", []),
        }

    def list(self, actor: str, *, state: str | None = None, mine: bool = False, limit: int = 25,
             before: int | None = None) -> dict:
        self.require(actor, "desk_view", "Seeing the queue")
        rows = self.store.list_cases(self.hub_id, state=state, owner=actor if mine else None, limit=limit, before=before)
        out = [self._summary(self.sync(r)) for r in rows]
        nxt = rows[-1]["number"] if len(rows) == max(1, min(limit, 100)) else None
        return {"cases": out, "next_before": nxt, "counts": {k: v["count"] for k, v in self.store.counts(self.hub_id).items()}}

    def show(self, actor: str, case_id: str) -> dict:
        self.require(actor, "desk_view", "Seeing a case")
        case = self.sync(self.store.require_case(case_id, self.hub_id))
        out = self._summary(case)
        prop = self.store.proposal(case["id"], case.get("proposal_revision")) if case.get("proposal_revision") else None
        out["documents"] = [{k: d.get(k) for k in ("label", "filename", "media_type", "sha256", "size", "parser",
                                                   "parse_status", "error_code")} | {"elements": d["elements"]}
                            for d in self.store.documents(case["id"])]
        out["proposal"] = prop
        out["proposals"] = [{"revision": p["revision"], "reason": p["reason"], "by": p["created_by"],
                             "at": p["created_at"], "digest": p["digest"], "questions": len(p["questions"] or [])}
                            for p in self.store.proposals(case["id"])]
        out["links"] = self.store.links(case["id"])
        out["events"] = [{"type": e["type"], "actor": e["actor"], "at": e["at"], "payload": e["payload"]}
                         for e in self.store.events(case["id"])]
        out["actions"] = self.actions(actor, case)
        out["write_mode"] = self.write_mode()
        out["backend"] = self.erp.config().backend
        out["synthetic"] = self.erp.config().synthetic
        return out

    def actions(self, actor: str, case: dict) -> list[str]:
        s, own = case["state"], case.get("owner")
        acts = []
        if s in ("needs_owner", "needs_clarification") and not own and self.can(actor, "desk_operate"):
            acts.append("claim")
        if own == actor or actor.startswith("cli:"):
            if s in ("draft_ready", "needs_clarification", "erp_draft_saved", "error"):
                acts.append("release")
            if s in ("needs_clarification", "draft_ready"):
                acts += ["correct", "rerun"]
            if s == "draft_ready" and self.write_mode() in ("draft", "approve"):
                acts.append("create_draft")
            if s == "erp_draft_saved" and self.write_mode() == "approve" and self.can(actor, "desk_submit"):
                acts.append("request_submit")
        if self.can(actor, "desk_manage"):
            acts.append("reassign")
            if s == "ambiguous":
                acts.append("reconcile")
        if s in OPEN and s not in ("submitting", "waiting_approval") and (own == actor or self.can(actor, "desk_manage")):
            acts.append("cancel")
        return acts

    def write_mode(self) -> str:
        try:
            from hubzoid.work_items import config as wi_config

            return wi_config.mode_for(wi_config.load(self.hub_dir), "erp.submit")
        except Exception:  # noqa: BLE001
            return "off"

    # -- ownership ----------------------------------------------------------------------------------------
    def claim(self, actor: str, case_id: str, *, expected_revision: int) -> dict:
        """Atomic: of two operators claiming at once, exactly one wins."""
        self.require(actor, "desk_operate", "Claiming a case")
        case = self.store.require_case(case_id, self.hub_id)
        if case.get("owner") and case["owner"] != actor:
            raise DeskError("owner_conflict", f"{case_number(case)} is already claimed by {case['owner']}.", status=409,
                            current_revision=case["revision"], owner=case["owner"])
        if case["state"] not in ("needs_owner", "needs_clarification"):
            raise DeskError("invalid_state", f"{case_number(case)} is {case['state']} and cannot be claimed.", status=409)
        try:
            with self.store.tx() as c:
                cur = self.store.get_case(case["id"], conn=c)
                if cur.get("owner"):
                    raise DeskError("owner_conflict", f"{case_number(cur)} was claimed by {cur['owner']} a moment ago.",
                                    status=409, current_revision=cur["revision"], owner=cur["owner"])
                new_state = "draft_ready" if cur["state"] == "needs_owner" else cur["state"]
                case = self.store.update_case(case["id"], conn=c, expected_revision=expected_revision, owner=actor,
                                              generation=cur["generation"] + 1, state=new_state,
                                              event="case.claimed", actor=actor)
        except StoreError as exc:
            if exc.code == "revision_conflict":
                cur = self.store.get_case(case["id"])
                raise DeskError("owner_conflict" if cur.get("owner") else "revision_conflict",
                                f"{case_number(cur)} changed: " + (f"claimed by {cur['owner']}." if cur.get("owner")
                                                                  else "reload it."), status=409,
                                current_revision=cur["revision"], owner=cur.get("owner")) from None
            raise
        return self._summary(case)

    def release(self, actor: str, case_id: str, *, expected_revision: int, reason: str = "") -> dict:
        case = self.store.require_case(case_id, self.hub_id)
        if case.get("owner") != actor and not actor.startswith("cli:"):
            raise DeskError("permission_denied", "Only the case owner can release it.", status=403)
        self._cancel_pending(case, actor, "released")
        state = {"draft_ready": "needs_owner"}.get(case["state"], case["state"])
        case = self.store.update_case(case["id"], expected_revision=expected_revision, owner=None, state=state,
                                      generation=case["generation"] + 1, event="case.released", actor=actor,
                                      payload={"reason": reason[:500]})
        return self._summary(case)

    def reassign(self, actor: str, case_id: str, *, to: str, expected_revision: int, reason: str) -> dict:
        """An administrator moves a case to another operator. Outstanding draft
        and submit requests are cancelled; the new owner starts fresh, with
        their own authority checked."""
        self.require(actor, "desk_manage", "Reassigning a case")
        if not reason.strip():
            raise DeskError("validation_failed", "Say why the case is reassigned.", status=422)
        if not self.can(to, "desk_operate"):
            raise DeskError("permission_denied", f"{to} cannot operate this desk (needs desk_operate).", status=403)
        case = self.store.require_case(case_id, self.hub_id)
        if case["state"] in ("submitting",):
            raise DeskError("invalid_state", "A submission is being sent. Wait for it to settle.", status=409)
        self._cancel_pending(case, actor, "reassigned")
        state = case["state"]
        if state == "waiting_approval":
            state = "erp_draft_saved"
        if state == "needs_owner":
            state = "draft_ready"
        case = self.store.update_case(case["id"], expected_revision=expected_revision, owner=to, state=state,
                                      generation=case["generation"] + 1, event="case.reassigned", actor=actor,
                                      payload={"to": to, "reason": reason[:500]})
        return self._summary(case)

    def _cancel_pending(self, case: dict, actor: str, why: str):
        from hubzoid.work_items import service as wi
        from hubzoid.work_items import store as wi_store

        for link in self.store.links(case["id"]):
            item = wi_store.get_item(self.hub_dir, link["work_item_id"]) if link.get("work_item_id") else None
            if item is None or item["state"] in wi_store.TERMINAL:
                continue
            ops = [o for o in wi_store.list_ops(self.hub_dir, item["hub"], item_id=item["id"])]
            if any(o["state"] in ("sending", "ambiguous") for o in ops):
                raise DeskError("operation_unresolved", "An ERP write for this case has an unknown outcome. Reconcile it "
                                "first.", status=409, operation_id=next(o["id"] for o in ops
                                                                     if o["state"] in ("sending", "ambiguous")))
            try:
                wi.cancel(self.hub_dir, item["id"], "cli:desk", reason=f"case {why} by {actor}", surface="desk")
            except Exception:  # noqa: BLE001 - recorded below either way
                log.exception("desk: could not cancel work item %s", item["id"])
            self.store.link(case["id"], work_item_id=item["id"], action=link["action"], state="cancelled")

    # -- corrections --------------------------------------------------------------------------------------
    CORRECTABLE = ("party", "kind", "reference", "transaction_date", "delivery_date", "bill_date", "due_date",
                   "bill_to_confirmed", "purchase_order")

    def correct(self, actor: str, case_id: str, *, expected_revision: int, fields: list[dict]) -> dict:
        """Case corrections: a new proposal revision with the given values
        applied over the extraction. They never change a shared rule."""
        case = self.store.require_case(case_id, self.hub_id)
        self._owner(actor, case, "Correcting a case")
        if case["state"] not in ("needs_clarification", "draft_ready"):
            raise DeskError("invalid_state", f"{case_number(case)} is {case['state']}. Only a proposal before its ERP "
                            "draft can be corrected. Edit a saved draft in the ERP itself.", status=409)
        if case["revision"] != int(expected_revision):
            raise DeskError("revision_conflict", "This case changed since you loaded it.", status=409,
                            current_revision=case["revision"])
        prop = self.store.proposal(case["id"])
        overrides = dict(prop.get("corrections") or {})
        party = None
        for f in fields:
            path, value = str(f.get("path", "")), f.get("value")
            ok = path in self.CORRECTABLE or any(path.startswith(f"lines[") and path.endswith(s)
                                                 for s in ("].item_code", "].qty", "].uom", "].rate", "].po_detail"))
            if not ok:
                raise DeskError("validation_failed", f"{path!r} cannot be corrected. Fields: "
                                f"{', '.join(self.CORRECTABLE)}, lines[N].item_code|qty|uom|rate|po_detail.", status=422)
            if path == "party":
                ptype, pid = (str(value).split(":", 1) + [""])[:2] if ":" in str(value) else ("", str(value))
                ptype = ptype or ("Customer" if str(value).startswith("SYN-CUST") else "Supplier"
                                  if str(value).startswith("SYN-SUPP") else "")
                if ptype not in ("Customer", "Supplier"):
                    raise DeskError("validation_failed", "party is Customer:ID or Supplier:ID.", status=422)
                party = (ptype, pid)
                continue
            overrides[path] = str(value)
        extra = {}
        if party:
            extra = {"party_type": party[0], "party_id": party[1], "party_bound_by": f"chosen by {actor}"}
            self.store.update_case(case["id"], event="case.party_bound", actor=actor, payload={"party": party[1]}, **extra)
        self._cancel_pending(case, actor, "corrected")
        self.store.event(case["id"], "case.corrected", actor, {"fields": fields})
        elements = [e for d in self.store.documents(case["id"]) for e in d["elements"]]
        return self.stage_match(case["id"], None, extraction=prop["extraction"], elements=elements, actor=actor,
                                overrides=overrides, reason="correction")

    def rerun(self, actor: str, case_id: str, *, reason: str) -> dict:
        """Match the same document again with the current rules (after an
        alias or policy change). The extraction and sources are kept."""
        case = self.store.require_case(case_id, self.hub_id)
        if case["state"] not in ("needs_owner", "needs_clarification", "draft_ready", "duplicate"):
            raise DeskError("invalid_state", f"{case_number(case)} is {case['state']}. A case with an ERP draft is "
                            "never re-proposed silently: change the draft in the ERP.", status=409)
        if case.get("owner") and case["owner"] != actor and not actor.startswith("cli:"):
            raise DeskError("permission_denied", "Only the case owner can rerun it.", status=403)
        self.require(actor, "desk_operate", "Rerunning a case")
        prop = self.store.proposal(case["id"])
        if prop is None:
            raise DeskError("invalid_state", "This case has no extraction yet. Restart its triage work item.", status=409)
        self._cancel_pending(case, actor, "rerun")
        if case["state"] == "duplicate":
            self.store.release_business_key(case["id"])
        elements = [e for d in self.store.documents(case["id"]) for e in d["elements"]]
        return self.stage_match(case["id"], None, extraction=prop["extraction"], elements=elements, actor=actor,
                                overrides=prop.get("corrections") or {}, reason=f"rerun: {reason}"[:200])

    def _owner(self, actor: str, case: dict, what: str):
        self.require(actor, "desk_operate", what)
        if case.get("owner") != actor and not actor.startswith("cli:"):
            raise DeskError("not_owner", f"{what} needs you to own {case_number(case)}. Claim it first."
                            if not case.get("owner") else f"{case_number(case)} belongs to {case['owner']}.", status=403)

    # -- ERP actions (admitted as SDK work items) ------------------------------------------------------------
    def request_draft(self, actor: str, case_id: str, *, expected_revision: int, digest: str) -> dict:
        case = self.store.require_case(case_id, self.hub_id)
        self._owner(actor, case, "Saving an ERP draft")
        mode = self.write_mode()
        if mode == "off":
            raise DeskError("write_mode_off", "ERP writes are off for this hub. Set erp.submit to draft in "
                            "work-items.yaml once the ERP is connected (doctor ready_write).", status=403)
        if case["state"] != "draft_ready":
            raise DeskError("invalid_state", f"{case_number(case)} is {case['state']}. Only a claimed proposal with no "
                            "questions can become an ERP draft.", status=409)
        if case["revision"] != int(expected_revision):
            raise DeskError("revision_conflict", "This case changed since you loaded it.", status=409,
                            current_revision=case["revision"])
        prop = self.store.proposal(case["id"])
        if prop["digest"] != digest:
            raise DeskError("stale_preview", "The proposal changed since you reviewed it. Review the current one.",
                            status=409, current_digest=prop["digest"])
        return self._admit(actor, case, route="draft", key=f"desk:{case['id']}:p{prop['revision']}:draft",
                           input={"case_id": case["id"], "proposal_revision": prop["revision"], "digest": digest,
                                  "action": "draft"})

    def request_submit(self, actor: str, case_id: str, *, expected_revision: int, digest: str,
                       erp_version: str | None = None) -> dict:
        case = self.store.require_case(case_id, self.hub_id)
        self._owner(actor, case, "Asking to submit")
        self.require(actor, "desk_submit", "Asking to submit an ERP draft")
        if self.write_mode() != "approve":
            raise DeskError("write_mode_not_approve", "Submission is available only when erp.submit is approve in "
                            "work-items.yaml. In draft mode a person submits the draft in the ERP.", status=403)
        if case["state"] != "erp_draft_saved":
            raise DeskError("invalid_state", f"{case_number(case)} is {case['state']}. Only a saved ERP draft can be "
                            "submitted.", status=409)
        if case.get("differences_json"):
            raise DeskError("review_required", "The ERP computed different values for this draft. Review it in the "
                            "ERP before asking to submit.", status=409, differences=case["differences_json"])
        if case["revision"] != int(expected_revision):
            raise DeskError("revision_conflict", "This case changed since you loaded it.", status=409,
                            current_revision=case["revision"])
        current = self.erp.writer().get(self._kind(case), case["erp_name"])
        if current["approval_digest"] != digest or (erp_version and current["erp_modified"] != erp_version):
            raise DeskError("stale_preview", "The ERP draft changed since you reviewed it. Review the current draft.",
                            status=409, current_digest=current["approval_digest"],
                            current_version=current["erp_modified"])
        return self._admit(actor, case, route="submit",
                           key=f"desk:{case['id']}:{case['erp_name']}:{digest[7:23]}:submit",
                           input={"case_id": case["id"], "erp_name": case["erp_name"], "digest": digest,
                                  "erp_version": current["erp_modified"], "action": "submit"})

    def _kind(self, case: dict) -> str:
        return case.get("kind") or ("sales_order" if case.get("erp_doctype") == "Sales Order" else "purchase_invoice")

    def _admit(self, actor: str, case: dict, *, route: str, key: str, input: dict) -> dict:
        from hubzoid.work_items import service as wi
        from hubzoid.work_items.errors import WorkItemError

        requester = actor if not actor.startswith("cli:") else self._local_owner()
        try:
            item, created = wi.admit_and_start(self.hub_dir, workflow=CASE_WORKFLOW, key=key, route=route, input=input,
                                               requester=requester, origin={"surface": "desk", "source": "desk",
                                                                            "case_id": case["id"]},
                                               actor=None if actor.startswith("cli:") else actor)
        except WorkItemError as exc:
            raise DeskError(exc.code, exc.message, status=exc.status, **exc.fields) from None
        self.store.link(case["id"], work_item_id=item["id"], action=route, insert_only=True,
                        proposal_revision=case.get("proposal_revision"), state="requested")
        self.store.event(case["id"], f"{route}.requested", actor, {"work_item_id": item["id"]})
        return {"case_id": case["id"], "number": case_number(case), "work_item_id": item["id"],
                "work_item_state": item["state"], "created": created, "route": route}

    def _local_owner(self) -> str:
        try:
            from hubzoid.work_items.cli import _decider

            return _decider(self.hub_dir, True)
        except BaseException:  # noqa: BLE001 - typer.Exit when not local quickstart
            raise DeskError("decide_as_requester", "Outside local owner mode, ERP actions run as a signed-in person. "
                            "Use the chat or the Console.", status=403) from None

    # -- inside the case work item (the workflow calls these) ----------------------------------------------------
    def begin(self, case_id: str, *, action: str, requester: str, work_item_id: str, expect: dict) -> dict:
        """Recheck everything at the start of a draft or submit execution: the
        case, its owner, the proposal digest and the requester's authority."""
        case = self.store.require_case(case_id, self.hub_id)
        if case.get("owner") != requester:
            raise DeskError("not_owner", f"{case_number(case)} now belongs to {case.get('owner') or 'nobody'}. This "
                            "request was made by someone else and stops.", status=409)
        if action == "draft":
            if case["state"] != "draft_ready":
                raise DeskError("invalid_state", f"{case_number(case)} is {case['state']}.", status=409)
            if not self.can(requester, "desk_operate"):
                raise DeskError("permission_denied", "The requester no longer holds desk_operate.", status=403)
            prop = self.store.proposal(case["id"], expect["proposal_revision"])
            if prop is None or prop["digest"] != expect["digest"] or case["proposal_revision"] != prop["revision"]:
                raise DeskError("stale_preview", "The proposal changed after this request. Review it again.",
                                status=409)
            return {"case": case, "proposal": prop}
        if case["state"] not in ("erp_draft_saved", "waiting_approval", "submitting"):
            raise DeskError("invalid_state", f"{case_number(case)} is {case['state']}.", status=409)
        if not self.can(requester, "desk_submit"):
            raise DeskError("permission_denied", "The requester no longer holds desk_submit.", status=403)
        return {"case": case}

    def submission_arguments(self, case: dict, view: dict) -> dict:
        """The exact content a person approves: everything the Console shows
        under 'Exactly what will be sent'."""
        return {"case": case_number(case), "case_id": case["id"], "company": view["company"],
                "doctype": view["doctype"], "erp_name": view["name"], "erp_version": view["erp_modified"],
                "party": view["party"], "reference": view["business_reference"], "currency": view["currency"],
                "lines": [{k: l.get(k) for k in ("item_code", "qty", "uom", "rate", "amount")} for l in view["lines"]],
                "totals": {k: view["totals"].get(k) for k in ("total", "total_taxes_and_charges", "grand_total")},
                "approval_digest": view["approval_digest"], "backend": view["backend"],
                "synthetic": view["synthetic"]}

    def record_draft(self, case_id: str, *, work_item_id: str, operation_id: str | None, view: dict, actor: str):
        diffs = view.get("differences") or []
        case = self.store.update_case(
            case_id, state="erp_draft_saved", erp_doctype=view["doctype"], erp_name=view["name"],
            erp_docstatus=view["docstatus"], erp_modified=view["erp_modified"], approval_digest=view["approval_digest"],
            content_digest=view["content_digest"], differences_json=diffs or None, event="draft.created", actor=actor,
            payload={"erp_name": view["name"], "deduplicated": view.get("deduplicated"), "differences": len(diffs)})
        self.store.link(case_id, work_item_id=work_item_id, action="draft", operation_id=operation_id,
                        erp_doctype=view["doctype"], erp_name=view["name"], erp_modified=view["erp_modified"],
                        docstatus=view["docstatus"], digest=view["approval_digest"], state="succeeded")
        return case

    def record_state(self, case_id: str, state: str, *, actor: str, event: str, **fields):
        return self.store.update_case(case_id, state=state, event=event, actor=actor, **fields)

    def record_submitted(self, case_id: str, *, work_item_id: str, operation_id: str | None, view: dict, actor: str):
        case = self.store.update_case(case_id, state="done", erp_docstatus=view["docstatus"],
                                      erp_modified=view["erp_modified"], content_digest=view["content_digest"],
                                      event="document.submitted", actor=actor,
                                      payload={"erp_name": view["name"], "docstatus": view["docstatus"]})
        self.store.link(case_id, work_item_id=work_item_id, action="submit", operation_id=operation_id,
                        erp_doctype=view["doctype"], erp_name=view["name"], erp_modified=view["erp_modified"],
                        docstatus=view["docstatus"], digest=view["approval_digest"], state="succeeded")
        return case

    # -- keeping the case in step with the SDK ------------------------------------------------------------------
    def sync(self, case: dict) -> dict:
        """Follow the case's work items: a denial, a lost answer or a stopped
        draft shows on the case even if the run that caused it crashed."""
        if case["state"] not in ("draft_ready", "erp_draft_saved", "waiting_approval", "submitting", "ambiguous"):
            return case
        try:
            from hubzoid.work_items import store as wi_store
        except ImportError:
            return case
        for link in reversed(self.store.links(case["id"])):
            if link["state"] in ("cancelled", "succeeded") or not link.get("work_item_id"):
                continue
            item = wi_store.get_item(self.hub_dir, link["work_item_id"])
            if item is None:
                continue
            ops = wi_store.list_ops(self.hub_dir, item["hub"], item_id=item["id"])
            op = ops[-1] if ops else None
            if op and op["state"] == "ambiguous" and case["state"] != "ambiguous":
                self.store.link(case["id"], work_item_id=item["id"], action=link["action"], operation_id=op["id"],
                                state="ambiguous")
                return self.store.update_case(case["id"], state="ambiguous", event="operation.marked_ambiguous",
                                              actor="desk", payload={"operation_id": op["id"]},
                                              error_code="erp_write_ambiguous",
                                              error_message="The ERP may have accepted this operation. Reconcile "
                                              "before retrying.")
            if op and op["state"] == "awaiting_approval" and case["state"] == "erp_draft_saved":
                return self.store.update_case(case["id"], state="waiting_approval", event="approval.requested",
                                              actor="desk", payload={"operation_id": op["id"]})
            if link["action"] == "submit" and op and op["state"] == "abandoned" \
                    and case["state"] in ("waiting_approval", "submitting", "ambiguous"):
                decision = (op.get("resolution_json") or {}).get("decision")
                self.store.link(case["id"], work_item_id=item["id"], action="submit", operation_id=op["id"],
                                state="denied" if decision == "deny" else "abandoned")
                return self.store.update_case(case["id"], state="erp_draft_saved",
                                              event="submission.denied" if decision == "deny" else "submission.abandoned",
                                              actor=(op.get("resolution_json") or {}).get("by") or "desk",
                                              error_code=None, error_message="Not submitted: "
                                              + ("the requester denied it." if decision == "deny" else "abandoned.")
                                              + " The ERP draft is unchanged.")
            if item["state"] in ("stopped", "failed") and link["state"] == "requested":
                self.store.link(case["id"], work_item_id=item["id"], action=link["action"], state=item["state"])
                if case["state"] in ("waiting_approval", "submitting"):
                    return self.store.update_case(case["id"], state="erp_draft_saved", event="submission.stopped",
                                                  actor="desk", error_code=item.get("reason"),
                                                  error_message=item.get("error"))
                if case["state"] == "draft_ready":
                    return self.store.update_case(case["id"], event="draft.stopped", actor="desk",
                                                  error_code=item.get("reason"), error_message=item.get("error"))
            if op and op["state"] == "succeeded" and case["state"] == "ambiguous":
                view = (op.get("result_json") or {})
                if view.get("name"):
                    if link["action"] == "draft":
                        return self.record_draft(case["id"], work_item_id=item["id"], operation_id=op["id"],
                                                 view=view, actor="desk")
                    return self.record_submitted(case["id"], work_item_id=item["id"], operation_id=op["id"],
                                                 view=view, actor="desk")
        return case

    # -- reconciliation and cancel --------------------------------------------------------------------------------
    def reconcile(self, actor: str, case_id: str) -> dict:
        """Read the ERP for the operation's marker and show the evidence. It
        never writes. The person who asked for the work then decides with the
        SDK's resolve (done with this evidence, retry or abandon)."""
        case = self.store.require_case(case_id, self.hub_id)
        if case.get("owner") != actor:
            self.require(actor, "desk_manage", "Reconciling a case")
        from hubzoid.work_items import store as wi_store

        out = {"case": case_number(case), "state": case["state"], "operations": []}
        writer = self.erp.writer()
        for link in self.store.links(case["id"]):
            item = wi_store.get_item(self.hub_dir, link["work_item_id"]) if link.get("work_item_id") else None
            if item is None:
                continue
            for op in wi_store.list_ops(self.hub_dir, item["hub"], item_id=item["id"]):
                entry = {"operation_id": op["id"], "action": op.get("action"), "state": op["state"],
                         "requester": op.get("requester"), "work_item_id": item["id"]}
                if link["action"] == "draft":
                    prop = self.store.proposal(case["id"], link.get("proposal_revision"))
                    ref = self.external_ref(case["id"], prop["revision"] if prop else 0)
                    found = writer.find(self._kind(case), ref)
                    entry["erp_documents"] = [{k: d[k] for k in ("name", "docstatus", "erp_modified", "content_digest")}
                                              for d in found]
                    entry["verdict"] = ("one_document" if len(found) == 1 else "none_found" if not found
                                        else "several_documents")
                    if len(found) == 1 and prop and prop.get("payload"):
                        entry["matches_proposal"] = not [d for d in writer._differences(
                            writer._expected(self._kind(case), _model(self._kind(case), prop["payload"])), _raw(writer, found[0]))
                            if d["field"] != "grand_total"]
                else:
                    try:
                        view = writer.get(self._kind(case), case["erp_name"])
                        entry["erp_documents"] = [{"name": view["name"], "docstatus": view["docstatus"],
                                                   "erp_modified": view["erp_modified"]}]
                        entry["verdict"] = "submitted" if view["docstatus"] == 1 else "still_draft"
                    except ERPError as exc:
                        entry["verdict"] = exc.code
                entry["next"] = ({"one_document": "The requester resolves it as done with this evidence: hubzoid "
                                  "operations resolve OP --decision done --evidence 'ERP NAME'",
                                  "submitted": "The requester resolves it as done with this evidence.",
                                  "none_found": "Nothing carries this operation's marker yet. The unique marker makes a "
                                  "retry safe: the requester resolves it as retry.",
                                  "still_draft": "The draft is not submitted. The requester resolves it as retry.",
                                  "several_documents": "An incident: more than one document carries the marker. Stop "
                                  "and investigate in the ERP."}).get(entry.get("verdict"), "Check the ERP.")
                out["operations"].append(entry)
        self.store.event(case["id"], "operation.reconcile_read", actor, {"operations": len(out["operations"])})
        return out

    def external_ref(self, case_id: str, revision: int) -> str:
        return f"{self.hub_id}:case:{case_id}:proposal:{revision}:draft"

    def cancel(self, actor: str, case_id: str, *, expected_revision: int, reason: str) -> dict:
        case = self.store.require_case(case_id, self.hub_id)
        if case.get("owner") != actor:
            self.require(actor, "desk_manage", "Cancelling someone else's case")
        if case["state"] in ("waiting_approval", "submitting"):
            raise DeskError("invalid_state", "Deny or settle the pending submission first.", status=409)
        self._cancel_pending(case, actor, "cancelled")
        self.store.release_business_key(case["id"])
        case = self.store.update_case(case["id"], expected_revision=expected_revision, state="cancelled",
                                      event="case.cancelled", actor=actor, payload={"reason": reason[:500]})
        note = " The ERP draft stays in the ERP." if case.get("erp_name") else ""
        return dict(self._summary(case), message=f"{case_number(case)} cancelled.{note}")

    # -- admin ---------------------------------------------------------------------------------------------------
    def health(self, actor: str) -> dict:
        self.require(actor, "desk_view", "Seeing desk health")
        counts = self.store.counts(self.hub_id)
        import time

        now = time.time()
        queue = {state: {"count": v["count"], "oldest_age_seconds": int(now - v["oldest"]) if v["oldest"] else None}
                 for state, v in counts.items()}
        cases = self.store.list_cases(self.hub_id, limit=100)
        duplicates = [self._summary(c) for c in cases if c["state"] == "duplicate"]
        ambiguous = [self._summary(c) for c in cases if c["state"] == "ambiguous"]
        errors = [self._summary(c) for c in cases if c["state"] == "error"]
        try:
            erp = self.erp.doctor(probe_resources=False)
            connection = {"backend": erp.get("backend"), "ready_read": erp["ready_read"], "ready_write": erp["ready_write"],
                          "ready_submit": erp["ready_submit"]}
        except Exception as exc:  # noqa: BLE001
            connection = {"error": type(exc).__name__}
        return {"queue": queue, "duplicates": duplicates, "ambiguous": ambiguous, "errors": errors,
                "intake_enabled": bool(self.settings()["DESK_INTAKE_ENABLED"]), "write_mode": self.write_mode(),
                "erp": connection}


def _model(kind, payload):
    from erp.write import clean_payload

    return clean_payload(kind, payload)[0]


def _raw(writer, view):
    return writer.rt.get_doc(view["doctype"], view["name"])
