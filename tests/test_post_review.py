"""Model-free regressions for the independent Opus review; no provider claims."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from desk import records, reports, setup
from desk.service import Desk, DeskError
from desk.store import StoreError
from erp.errors import ERPError, NotDispatched, WriteUncertain
from erp.service import ERPService
from tests.desk_helpers import ROOT, make_hub, make_desk, triage
from tests.test_inbox_workflow import inbox, normalize, ELEMENTS, extraction
from tests.test_review_fixes import OWNER, _claimed, _expect, _triage_with


def invoice_pair(tmp_path):
    d = make_desk(make_hub(tmp_path))
    a = _claimed(d, triage(d, "OID-009"))
    from tests.test_review_fixes import _fixture
    els, ex = _fixture("OID-009")
    old = ex["reference"]["value"]
    ex["reference"]["value"] += "-SECOND"
    for e in els:
        e["text"] = e["text"].replace(old, old + "-SECOND")
    b = _claimed(d, _triage_with(d, "OID-009", elements=els, extraction=ex, intake_id="other-invoice"))
    return d, a, b


def draft_args(d, case):
    expect, prop = _expect(d, case)
    return {"requester": OWNER, "expect": expect, "arguments": {"kind": prop["kind"], "payload": prop["payload"],
            "external_ref": d.external_ref(case["id"], prop["revision"])}, "work_item_id": "work-" + case["id"]}


@pytest.mark.parametrize("failure", [WriteUncertain, KeyboardInterrupt])
def test_capacity_fence_survives_uncertain_send_and_process_loss_without_sync(tmp_path, failure):
    d, a, b = invoice_pair(tmp_path)
    actual = d.erp.writer()
    def fail(*args):
        raise failure()
    fake = SimpleNamespace(marker=actual.marker, create_draft=fail)
    aa, bb = draft_args(d, a), draft_args(d, b)
    with pytest.raises(failure):
        d.dispatch_draft(a["id"], writer=fake, **aa)
    assert d.store.get_case(a["id"])["state"] == "draft_ready"  # no lazy sync needed
    restarted = Desk(d.hub_dir, erp=d.erp, grants=lambda a,p: True)
    with pytest.raises(DeskError) as blocked:
        restarted.dispatch_draft(b["id"], **bb)
    assert blocked.value.code == "receipt_capacity_uncertain"
    # Absence and case cancellation cannot prove the delayed insert impossible.
    assert actual.recover(**aa["arguments"])["status"] == "absent"
    d.cancel(OWNER, b["id"], expected_revision=b["revision"], reason="do not proceed")
    assert restarted.store.meta(d._invoice_fence_key())
    # Delayed original ERP insert eventually appears. The original recovery
    # path records exact named evidence before releasing its reservation.
    delayed = actual.create_draft(**aa["arguments"])
    found = actual.recover(**aa["arguments"])
    assert found["status"] == "applied"
    restarted.record_draft(a["id"], work_item_id=aa["work_item_id"], operation_id=None,
                           view=found["view"], actor=OWNER)
    assert not restarted.store.meta(d._invoice_fence_key())
    assert delayed["name"] == found["view"]["name"]


def test_capacity_claim_releases_only_a_new_definitively_unsent_attempt(tmp_path):
    d, a, b = invoice_pair(tmp_path)
    actual = d.erp.writer()
    def unsent(*args):
        raise NotDispatched("never_sent", "No connection opened")
    def unknown(*args):
        raise WriteUncertain()
    aa = draft_args(d, a)
    with pytest.raises(NotDispatched):
        d.dispatch_draft(a["id"], writer=SimpleNamespace(marker=actual.marker, create_draft=unsent), **aa)
    assert d.store.meta(d._invoice_fence_key()) is None
    with pytest.raises(WriteUncertain):
        d.dispatch_draft(a["id"], writer=SimpleNamespace(marker=actual.marker, create_draft=unknown), **aa)
    with pytest.raises(NotDispatched):
        d.dispatch_draft(a["id"], writer=SimpleNamespace(marker=actual.marker, create_draft=unsent), **aa)
    assert d.store.meta(d._invoice_fence_key())
    d.cancel(OWNER, a["id"], expected_revision=a["revision"], reason="stop")
    assert d.store.meta(d._invoice_fence_key())  # cancellation is not readback


ACTIONS = {
    "claim": {"expected_revision": 2}, "release": {"expected_revision": 2},
    "reassign": {"to": "other", "expected_revision": 2, "reason": "test"},
    "correct": {"expected_revision": 2, "fields": []}, "cancel": {"expected_revision": 2, "reason": "test"},
    "rerun": {"reason": "test"}, "request_draft": {"expected_revision": 2, "digest": "x"},
    "request_submit": {"expected_revision": 2, "digest": "x"}, "reconcile": {},
}


@pytest.mark.parametrize("action", ACTIONS)
def test_every_case_action_rechecks_mailbox_scope(tmp_path, action):
    d = inbox(tmp_path)
    case = normalize(d)
    d._grants = lambda a,p: p in ("desk_view", "desk_operate", "desk_manage", "desk_submit")
    with pytest.raises(DeskError) as denied:
        getattr(d, action)("outsider", case["case_id"], **ACTIONS[action])
    assert denied.value.code == "permission_denied"


@pytest.mark.parametrize("action", ["claim", "release", "reassign", "correct", "request_draft", "request_submit", "reconcile"])
def test_erp_only_case_actions_explain_disabled_addon(tmp_path, action):
    d = inbox(tmp_path)
    case = normalize(d)
    with pytest.raises(DeskError) as refused:
        getattr(d, action)("operator", case["case_id"], **ACTIONS[action])
    assert refused.value.code == "erp_addon_off"


def test_cancel_dismisses_exception_with_immutable_superseded_record(tmp_path):
    d = inbox(tmp_path)
    first = normalize(d)
    duplicate = normalize(d, "forwarded")
    row = d.store.get_case(duplicate["case_id"])
    assert d.report("operator", "exceptions")["rows"]
    d.cancel("operator", row["id"], expected_revision=row["revision"], reason="Reviewed duplicate of first")
    assert d.store.record(row["id"])["status"] == "superseded"
    assert len([r for r in d.store.records(d.hub_id) if r["record_id"] == row["id"]]) == 2
    assert row["id"] not in [r["record_id"] for r in d.report("operator", "exceptions")["rows"]]
    assert d.show("operator", row["id"])["actions"] == []
    with pytest.raises(DeskError):
        d.review_record("operator", row["id"], expected_revision=2, accept=True)
    d.review_record("operator", first["case_id"], expected_revision=1, accept=True)
    assert d.report("operator", "invoices")["totals_by_currency"]["USD"]["count"] == 1


def test_concurrent_autoaccept_copies_share_one_business_identity(tmp_path):
    d = inbox(tmp_path)
    rule = d.record_rules().model_copy(update={"auto_accept": True})
    (d.hub_dir / records.RULE_FILE).write_text(records.rule_text(rule))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda event: normalize(d, event), ["copy-a", "copy-b"]))
    assert sorted(r["record"]["status"] for r in results) == ["accepted", "exception"]
    assert d.report("operator", "invoices")["totals_by_currency"]["USD"]["count"] == 1


def test_review_rechecks_duplicate_when_fields_are_edited_into_collision(tmp_path):
    d = inbox(tmp_path)
    a = normalize(d)
    b = normalize(d, "copy")
    d.review_record("operator", a["case_id"], expected_revision=1, accept=True)
    d.review_record("operator", b["case_id"], expected_revision=1, changes={"reference": "DIFFERENT"}, accept=True)
    with pytest.raises(DeskError) as conflict:
        d.review_record("operator", b["case_id"], expected_revision=2, changes={"reference": "INV-A"}, accept=True)
    assert conflict.value.code == "record_has_exceptions"
    assert d.store.record(b["case_id"])["reference"] == "DIFFERENT"


def test_conversions_follow_corrected_lines_and_defend_legacy_stale_indices(tmp_path):
    d = inbox(tmp_path)
    source = d.hub_dir / "knowledge/source.md"
    source.write_text("Reviewed Widget pcs to unit factor 12")
    rules = d.record_rules().model_copy(update={"conversions": [records.Conversion(item="Widget", source_unit="pcs", target_unit="Nos", factor="12", source_ref="knowledge/source.md")]})
    (d.hub_dir / records.RULE_FILE).write_text(records.rule_text(rules))
    case = normalize(d)
    assert d.store.record(case["case_id"])["approved_conversions"][0]["item"] == "Widget"
    fixed = d.review_record("operator", case["case_id"], expected_revision=1,
                            changes={"lines": [{"item": "Other", "unit": "kg", "quantity": "1", "unit_price": "20", "amount": "20"}]}, accept=True)
    assert fixed["approved_conversions"] == []
    fixed["document_kind"] = "order"
    fixed["approved_conversions"] = [{"line": 5, "factor": "12", "from": "pcs", "to": "Nos"},
                                      {"line": 0, "item": "Widget", "factor": "12", "from": "kg", "to": "Nos"}]
    out = reports.build([fixed], "orders")
    assert out["converted_quantities"] == [] and len(out["conversion_exclusions"]) == 2


def test_saved_acknowledgements_and_rule_correction_apply_to_existing_case(tmp_path):
    d = inbox(tmp_path)
    case = normalize(d)
    row = d.store.record(case["case_id"])
    row["source_problems"] = ["multiple_documents_need_review", "untrusted_instructions"]
    row = records.validate(row, d.record_rules())
    d.store.save_record(case["case_id"], row, actor="test")
    reviewed = d.review_record("operator", case["case_id"], expected_revision=2,
                               changes={"source_acknowledgements": row["source_problems"]})
    assert reviewed["case_changes"]["source_acknowledgements"] == sorted(row["source_problems"])
    (d.hub_dir / "knowledge/source.md").write_text("Widget maps to Company Widget")
    rules = d.record_rules().model_copy(update={"aliases": [records.Alias(source="Widget", target="Company Widget", source_ref="knowledge/source.md")]})
    (d.hub_dir / records.RULE_FILE).write_text(records.rule_text(rules))
    assert "rerun" in d.show("operator", case["case_id"])["actions"]
    d.rerun("operator", case["case_id"], reason="Owner activated reviewed mapping")
    row = d.store.record(case["case_id"])
    assert row["lines"][0]["item"] == "Company Widget" and not row["exceptions"]
    assert row["source_acknowledgements"] == sorted(reviewed["source_acknowledgements"])
    d.review_record("operator", case["case_id"], expected_revision=row["revision"], accept=True)


def test_legacy_erp_fallback_and_migration_guard_preserve_identity(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    legacy = ERPService.for_hub(hub)
    deployment = legacy.deployment_id
    assert Desk(hub, grants=lambda a,p: True).erp_enabled()
    monkeypatch.setenv("HUBZOID_DATA_DIR", str(tmp_path / "newdata"))
    from hubzoid.datadir import state
    target = state(hub) / "data/erp/deployment-id"
    with pytest.raises(ERPError) as guarded:
        ERPService.for_hub(hub)
    assert guarded.value.code == "data_root_migration_required" and not target.exists()
    assert (legacy.state_dir / "deployment-id").read_text() == deployment


def test_dispatch_rechecks_current_rules_after_queue_time(tmp_path):
    d = make_desk(make_hub(tmp_path))
    case = _claimed(d, triage(d, "OID-001"))
    args = draft_args(d, case)
    path = d.hub_dir / "knowledge/order-policy.md"
    path.write_text(path.read_text() + "\nOwner changed policy context.\n")
    with pytest.raises(DeskError) as changed:
        d.recheck(case["id"], action="draft", requester=OWNER, expect=args["expect"], payload=args["arguments"]["payload"])
    assert changed.value.code == "rules_changed"


@pytest.mark.parametrize("runtime", ["claude-local", "codex-local"])
def test_setup_real_agents_and_gmail_pass_sdk_definition_validators(tmp_path, runtime):
    import shutil
    from hubzoid.channels.config import load_file
    from hubzoid.loaders.agents import load_main
    from hubzoid.stations.definitions import load
    hub = tmp_path / runtime
    hub.mkdir()
    shutil.copy(ROOT / "AGENTS.md", hub / "AGENTS.md")
    for directory in ("agents", "desk", "erp", "workflows"):
        shutil.copytree(ROOT / directory, hub / directory, ignore=shutil.ignore_patterns("__pycache__"))
    plan = setup.preview(hub, company="Alpha", runtime=runtime, mailbox="ops@example.org", client_id="id",
                         client_secret_ref="env:GMAIL_CLIENT_SECRET", label_ids=["Label_business"])
    setup.apply(hub, plan, plan["digest"])
    valid = load_file(hub, hub / "channels/orders-mail.yaml")
    assert valid.valid, valid.errors
    main = load_main(hub)
    assert main.spec.model == runtime
    station = load(hub, "desk-extractor")
    assert station.engine == runtime and station.model_setting().startswith(runtime + "/")
    assert station.schema and station.tools == ()


def test_setup_requires_explicit_single_label_or_whole_inbox_consent(tmp_path):
    d = inbox(tmp_path)
    args = {"company": "Alpha", "runtime": "codex-local", "mailbox": "ops@example.org", "client_id": "id", "client_secret_ref": "env:GMAIL_CLIENT_SECRET"}
    for labels in (None, [], ["INBOX"], ["Label_A", "Label_B"]):
        with pytest.raises(StoreError):
            setup.preview(d.hub_dir, **args, label_ids=labels)
    one = setup.preview(d.hub_dir, **args, label_ids=["Label_A"])
    whole = setup.preview(d.hub_dir, **args, whole_inbox=True)
    assert one["digest"] != whole["digest"]
    assert whole["ongoing_scope"]["selection"] == "whole_inbox_explicit"
    setup.apply(d.hub_dir, whole, whole["digest"])
    with pytest.raises(StoreError):
        setup.preview(d.hub_dir, **args)  # rerun cannot silently retain INBOX


def test_readonly_rule_change_returns_builder_diff_without_pending_claim(tmp_path, monkeypatch):
    d = inbox(tmp_path)
    before = (d.hub_dir / records.RULE_FILE).read_bytes()
    from hubzoid import datadir
    monkeypatch.setattr(datadir, "hub_read_only", lambda hub: True)
    # service imports _writable's helper from this same module dynamically.
    changed = d.record_rules().model_dump()
    changed["auto_accept"] = True
    proposed = d.propose_record_rules("builder", rules=changed, reason="Reviewed behavior")
    assert proposed["status"] == "builder_change_required" and "auto_accept: true" in proposed["diff"]
    assert "id" not in proposed and (d.hub_dir / records.RULE_FILE).read_bytes() == before


def test_queue_record_is_brief_and_show_retains_original_evidence(tmp_path):
    d = inbox(tmp_path)
    case = normalize(d)
    queued = d.list("operator")["cases"][0]
    assert set(queued["record"]) == {"revision", "status", "exceptions", "rule_revision"}
    assert "extraction" not in str(queued) and "raw_text" not in str(queued)
    shown = d.show("operator", case["case_id"])
    assert shown["record"]["evidence"] == ELEMENTS and shown["record"]["extraction"]


@pytest.mark.parametrize("changed", [False, True])
def test_uncertain_submit_recovery_compares_to_exact_prepared_approval(tmp_path, monkeypatch, changed):
    from desk import flow
    d = make_desk(make_hub(tmp_path))
    case = _claimed(d, triage(d, "OID-001"))
    _, prop = _expect(d, case)
    original = d.erp.writer().create_draft(prop["kind"], prop["payload"], "recover-submit")
    d.record_draft(case["id"], work_item_id="draft", operation_id=None, view=original, actor=OWNER)
    # A user may intentionally review an ERP edit before requesting submit.
    # Compare to that new approved content, not the old case content digest.
    approved = dict(original, content_digest="approved-edit", approval_digest="approved-version")
    submitted = dict(approved, docstatus=1, content_digest="changed-after-submit" if changed else "approved-edit",
                     totals={**approved["totals"], "grand_total": "2417.40"})
    reads = iter([approved, submitted])
    fake_writer = SimpleNamespace(get=lambda *a: next(reads))
    monkeypatch.setattr(d.erp, "writer", lambda: fake_writer)
    monkeypatch.setattr(flow, "Desk", lambda _: d)
    monkeypatch.setattr(flow, "_hub_dir", lambda _: d.hub_dir)
    item = SimpleNamespace(id="submit", requester=OWNER, route="submit",
                           input={"case_id": case["id"], "erp_name": original["name"], "digest": approved["approval_digest"]},
                           checkpoint=lambda name, fn: fn())
    def recover(name, **kwargs):
        assert kwargs["arguments"]["approved_content_digest"] == "approved-edit"
        result = kwargs["reconcile"](kwargs["arguments"])
        assert result.status == "applied"
        return result.result
    out = flow.case(SimpleNamespace(work_item=item, operation=SimpleNamespace(run=recover)))
    assert out["needs_review"] is changed
    assert out["state"] == ("needs_review" if changed else "done")
    assert out["total"] == "2417.40"


def test_erp_off_existing_operations_fail_storage_doctor(tmp_path):
    from desk.doctor import check_storage
    import yaml
    d = make_desk(make_hub(tmp_path))
    case = _claimed(d, triage(d, "OID-001"))
    d.store.link(case["id"], work_item_id="pending", action="draft", state="requested")
    (d.hub_dir / "hub.settings.yaml").write_text(yaml.safe_dump({"version": 1, "values": {"DESK_ERP_ENABLED": False}}))
    assert check_storage(d.hub_dir)["status"] == "fail"


def test_json_decimal_corrections_work_through_tool_and_cli(tmp_path, monkeypatch):
    import asyncio
    import json
    from typer.testing import CliRunner
    from desk import cli
    from tests.test_report_tool import load_tools
    d = inbox(tmp_path)
    case = normalize(d)
    tools = load_tools(monkeypatch, d)
    from agents.tool_context import ToolContext
    def invoke(tool, arguments):
        args_json = json.dumps(arguments)
        context = ToolContext(context=None, tool_name=tool.name, tool_call_id="test", tool_arguments=args_json)
        return json.loads(asyncio.run(tool.on_invoke_tool(context, args_json)))
    result = invoke(tools.desk_review_record, {
        "case": case["case_id"], "expected_revision": 1, "changes_json": '{"stated_tax":2.00}', "accept": True})
    assert result["status"] == "accepted" and result["stated_tax"] == "2.00"
    monkeypatch.setattr(cli, "_desk", lambda _: d)
    monkeypatch.setattr(cli, "_person", lambda _: "operator")
    output = CliRunner().invoke(cli.app, ["review-record", str(d.hub_dir), case["case_id"],
                                         "--expected-revision", "2", "--changes", '{"stated_tax":2.00}', "--accept", "--json"])
    assert output.exit_code == 0, output.output
    assert json.loads(output.output)["status"] == "accepted"
    invalid = invoke(tools.desk_propose_record_rules, {"rules_json": "{bad", "reason": "Invalid input"})
    assert invalid["error"]["code"] == "invalid_json"
