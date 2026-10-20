"""The two workflow bodies, kept here so they are importable and testable.

`intake(hub)`: one incoming message or upload, as an SDK work item. Saved
stages: parse, extract (the desk-extractor station), match. It ends at a case
waiting for an owner, a question, a duplicate or an error. It never writes to
the ERP: an unattended run has nobody who could own a financial write.

`case(hub)`: one ERP action a person asked for, as an SDK work item whose
requester is that person.

* route `draft`: `hub.operation.run` saves an inert draft through the ERP
  contract (`create_draft`). Allowed when `erp.submit` is `draft` or
  `approve`, checked again right before the write together with the owner,
  the requester's grant, the proposal digest and (for a bill) what the
  receipts still allow. Recovery is `reconcile`: the operation's unique marker
  in the ERP shows whether a lost request was applied, and a found draft goes
  through the same checks as a fresh one (`writer.recover`). A missing draft
  is retried only where the marker is proven unique in the ERP.
* route `submit`: `hub.operation.run(actions="erp.submit")`. In `approve` mode
  the SDK waits for the requester to approve the exact content (company,
  party, lines, totals, ERP name, version and digest), then rechecks the
  digest, the approver's access and the draft's version before
  `submit_approved` runs.
"""
from __future__ import annotations

from decimal import Decimal

from erp.errors import ERPError, NotDispatched, Refused, WriteUncertain

from .service import Desk, DeskError

RETRY_CLARIFICATION = ("Return one JSON object that matches the schema exactly: every key present, null where the "
                       "document does not say, numbers as plain decimal strings, and each source_ref an element id "
                       "such as E3 that holds the value.")


def intake(hub) -> dict:
    from hubzoid.stations import StationError, StationOutputError

    desk = Desk(_hub_dir(hub))
    payload = hub.work_item.input or {}
    preview = hub.work_item.route == "preview"
    parsed = hub.work_item.checkpoint("parse", lambda: desk.stage_parse(payload, work_item_id=hub.work_item.id,
                                                                         preview=preview))
    if parsed.get("stop"):
        return {"case_id": parsed["case_id"], "stopped": parsed["stop"]}
    case_id = parsed["case_id"]
    try:
        try:
            output = hub.call_agent(desk.extraction_task(parsed), agent="desk-extractor", stage="extract")
        except StationOutputError as exc:
            output = hub.retry_station(exc.call_id, RETRY_CLARIFICATION)
    except StationError as exc:
        hub.work_item.checkpoint("extract_failed", lambda: desk._error(
            desk.store.get_case(case_id), "extraction_failed", f"The extractor station did not finish ({exc.code}). "
            "Check the model login with `hubzoid stations doctor .`, then restart this work item "
            "(hubzoid work-items restart . ITEM --reason ...).") or True)
        raise
    return hub.work_item.checkpoint("match", lambda: desk.stage_match(
        case_id, output, elements=parsed["elements"], parse_problems=parsed.get("problems")))


def _hub_dir(hub):
    from hubzoid.workflows import context

    return context._ctx().hub_dir


