"""The Order & Invoice Desk workflows. The bodies live in `desk/flow.py`.

* `order_invoice_intake`: one message or upload becomes a checked case.
  Routes `triage` (from a channel or an upload) and `preview` (a dry run that
  creates no case for the queue and never writes to the ERP).
* `order_invoice_case`: one ERP action a person asked for. Routes `draft`
  (an inert ERP draft) and `submit` (approve mode only, after that person
  approves the exact draft).
"""
import sys
from pathlib import Path

_HUB = Path(__file__).resolve().parents[2]
if str(_HUB) not in sys.path:
    sys.path.insert(0, str(_HUB))

from hubzoid import hub, workflow  # noqa: E402
from hubzoid.work_items import Route  # noqa: E402

from desk import flow, sample_channels  # noqa: E402

sample_channels.register()


@workflow(work_items={"triage": Route(stages=("parse", "extract", "match", "extract_failed"), actions=()),
                      "preview": Route(stages=("parse", "extract", "match", "extract_failed"), actions=())})
def order_invoice_intake():
    """Read an incoming order or supplier invoice and prepare a checked case. Never writes to the ERP."""
    return flow.intake(hub)


@workflow(work_items={"draft": Route(stages=("check", "draft_refused", "record"), actions=("erp.create_draft",)),
                      "submit": Route(stages=("check", "prepare", "waiting", "submit_refused", "record"),
                                      actions=("erp.submit",))})
def order_invoice_case():
    """Save a case's inert ERP draft, or submit it after its requester approves the exact draft."""
    return flow.case(hub)
