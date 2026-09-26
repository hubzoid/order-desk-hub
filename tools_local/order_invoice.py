"""Chat tools for the Order & Invoice Desk.

Each tool acts as the signed-in person in the chat (never a name the model
gives) and calls the same desk service as the CLI, so permissions and states
are identical everywhere. Tools that change something take the case's
`expected_revision` or the exact digest the person saw.

`desk_approve_submission` is the chat approval card. `hitl.yaml` sets it to
`ask: always`, so it never runs until the person approves that exact call on
an inline card (the arguments show the ERP document, its version, lines,
totals and digest). Only then does it record that person's approval of the
waiting SDK operation, which still rechecks the digest, the person's access
and the ERP version before anything is submitted. The model can never
approve: the card is decided by a person through Hubzoid's request routes.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_HUB = Path(__file__).resolve().parents[1]
if str(_HUB) not in sys.path:
    sys.path.insert(0, str(_HUB))

from agents import function_tool  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

from desk import render, sample_channels  # noqa: E402
from desk.service import Desk, DeskError  # noqa: E402

sample_channels.register()


def _actor() -> str | None:
    from hubzoid import current_user

    user = current_user(_HUB)
    return user.email if user is not None else None


def _desk() -> Desk:
    return Desk(_HUB)


def _err(exc) -> str:
    detail = exc.detail() if hasattr(exc, "detail") else {"code": type(exc).__name__, "message": str(exc)}
    return json.dumps({"error": detail}, default=str)


def _run(fn):
    actor = _actor()
    if not actor:
        return json.dumps({"error": {"code": "unauthenticated", "message": "The desk acts only for a signed-in person."}})
    try:
        return json.dumps(fn(_desk(), actor), default=str)
    except DeskError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001 - the model gets a readable reason
        code = getattr(exc, "code", None)
        if code:
            return _err(exc)
        return json.dumps({"error": {"code": "desk_failed", "message": f"{type(exc).__name__}"}})


def _write_case_page(desk: Desk, view: dict) -> str | None:
    """Put the case page in the chat's document panel."""
    try:
        from hubzoid import _request_ctx
        from hubzoid import memory as memlib
        from hubzoid.tools.files import surface_artifact

        chat_id = _request_ctx.get_chat_id()
        if not chat_id:
            return None
        name = f"{view['number']}.html"
        folder = memlib.chat_artifact_dir(_HUB, chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(render.case_page(view), encoding="utf-8")
        surface_artifact(name)
        return name
    except Exception:  # noqa: BLE001 - the page is a convenience; the data is in the reply
        return None


def _brief(view: dict) -> dict:
    prop = view.get("proposal") or {}
    ev = prop.get("evidence") or {}
    return {
        "case": view["number"], "case_id": view["case_id"], "state": view["state"], "revision": view["revision"],
        "kind": view.get("kind"), "party": view.get("party"), "reference": view.get("reference"),
        "owner": view.get("owner"), "proposal_revision": view.get("proposal_revision"), "digest": view.get("digest"),
        "total": (ev.get("totals") or {}).get("computed"),
        "lines": [{"n": l["n"], "source": (l.get("source") or {}).get("text"), "loc": (l.get("source") or {}).get("loc"),
                   "item": (l.get("item") or {}).get("code"), "qty": (l.get("unit") or {}).get("qty"),
                   "uom": (l.get("unit") or {}).get("uom"), "rate": (l.get("rate") or {}).get("value"),
                   "amount": l.get("amount"),
                   "rules": [(l.get(k) or {}).get("rule", {}).get("text") for k in ("item", "unit", "rate") if l.get(k)]}
                  for l in ev.get("lines") or []],
        "questions": view.get("questions"), "warnings": [w["message"] for w in prop.get("warnings") or []],
        "erp": view.get("erp"), "error": view.get("error"), "duplicate_of": view.get("duplicate_of"),
        "actions": view.get("actions"), "write_mode": view.get("write_mode"), "backend": view.get("backend"),
        "synthetic": view.get("synthetic"),
    }


class SubmissionLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_code: str
    qty: str
    uom: str
    rate: str
    amount: str | None


class SubmissionTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: str | None
    total_taxes_and_charges: str | None
    grand_total: str | None


class SubmissionDocument(BaseModel):
    """The ERP document a person approves, exactly as stored for submission."""
    model_config = ConfigDict(extra="forbid")
    company: str
    doctype: str
    erp_name: str
    erp_version: str
    party: str
    reference: str | None
    currency: str
    lines: list[SubmissionLine]
    totals: SubmissionTotals


@function_tool
def desk_queue(state: str = "", mine: bool = False) -> str:
    """List desk cases, newest first: number, state, party, reference, total, owner and open questions.

    Args:
        state: Only this state (needs_owner, needs_clarification, draft_ready, erp_draft_saved,
            waiting_approval, done, duplicate, error, ambiguous), or empty for all.
        mine: Only the cases you own.
    """
    return _run(lambda d, a: d.list(a, state=state or None, mine=mine, limit=50))


@function_tool
def desk_case(case: str) -> str:
    """Show one case: every line with its source text and location, the matched item, unit, rate and the
    rule behind each, the questions, the ERP draft and the actions you can take. Also puts the full case
    page (source next to proposal) in the chat's document panel.

    Args:
        case: The case number (CASE-000003) or id.
    """
    def go(d, a):
        view = d.show(a, case)
        out = _brief(view)
        out["page"] = _write_case_page(d, view)
        return out
    return _run(go)


@function_tool
def desk_claim(case: str, expected_revision: int) -> str:
    """Claim a case so you own it. Only one person can own a case.

    Args:
        case: The case number or id.
        expected_revision: The case revision you saw (desk_case shows it).
    """
    return _run(lambda d, a: d.claim(a, case, expected_revision=expected_revision))


@function_tool
def desk_release(case: str, expected_revision: int, reason: str = "") -> str:
    """Give a case back to the queue. Pending draft or submit requests are cancelled.

    Args:
        case: The case number or id.
        expected_revision: The case revision you saw.
        reason: Why, in a few words.
    """
    return _run(lambda d, a: d.release(a, case, expected_revision=expected_revision, reason=reason))


@function_tool
def desk_answer(case: str, expected_revision: int, field: str, value: str, source_ref: str = "") -> str:
    """Answer one of the case's questions with a correction for this case only. It never changes a shared rule.

    Args:
        case: The case number or id.
        expected_revision: The case revision you saw.
        field: The question's field, for example lines[1].item_code, lines[2].qty, lines[1].uom, lines[1].rate,
            reference, delivery_date, due_date, party (Customer:ID or Supplier:ID), bill_to_confirmed (yes or
            no), lines[3].include (yes or no), lines[4].source (an element id, to add a missed line, then its
            item_code and qty), ack.uncovered.E4 (not_a_line), ack.unreadable.2 (nothing_missing).
        value: The value the person chose.
        source_ref: The element id the person points to as evidence, if any.
    """
    return _run(lambda d, a: d.correct(a, case, expected_revision=expected_revision,
                                       fields=[{"path": field, "value": value, "source_ref": source_ref or None}]))


@function_tool
def desk_rerun(case: str, reason: str) -> str:
    """Match a case's document again with the current rules, for example after an alias row was added.

    Args:
        case: The case number or id.
        reason: Why, in a few words.
    """
    return _run(lambda d, a: d.rerun(a, case, reason=reason))


@function_tool
def desk_create_draft(case: str, expected_revision: int, digest: str) -> str:
    """Save the case's proposal as an inert ERP draft (docstatus 0). Needs erp.submit set to draft or approve.
    A person finishes or submits it later. Returns the work item that does it.

    Args:
        case: The case number or id. You must own it and it must have no open questions.
        expected_revision: The case revision you saw.
        digest: The proposal digest you reviewed (desk_case shows it).
    """
    def go(d, a):
        out = d.request_draft(a, case, expected_revision=expected_revision, digest=digest)
        return _wait(d, a, out, until=("erp_draft_saved", "error", "ambiguous", "draft_ready"))
    return _run(go)


@function_tool
def desk_request_submission(case: str, expected_revision: int, digest: str, erp_version: str) -> str:
    """Ask to submit a saved ERP draft. Only in approve mode. Hubzoid then waits for you to approve the
    exact draft: show the person the returned approval details, then call desk_approve_submission.

    Args:
        case: The case number or id, in state erp_draft_saved, owned by you.
        expected_revision: The case revision you saw.
        digest: The ERP draft's approval digest you reviewed (desk_case shows it under erp).
        erp_version: The ERP draft's modified version you reviewed.
    """
    def go(d, a):
        out = d.request_submit(a, case, expected_revision=expected_revision, digest=digest, erp_version=erp_version)
        out = _wait(d, a, out, until=("waiting_approval", "erp_draft_saved", "error", "ambiguous", "done"))
        op = _waiting_operation(d, out.get("work_item_id"))
        if op:
            out["approval"] = {"operation_id": op["id"], "digest": op["args_digest"], "document": op["document"]}
        return out
    return _run(go)


@function_tool
def desk_approve_submission(case: str, operation_id: str, digest: str, document: SubmissionDocument) -> str:
    """Approve submitting the exact ERP draft shown. A person must approve this call on its card first;
    it is never approved by the assistant. Pass exactly what desk_request_submission returned.

    Args:
        case: The case number or id.
        operation_id: The waiting operation's id.
        digest: The operation's digest.
        document: The document as returned: company, doctype, erp_name, erp_version, party, reference,
            currency, lines and totals.
    """
    def go(d, a):
        from hubzoid.work_items import service as wi
        from hubzoid.work_items import store as wi_store

        row = wi_store.get_op(d.hub_dir, operation_id)
        if row is None:
            raise DeskError("not_found", "No such operation.", status=404)
        current = _document(d, row)
        if current != document.model_dump():
            raise DeskError("approval_stale", "The approval card does not match the stored submission. Ask for "
                            "the current details and approve those.", status=409, document=current)
        out = wi.approve(d.hub_dir, operation_id, a, digest=digest, surface="chat")
        return {"operation": out["operation"]["state"], "work_item": (out.get("work_item") or {}).get("state"),
                "message": "Approved. Hubzoid rechecks the digest, your access and the ERP version, then submits."}
    return _run(go)


@function_tool
def desk_deny_submission(case: str, operation_id: str, reason: str) -> str:
    """Deny a waiting submission. The ERP draft stays a draft, unchanged.

    Args:
        case: The case number or id.
        operation_id: The waiting operation's id.
        reason: Why, in a few words.
    """
    def go(d, a):
        from hubzoid.work_items import service as wi

        out = wi.deny(d.hub_dir, operation_id, a, reason=reason, surface="chat")
        d.show(a, case)       # the case follows the decision
        return {"operation": out["operation"]["state"], "message": "Denied. Nothing was submitted."}
    return _run(go)


@function_tool
def desk_reconcile(case: str) -> str:
    """For a case whose ERP write has an unknown outcome: read the ERP for the operation's unique marker and
    show what is there. It writes nothing. The person who asked for the work then resolves the operation.

    Args:
        case: The case number or id.
    """
    return _run(lambda d, a: d.reconcile(a, case))


@function_tool
def desk_cancel(case: str, expected_revision: int, reason: str) -> str:
    """Cancel a case. An ERP draft it created stays in the ERP.

    Args:
        case: The case number or id.
        expected_revision: The case revision you saw.
        reason: Why.
    """
    return _run(lambda d, a: d.cancel(a, case, expected_revision=expected_revision, reason=reason))


@function_tool
def desk_health() -> str:
    """Queue health for administrators: cases per state and the oldest age, duplicates, uncertain ERP writes,
    errors, the ERP connection and whether intake is on."""
    return _run(lambda d, a: d.health(a))


@function_tool
def desk_submit_document(filename: str, party: str = "", note: str = "") -> str:
    """Send a file the person attached in this chat to the desk as a new case (an upload).

    Args:
        filename: The attached file's name.
        party: Customer:ID or Supplier:ID when the person says who it is from, else empty.
        note: Anything the person said about it.
    """
    def go(d, a):
        from desk.uploads import submit_chat_upload

        return submit_chat_upload(d, a, filename=filename, party=party, note=note)
    return _run(go)


def _wait(d, actor, out, *, until, seconds: float = 25.0):
    deadline = time.time() + seconds
    view = None
    while time.time() < deadline:
        view = d.show(actor, out["case_id"])
        if view["state"] in until and (view["state"] != "draft_ready" or view.get("error")):
            break
        time.sleep(1.0)
    if view is not None:
        out = dict(out, state=view["state"], erp=view.get("erp"), error=view.get("error"))
    return out


def _document(d, row) -> dict:
    from hubzoid.work_items import operations as ops

    args = ops.row_arguments(d.hub_dir, row)
    return {k: args.get(k) for k in ("company", "doctype", "erp_name", "erp_version", "party", "reference",
                                     "currency", "lines", "totals")}


def _waiting_operation(d, work_item_id):
    if not work_item_id:
        return None
    from hubzoid.work_items import store as wi_store

    deadline = time.time() + 15
    while time.time() < deadline:
        for op in wi_store.list_ops(d.hub_dir, d.hub_id, item_id=work_item_id):
            if op["state"] == "awaiting_approval":
                return dict(op, document=_document(d, op))
        time.sleep(1.0)
    return None