def case(hub) -> dict:
    from hubzoid.work_items import APPLIED, SAFE_TO_RETRY, UNKNOWN, NotSent, Reconciled
    from hubzoid.work_items import Rejected as SdkRejected

    desk = Desk(_hub_dir(hub))
    item = hub.work_item
    data = item.input or {}
    requester = item.requester
    if item.route == "draft":
        ctx = item.checkpoint("check", lambda: _jsonable(desk.begin(
            data["case_id"], action="draft", requester=requester, work_item_id=item.id, expect=data)))
        case_row, prop = ctx["case"], ctx["proposal"]
        kind = prop["kind"]
        ref = desk.external_ref(case_row["id"], prop["revision"])
        writer = desk.erp.writer()

        def dispatch(arguments):
            try:
                desk.recheck(case_row["id"], action="draft", requester=requester, expect=data,
                             payload=arguments["payload"])
            except (DeskError, ERPError) as exc:      # checked before anything was sent
                raise NotSent(f"{exc.code}: {exc.message}") from None
            try:
                return desk.dispatch_draft(case_row["id"], requester=requester, expect=data, arguments=arguments, writer=writer)
            except DeskError as exc:
                raise NotSent(f"{exc.code}: {exc.message}") from None
            except NotDispatched as exc:
                raise NotSent(f"{exc.code}: {exc.message}") from None
            except Refused as exc:
                raise SdkRejected(f"{exc.code}: {exc.message}") from None
            except WriteUncertain:
                raise
            except ERPError as exc:
                if exc.code in ("erp_writes_disabled", "company_denied", "invalid_payload", "duplicate_found",
                                "operation_conflict", "erp_validation_failed", "write_not_allowed"):
                    raise NotSent(f"{exc.code}: {exc.message}") from None
                raise

        def reconcile(arguments):
            found = writer.recover(arguments["kind"], arguments["payload"], arguments["external_ref"])
            if found["status"] == "applied":
                return Reconciled(APPLIED, result=_jsonable(found["view"]),
                                  evidence={"erp_name": found["view"]["name"], "docstatus": found["view"]["docstatus"],
                                            "differences": len(found["view"].get("differences") or [])})
            if found["status"] == "absent":
                try:
                    # Resending the identical draft can never make a second one only
                    # because the ERP refuses a second document with this marker.
                    writer.require_unique_marker(arguments["kind"])
                except ERPError as exc:
                    return Reconciled(UNKNOWN, evidence={"erp_documents": 0, "marker_unique": False, "error": exc.code})
                return Reconciled(SAFE_TO_RETRY, evidence={"erp_documents": 0, "marker_unique": True})
            # A draft that differs, is no longer a draft, or several documents: a person decides.
            return Reconciled(UNKNOWN, evidence={"status": found["status"], "erp_documents": found["names"]})

        try:
            view = hub.operation.run(
                "erp_create_draft", target={"case": case_row["id"], "kind": kind},
                arguments={"case": case_row["id"], "kind": kind, "proposal_revision": prop["revision"],
                           "payload": prop["payload"], "external_ref": ref, "proposal_digest": prop["digest"]},
                recovery="reconcile", reconcile=reconcile, dispatch=dispatch, actions="erp.create_draft")
        except NotSent as exc:
            item.checkpoint("draft_refused", lambda: desk.record_state(
                case_row["id"], "draft_ready", actor="desk", event="draft.refused", error_code="draft_not_saved",
                error_message=str(exc)[:500])["revision"])
            raise
        if not (view or {}).get("name"):
            # A person resolved an uncertain create as done: the ERP itself says which
            # draft it was, checked like a fresh one (a difference stays for review).
            found = writer.recover(kind, prop["payload"], ref)
            if found["view"] is None:
                raise DeskError("reconcile_needed", f"The ERP shows {len(found['names'])} drafts for this operation. "
                                "Reconcile the case before going on.", status=409)
            view = found["view"]
        return item.checkpoint("record", lambda: desk._summary(desk.record_draft(
            case_row["id"], work_item_id=item.id, operation_id=None, view=_jsonable(view), actor=requester)))

    # -- submit --------------------------------------------------------------------------------------
    ctx = item.checkpoint("check", lambda: _jsonable(desk.begin(
        data["case_id"], action="submit", requester=requester, work_item_id=item.id, expect=data)))
    case_row = ctx["case"]
    kind = case_row["kind"]
    writer = desk.erp.writer()
    view = item.checkpoint("prepare", lambda: _jsonable(writer.get(kind, data["erp_name"])))
    if view["approval_digest"] != data["digest"]:
        raise DeskError("stale_preview", "The ERP draft changed after the request. Ask again.", status=409)
    arguments = desk.submission_arguments(case_row, view)
    item.checkpoint("waiting", lambda: desk.record_state(case_row["id"], "waiting_approval", actor=requester,
                                                         event="approval.requested")["revision"])

    def target_version():
        return writer.get(kind, data["erp_name"])["approval_digest"]

    def dispatch(arguments):
        try:
            desk.recheck(case_row["id"], action="submit", requester=requester, expect=data)
        except (DeskError, ERPError) as exc:          # checked before anything was sent
            raise NotSent(f"{exc.code}: {exc.message}") from None
        desk.record_state(case_row["id"], "submitting", actor=requester, event="document.submitting")
        try:
            return writer.submit_approved(kind, arguments["erp_name"], arguments["approval_digest"])
        except NotDispatched as exc:
            raise NotSent(f"{exc.code}: {exc.message}") from None
        except Refused as exc:
            raise SdkRejected(f"{exc.code}: {exc.message}") from None
        except WriteUncertain:
            raise
        except ERPError as exc:
            if exc.code in ("approval_stale", "already_submitted", "submit_not_allowed", "erp_writes_disabled",
                            "not_found"):
                raise SdkRejected(f"{exc.code}: {exc.message}") from None
            raise

    def reconcile(arguments):
        current = writer.get(kind, arguments["erp_name"])
        if current["docstatus"] == 1:
            return Reconciled(APPLIED, result=current, evidence={"erp_name": current["name"], "docstatus": 1})
        if current["docstatus"] == 0 and current["approval_digest"] == arguments["approval_digest"]:
            return Reconciled(SAFE_TO_RETRY, evidence={"docstatus": 0})
        return Reconciled(UNKNOWN, evidence={"docstatus": current["docstatus"]})

    try:
        result = hub.operation.run(
            "erp_submit", target={"doctype": view["doctype"], "name": view["name"]}, arguments=arguments,
            recovery="reconcile", reconcile=reconcile, dispatch=dispatch, actions="erp.submit",
            target_version=target_version)
    except Exception as exc:     # a denial, a refusal or a revoked submitter (waiting is BaseException)
        item.checkpoint("submit_refused", lambda: desk.record_state(
            case_row["id"], "erp_draft_saved", actor="desk", event="submission.refused",
            error_code="not_submitted", error_message=f"Not submitted: {exc}"[:500])["revision"])
        raise
    if not (result or {}).get("name"):
        # Resolved as done by the requester after an uncertain submit: read what the ERP holds.
        result = writer.get(kind, data["erp_name"])
        if result["docstatus"] != 1:
            raise DeskError("reconcile_needed", f"{data['erp_name']} is not submitted in the ERP. Reconcile again.",
                            status=409)
    return item.checkpoint("record", lambda: desk._summary(desk.record_submitted(
        case_row["id"], work_item_id=item.id, operation_id=None, view=_jsonable(result), actor=requester)))


def _jsonable(value):
    import json

    return json.loads(json.dumps(value, default=lambda v: str(v) if isinstance(v, Decimal) else str(v)))
