from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from desk import records, setup
from desk.service import Desk, DeskError
from desk.store import StoreError
from tests.desk_helpers import ROOT


def inbox(tmp_path, *, grants=None):
    hub = tmp_path / "inbox"
    hub.mkdir()
    shutil.copytree(ROOT / "agents", hub / "agents")
    (hub / "AGENTS.md").write_text("Inbox")
    plan = setup.preview(hub, company="Alpha", runtime="codex-local")
    setup.apply(hub, plan, plan["digest"])
    return Desk(hub, grants=grants or (lambda a, p: True))


def extraction():
    return {"document_kind": "supplier_invoice", "party_name_seen": "Supplier", "bill_to_seen": None,
            "reference": {"value": "INV-A", "source_ref": "E1"}, "document_date": {"value": "2026-10-20", "source_ref": "E1"},
            "delivery_date": None, "due_date": None, "purchase_order_refs": [], "currency_seen": "USD",
            "lines": [{"source_ref": "E2", "raw_text": "Widget 2 pcs 10.00 20.00", "item_code_seen": "Widget",
                       "description_seen": "Widget", "quantity": "2", "unit_seen": "pcs", "unit_price": "10.00",
                       "amount": "20.00", "purchase_order_seen": None}],
            "stated_total": {"value": "22.00", "source_ref": "E3"}, "stated_tax": {"value": "2.00", "source_ref": "E3"},
            "instructions_seen": False, "notes": ""}


ELEMENTS = [{"id": "E1", "text": "Supplier INV-A 2026-10-20 USD", "loc": "line 1", "doc": 1, "doc_label": "email"},
            {"id": "E2", "text": "Widget 2 pcs 10.00 20.00", "loc": "line 2", "doc": 1, "doc_label": "email"},
            {"id": "E3", "text": "Tax exclusive 2.00 Total 22.00", "loc": "line 3", "doc": 1, "doc_label": "email"}]


def normalize(d, event="mail-1"):
    payload = {"intake_id": event, "channel": "orders-mail", "provider": "email", "sender": {"external_id": "supplier@example.org"}}
    case, _ = d.open_case(payload, work_item_id=event)
    d.store.add_document(case["id"], ordinal=0, label="email", parse_status="parsed", elements_json=ELEMENTS)
    return d.stage_match(case["id"], extraction(), elements=ELEMENTS)


def test_inbox_to_review_to_all_reports_without_any_erp_configuration(tmp_path):
    d = inbox(tmp_path)
    assert not d.erp_enabled() and not (d.hub_dir / "connectors/erpnext.yaml").exists()
    out = normalize(d)
    assert out["record"]["status"] == "unreviewed" and out["record"]["exceptions"] == []
    view = d.show("member", out["case_id"])
    assert view["backend"] == "records" and "review_record" in view["actions"]
    assert out["total"] == "22.00" and view["total"] == "22.00"
    assert d.list("member")["cases"][0]["total"] == "22.00"
    accepted = d.review_record("member", out["case_id"], expected_revision=1, accept=True)
    assert accepted["status"] == "accepted"
    assert d.report("member", "invoices")["totals_by_currency"]["USD"]["total"] == "22.00"
    assert len(d.report("member", "register")["rows"]) == 1
    assert d.report("member", "orders")["totals_by_currency"] == {}
    assert d.report("member", "exceptions")["rows"] == []
    assert not (d.hub_dir / ".hubzoid/data/erp").exists()
    # A saved correction survives restart without changing original extraction.
    new = d.review_record("member", out["case_id"], expected_revision=2, changes={"party": "Supplier Ltd"})
    assert Desk(d.hub_dir, grants=lambda a,p: True).store.record(out["case_id"])["party"] == "Supplier Ltd"
    assert new["extraction"]["party_name_seen"] == "Supplier"
    with pytest.raises(StoreError, match="changed"):
        d.review_record("member", out["case_id"], expected_revision=1, accept=True)


