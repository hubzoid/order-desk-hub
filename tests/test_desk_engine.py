"""The desk's draft and submit work items on the real Hubzoid workflow engine.

The engine runs in a child process (it is a process-global singleton), with
this hub's real workflows, against the sample ERP. The test drives it the way
the CLI and the chat do: through the desk service and the SDK's work-item
decisions. Covered: an inert draft, an approve-mode submit after the
requester's approval of the exact draft, one submit for two decisions, a
denial, asking again after a denial, a draft changed after approval, a crash
right after the ERP saved the draft, a draft changed in the ERP before that
crash was recovered, and a lost answer reconciled by the requester.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.desk_helpers import ROOT, bind_samples, triage

pytestmark = [pytest.mark.engine, pytest.mark.skipif(os.name == "nt", reason="uses SIGKILL")]
OWNER = "admin@localhost"
ENGINE = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    from hubzoid.workflows import runtime
    runtime.init(sys.argv[1], hub_name=Path(sys.argv[1]).name)
    runtime.load_workflows(sys.argv[1])
    runtime.launch()
    runtime._OWNER.heartbeat(runtime.ready_record())
    print("READY", flush=True)
    stop = Path(sys.argv[2])
    beat = time.time()
    while not stop.exists():
        time.sleep(0.2)
        if time.time() - beat > 5:
            runtime._OWNER.heartbeat(runtime.ready_record())
            beat = time.time()
    runtime.shutdown()
''')
FILES = ["AGENTS.md", "hitl.yaml", "settings.yaml", "hub.settings.yaml"]
DIRS = ["agents", "desk", "erp", "knowledge", "workflows/order_invoice", "connectors"]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    for var in ("WEBUI_AUTH", "HUBZOID_AUTH", "DATABASE_URL", "HUBZOID_OPERATIONAL_DB"):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path / "order-invoice-desk"
    root.mkdir()
    for f in FILES:
        shutil.copy2(ROOT / f, root / f)
    for d in DIRS:
        shutil.copytree(ROOT / d, root / d, ignore=shutil.ignore_patterns("__pycache__"))
    # These engine cases intentionally exercise the optional sample ERP,
    # unlike a fresh inbox-only installation. Never change production defaults
    # merely to make an ERP fixture enter the old path.
    import yaml
    settings_path = root / "hub.settings.yaml"
    settings = yaml.safe_load(settings_path.read_text())
    settings["values"]["DESK_ERP_ENABLED"] = True
    settings_path.write_text(yaml.safe_dump(settings, sort_keys=False))
    (root / "work-items.yaml").write_text("version: 1\nwrite_modes:\n  erp.submit: approve\n")
    from hubzoid.access import store_for

    store_for(root).bootstrap(admin_subjects=[OWNER])
    return root


class Engine:
    def __init__(self, hub: Path, tmp: Path):
        self.stop = tmp / f"stop-{time.time_ns()}"
        script = tmp / "engine.py"
        script.write_text(ENGINE)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, (str(hub), os.environ.get("PYTHONPATH", "")))))
        self.proc = subprocess.Popen([sys.executable, str(script), str(hub), str(self.stop)], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, env=env)
        deadline = time.time() + 90
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if line.startswith("READY"):
                return
            if not line and self.proc.poll() is not None:
                break
        raise RuntimeError("the engine did not start: " + (self.proc.stdout.read() or ""))

    def wait_exit(self, seconds=30):
        self.proc.wait(seconds)

    def close(self):
        if self.proc.poll() is None:
            self.stop.write_text("1")
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.send_signal(signal.SIGKILL)
                self.proc.wait(10)


def _desk(hub):
    from desk.service import Desk

    d = Desk(hub)
    bind_samples(d)
    return d


def _wait(desk, case_id, states, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        case = desk.sync(desk.store.get_case(case_id))
        if case["state"] in states:
            return case
        time.sleep(0.5)
    raise AssertionError(f"case stayed {desk.store.get_case(case_id)['state']}")


def _waiting_op(desk, item_id, timeout=60):
    from hubzoid.work_items import store as wi_store

    deadline = time.time() + timeout
    while time.time() < deadline:
        for op in wi_store.list_ops(desk.hub_dir, desk.hub_id, item_id=item_id):
            if op["state"] == "awaiting_approval":
                return op
        time.sleep(0.5)
    raise AssertionError("no operation waits for approval")


def _ready_case(desk, event="OID-001"):
    out = triage(desk, event)
    case = desk.store.get_case(out["case_id"])
    claimed = desk.claim(OWNER, case["id"], expected_revision=case["revision"])
    return desk.store.get_case(claimed["case_id"])


def _draft(desk, case):
    prop = desk.store.proposal(case["id"])
    out = desk.request_draft(OWNER, case["id"], expected_revision=case["revision"], digest=prop["digest"])
    return out, _wait(desk, case["id"], ("erp_draft_saved", "error", "ambiguous"))


def _sales_orders(desk, marker_case):
    erp = desk.erp
    t = erp.transport()
    return [d for d in t.list_docs("Sales Order", filters=[["po_no", "=", marker_case]], fields=["docstatus"],
                                   start=0, limit=20)]


def test_draft_then_approved_submit_once(hub, tmp_path):
    from hubzoid.work_items import service as wi
    from hubzoid.work_items.errors import WorkItemError

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        case = _ready_case(desk)
        _, case = _draft(desk, case)
        assert case["state"] == "erp_draft_saved" and case["erp_docstatus"] == 0, case
        view = desk.erp.writer().get("sales_order", case["erp_name"])
        assert view["docstatus"] == 0 and view["totals"]["grand_total"] == "276.6"
        out = desk.request_submit(OWNER, case["id"], expected_revision=case["revision"],
                                  digest=case["approval_digest"], erp_version=case["erp_modified"])
        op = _waiting_op(desk, out["work_item_id"])
        assert _wait(desk, case["id"], ("waiting_approval",))["state"] == "waiting_approval"
        from hubzoid.work_items import operations as ops

        args = ops.row_arguments(hub, op)
        assert args["erp_name"] == case["erp_name"] and args["totals"]["grand_total"] == "276.6"
        assert [l["item_code"] for l in args["lines"]] == ["SYN-ITEM-001", "SYN-ITEM-003"]
        wi.approve(hub, op["id"], OWNER, digest=op["args_digest"], surface="test")
        done = _wait(desk, case["id"], ("done", "erp_draft_saved", "error"))
        assert done["state"] == "done" and done["erp_docstatus"] == 1
        with pytest.raises(WorkItemError) as err:           # a second decision never submits again
            wi.approve(hub, op["id"], OWNER, digest=op["args_digest"], surface="test")
        assert err.value.code == "not_waiting"
        docs = _sales_orders(desk, "HF-7010")
        assert len(docs) == 1 and int(docs[0]["docstatus"]) == 1
    finally:
        engine.close()


def test_deny_leaves_the_draft_inert(hub, tmp_path):
    from hubzoid.work_items import service as wi

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        _, case = _draft(desk, _ready_case(desk, "OID-002"))
        out = desk.request_submit(OWNER, case["id"], expected_revision=case["revision"],
                                  digest=case["approval_digest"], erp_version=case["erp_modified"])
        op = _waiting_op(desk, out["work_item_id"])
        wi.deny(hub, op["id"], OWNER, reason="wrong customer reference", surface="test")
        back = _wait(desk, case["id"], ("erp_draft_saved",))
        assert "denied" in (back["error_message"] or "")
        assert desk.erp.writer().get("sales_order", case["erp_name"])["docstatus"] == 0
    finally:
        engine.close()


def test_draft_changed_after_approval_never_submits(hub, tmp_path):
    from hubzoid.work_items import service as wi
    from hubzoid.work_items import store as wi_store

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        _, case = _draft(desk, _ready_case(desk, "OID-003"))
        out = desk.request_submit(OWNER, case["id"], expected_revision=case["revision"],
                                  digest=case["approval_digest"], erp_version=case["erp_modified"])
        op = _waiting_op(desk, out["work_item_id"])
        # Someone edits the draft in the ERP after the person saw it.
        desk.erp.transport().edit_draft("Sales Order", case["erp_name"], {"delivery_date": "2000-01-31"})
        wi.approve(hub, op["id"], OWNER, digest=op["args_digest"], surface="test")
        deadline = time.time() + 60
        while time.time() < deadline:
            row = wi_store.get_op(hub, op["id"])
            if row["state"] == "awaiting_approval" and not row.get("approval_json"):
                break
            time.sleep(0.5)
        assert row["state"] == "awaiting_approval" and not row.get("approval_json")     # a fresh approval is needed
        assert desk.erp.writer().get("sales_order", case["erp_name"])["docstatus"] == 0
        wi.approve(hub, op["id"], OWNER, digest=op["args_digest"], surface="test")     # approving again ...
        deadline = time.time() + 60
        while time.time() < deadline and wi_store.get_op(hub, op["id"])["state"] not in ("rejected", "succeeded"):
            time.sleep(0.5)
        assert wi_store.get_op(hub, op["id"])["state"] == "rejected"                    # ... the old digest is stale
        assert desk.erp.writer().get("sales_order", case["erp_name"])["docstatus"] == 0
    finally:
        engine.close()


def test_crash_after_erp_saved_the_draft_recovers_one_document(hub, tmp_path):
    engine = Engine(hub, tmp_path)
    desk = _desk(hub)
    case = _ready_case(desk, "OID-004")
    (hub / ".hubzoid" / "data" / "erp" / "sample-faults.json").write_text(json.dumps([["insert", "commit_then_crash"]]))
    prop = desk.store.proposal(case["id"])
    desk.request_draft(OWNER, case["id"], expected_revision=case["revision"], digest=prop["digest"])
    engine.wait_exit(90)                       # the engine died right after the ERP committed
    assert desk.store.get_case(case["id"])["state"] == "draft_ready"
    engine = Engine(hub, tmp_path)             # recovery: the operation was "sending"
    try:
        done = _wait(desk, case["id"], ("erp_draft_saved", "ambiguous", "error"), timeout=120)
        assert done["state"] == "erp_draft_saved", done
        found = desk.erp.writer().find("sales_order", desk.external_ref(case["id"], prop["revision"]))
        assert len(found) == 1 and found[0]["name"] == done["erp_name"]
        assert len(_sales_orders(desk, "CC-125")) == 1
    finally:
        engine.close()


def test_lost_answer_waits_for_the_requester_then_settles(hub, tmp_path):
    from hubzoid.work_items import service as wi
    from hubzoid.work_items import store as wi_store

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        case = _ready_case(desk, "OID-005")
        (hub / ".hubzoid" / "data" / "erp" / "sample-faults.json").write_text(json.dumps([["insert", "commit_then_lose"]]))
        out, settled = _draft(desk, case)
        assert settled["state"] == "ambiguous"
        evidence = desk.reconcile(OWNER, case["id"])
        entry = next(o for o in evidence["operations"] if o["state"] == "ambiguous")
        assert entry["verdict"] == "one_document"
        name = entry["erp_documents"][0]["name"]
        wi.resolve(hub, entry["operation_id"], OWNER, decision="done", evidence=f"ERP {name} carries the marker",
                   surface="test")
        done = _wait(desk, case["id"], ("erp_draft_saved", "error"))
        assert done["state"] == "erp_draft_saved" and done["erp_name"] == name
        assert done["error_code"] is None and done["error_message"] is None     # the lost answer is settled
        assert len(_sales_orders(desk, "HF-7011")) == 1
        assert wi_store.get_op(hub, entry["operation_id"])["state"] == "resolved_done"
    finally:
        engine.close()


def test_denied_then_asked_again_is_a_new_approval(hub, tmp_path):
    from hubzoid.work_items import service as wi

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        _, case = _draft(desk, _ready_case(desk, "OID-001"))
        first = desk.request_submit(OWNER, case["id"], expected_revision=case["revision"],
                                    digest=case["approval_digest"], erp_version=case["erp_modified"])
        op = _waiting_op(desk, first["work_item_id"])
        wi.deny(hub, op["id"], OWNER, reason="checking the delivery date first", surface="test")
        case = _wait(desk, case["id"], ("erp_draft_saved",))
        _wait_item_done(hub, first["work_item_id"])
        again = desk.request_submit(OWNER, case["id"], expected_revision=case["revision"],
                                    digest=case["approval_digest"], erp_version=case["erp_modified"])
        assert again["work_item_id"] != first["work_item_id"] and again["created"] is True
        op2 = _waiting_op(desk, again["work_item_id"])
        wi.approve(hub, op2["id"], OWNER, digest=op2["args_digest"], surface="test")
        done = _wait(desk, case["id"], ("done", "error"))
        assert done["state"] == "done" and done["erp_docstatus"] == 1
        assert done["error_code"] is None and not done["error_message"]         # the denial is history now
    finally:
        engine.close()


def test_a_draft_changed_before_recovery_is_never_taken_as_done(hub, tmp_path):
    engine = Engine(hub, tmp_path)
    desk = _desk(hub)
    case = _ready_case(desk, "OID-004")
    (hub / ".hubzoid" / "data" / "erp" / "sample-faults.json").write_text(json.dumps([["insert", "commit_then_crash"]]))
    prop = desk.store.proposal(case["id"])
    desk.request_draft(OWNER, case["id"], expected_revision=case["revision"], digest=prop["digest"])
    engine.wait_exit(90)                       # the ERP saved the draft, then the engine died
    saved = desk.erp.writer().find("sales_order", desk.external_ref(case["id"], prop["revision"]))[0]
    desk.erp.transport().edit_draft("Sales Order", saved["name"], {"delivery_date": "2000-01-31"})
    engine = Engine(hub, tmp_path)             # recovery finds a draft that differs from the proposal
    try:
        settled = _wait(desk, case["id"], ("erp_draft_saved", "ambiguous", "error"), timeout=120)
        assert settled["state"] == "ambiguous", settled
        entry = next(o for o in desk.reconcile(OWNER, case["id"])["operations"] if o["state"] == "ambiguous")
        assert entry["verdict"] == "one_document_differs" and entry["matches_proposal"] is False
        assert any(d["field"] == "delivery_date" for d in entry["differences"])
    finally:
        engine.close()


def _wait_item_done(hub, item_id, timeout=60):
    from hubzoid.work_items import store as wi_store

    deadline = time.time() + timeout
    while time.time() < deadline:
        if wi_store.get_item(hub, item_id)["state"] in wi_store.TERMINAL:
            return
        time.sleep(0.5)
    raise AssertionError("the work item did not finish")


def test_an_uncertain_draft_sent_again_by_its_requester_makes_one_document(hub, tmp_path):
    from hubzoid.work_items import service as wi
    from hubzoid.work_items import store as wi_store

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        case = _ready_case(desk, "OID-003")
        (hub / ".hubzoid" / "data" / "erp" / "sample-faults.json").write_text(
            json.dumps([["insert", "lose_before_commit"]]))
        _, settled = _draft(desk, case)
        assert settled["state"] == "ambiguous"
        entry = next(o for o in desk.reconcile(OWNER, case["id"])["operations"] if o["state"] == "ambiguous")
        assert entry["verdict"] == "none_found"
        wi.resolve(hub, entry["operation_id"], OWNER, decision="retry", evidence="nothing carries the marker",
                   acknowledge_duplicate_risk=True, surface="test")
        done = _wait(desk, case["id"], ("erp_draft_saved", "error", "draft_ready"))
        assert done["state"] == "erp_draft_saved" and done["error_code"] is None, done
        assert len(_sales_orders(desk, "PC-0050")) == 1
        assert wi_store.get_op(hub, entry["operation_id"])["state"] == "succeeded"
    finally:
        engine.close()


def test_write_modes_gate_the_actions(hub, tmp_path):
    from desk.service import DeskError

    desk = _desk(hub)
    case = _ready_case(desk, "OID-006")
    (hub / "work-items.yaml").write_text("version: 1\nwrite_modes:\n  erp.submit: \"off\"\n")
    prop = desk.store.proposal(case["id"])
    with pytest.raises(DeskError) as err:
        desk.request_draft(OWNER, case["id"], expected_revision=case["revision"], digest=prop["digest"])
    assert err.value.code == "write_mode_off"
    (hub / "work-items.yaml").write_text("version: 1\nwrite_modes:\n  erp.submit: draft\n")
    assert "create_draft" in desk.actions(OWNER, desk.store.get_case(case["id"]))
    assert "request_submit" not in desk.actions(OWNER, dict(desk.store.get_case(case["id"]), state="erp_draft_saved"))


@pytest.mark.slow
def test_uncertain_invoice_retry_after_policy_change_can_still_resolve_late_insert(hub, tmp_path):
    """Real SDK state/resolve and workflow replay; only ERP is synthetic."""
    from hubzoid.work_items import service as wi
    from hubzoid.work_items import store as wi_store

    engine = Engine(hub, tmp_path)
    try:
        desk = _desk(hub)
        case = _ready_case(desk, "OID-009")
        prop = desk.store.proposal(case["id"])
        ref = desk.external_ref(case["id"], prop["revision"])
        (hub / ".hubzoid/data/erp/sample-faults.json").write_text(json.dumps([["insert", "lose_before_commit"]]))
        _, settled = _draft(desk, case)
        assert settled["state"] == "ambiguous"
        entry = next(o for o in desk.reconcile(OWNER, case["id"])["operations"] if o["state"] == "ambiguous")
        assert entry["verdict"] == "none_found"
        op_id = entry["operation_id"]
        initial = wi_store.get_op(hub, op_id)
        fence = desk.store.meta(desk._invoice_fence_key())
        assert fence and len(initial["attempts_json"]) == 1
        policy = hub / "knowledge/invoice-policy.md"
        policy.write_text(policy.read_text() + "\nOwner reviewed a new policy revision.\n")
        wi.resolve(hub, op_id, OWNER, decision="retry", evidence="No marker found yet; retry acknowledged",
                   acknowledge_duplicate_risk=True, surface="test")
        deadline = time.time() + 60
        while time.time() < deadline:
            op = wi_store.get_op(hub, op_id)
            if len(op.get("attempts_json") or []) >= 2 and op["state"] != "sending":
                break
            time.sleep(0.2)
        assert op["state"] == "ambiguous", op
        assert len(op["attempts_json"]) == 2 and "rules_changed" in op["error"]
        assert desk.store.meta(desk._invoice_fence_key()) == fence
        assert desk.store.get_case(case["id"])["error_code"] != "draft_not_saved"
        # Simulate the original request committing after its retry was blocked.
        late = desk.erp.writer().create_draft(prop["kind"], prop["payload"], ref)
        proof = desk.reconcile(OWNER, case["id"])
        assert next(o for o in proof["operations"] if o["operation_id"] == op_id)["verdict"] == "one_document"
        wi.resolve(hub, op_id, OWNER, decision="done", evidence=f"ERP {late['name']} carries original marker",
                   surface="test")
        done = _wait(desk, case["id"], ("erp_draft_saved", "error"))
        assert done["state"] == "erp_draft_saved" and done["erp_name"] == late["name"]
        assert wi_store.get_op(hub, op_id)["state"] == "resolved_done"
        assert desk.store.meta(desk._invoice_fence_key()) is None
    finally:
        engine.close()