def test_same_source_is_idempotent_and_forwarded_copy_is_candidate_not_counted(tmp_path):
    d = inbox(tmp_path)
    a = normalize(d)
    assert normalize(d)["record"]["revision"] == 1
    b = normalize(d, "forwarded-mail")
    assert b["record"]["duplicate_of"] == a["case_id"]
    assert "duplicate_candidate" in b["record"]["exceptions"]
    assert not d.report("member", "invoices")["totals_by_currency"]


def test_mailbox_scope_is_enforced_on_queue_case_review_and_reports(tmp_path):
    d = inbox(tmp_path)
    out = normalize(d)
    d._grants = lambda actor, perm: perm in ("desk_view", "desk_operate")
    assert d.list("other")["cases"] == []
    assert d.report("other", "register")["rows"] == []
    with pytest.raises(DeskError) as exc:
        d.show("other", out["case_id"])
    assert exc.value.code == "permission_denied"
    with pytest.raises(DeskError):
        d.review_record("other", out["case_id"], expected_revision=1, accept=True)


def test_custom_data_root_and_readonly_source_setup(tmp_path, monkeypatch):
    d = inbox(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setenv("HUBZOID_DATA_DIR", str(data))
    moved = Desk(d.hub_dir, grants=lambda a,p: True)
    assert moved.store.path.is_relative_to(data)
    plan = setup.preview(d.hub_dir, company="Alpha", runtime="codex-local")
    with pytest.raises(Exception, match="builder checkout"):
        setup.apply(d.hub_dir, plan, plan["digest"])


def test_setup_preview_has_no_credentials_and_preserves_repeated_apply(tmp_path):
    d = inbox(tmp_path)
    plan = setup.preview(d.hub_dir, company="Alpha", runtime="codex-local", mailbox="ops@example.org", client_id="client-id",
                         client_secret_ref="env:GMAIL_CLIENT_SECRET")
    assert not (d.hub_dir / "channels/orders-mail.yaml").exists()
    assert "env:GMAIL_CLIENT_SECRET" in json.dumps(plan)
    setup.apply(d.hub_dir, plan, plan["digest"])
    setup.apply(d.hub_dir, plan, plan["digest"])
    channel = d.hub_dir / "channels/orders-mail.yaml"
    channel.write_text(channel.read_text() + "# custom edit\n")
    with pytest.raises(Exception, match="changed"):
        setup.apply(d.hub_dir, plan, plan["digest"])


def test_gmail_channels_have_distinct_oauth_slots_and_keep_custom_sources(tmp_path):
    import yaml
    d = inbox(tmp_path)
    settings = d.hub_dir / "hub.settings.yaml"
    data = yaml.safe_load(settings.read_text())
    data["values"]["DESK_SOURCES"] = ["whatsapp", "upload", "email"]
    settings.write_text(yaml.safe_dump(data))
    plans = [setup.preview(d.hub_dir, company="Alpha", runtime="codex-local", mailbox=f"{channel}@example.org",
                           client_id="client-id", client_secret_ref="env:GMAIL_CLIENT_SECRET", channel=channel,
                           label_ids=["Label_business"]) for channel in ("orders", "invoices")]
    refs = []
    for plan in plans:
        channel_edit = next(e for e in plan["edits"] if e["path"].startswith("channels/"))
        configured = yaml.safe_load(channel_edit["text"])
        refs.append(configured["provider_config"]["gmail"]["oauth_ref"])
        assert plan["ongoing_scope"]["label_ids"] == ["Label_business"]
        assert not plan["ongoing_scope"]["history_query_filters_recurring_poll"]
        assert not any(e["path"] == "hub.settings.yaml" for e in plan["edits"])
    assert refs == [f"secret:channel/{d.hub_id}/{channel}/gmail-oauth" for channel in ("orders", "invoices")]
    assert len(set(refs)) == 2
    whole = setup.preview(d.hub_dir, company="Alpha", runtime="codex-local", mailbox="whole@example.org",
                          client_id="id", client_secret_ref="env:GMAIL_CLIENT_SECRET")
    assert whole["ongoing_scope"]["label_ids"] == ["INBOX"]
    assert "all newly admitted INBOX mail" in whole["ongoing_scope"]["retention"]
