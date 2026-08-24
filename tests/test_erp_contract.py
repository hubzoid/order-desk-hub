"""One contract, every offline backend: sample and fake REST (the real HTTP
adapter against the fake Frappe site). The live suite runs the same checks
against a disposable ERPNext site (tests/live/test_erpnext_live.py)."""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from erp import digest as dg
from erp.errors import ERPError, WriteUncertain
from erp.models import ERPContext
from erp.write import clean_payload

CTX = ERPContext(hub_id="order-invoice-desk", principal="operator@example.test")
SO = {
    "company": "Synthetic Meridian Distribution", "customer": "SYN-CUST-001", "po_no": "HF-9001",
    "transaction_date": "2000-01-14", "delivery_date": "2000-01-21", "currency": "USD",
    "selling_price_list": "SYN-RETAIL", "set_warehouse": "SYN-MAIN - SMD",
    "items": [{"item_code": "SYN-ITEM-001", "qty": "24", "uom": "Nos", "rate": "9.80"},
              {"item_code": "SYN-ITEM-004", "qty": "3", "uom": "Box", "rate": "8.50"}],
}
PI = {
    "company": "Synthetic Meridian Distribution", "supplier": "SYN-SUPP-001", "bill_no": "SNP-INV-9999",
    "bill_date": "2000-01-12", "posting_date": "2000-01-12", "due_date": "2000-02-11", "currency": "USD",
    "buying_price_list": "SYN-BUYING",
    "items": [{"item_code": "SYN-ITEM-005", "qty": "60", "uom": "Nos", "rate": "3.70",
               "purchase_order": "PUR-ORD-2000-00001", "po_detail": "PUR-ORD-2000-00001-A",
               "purchase_receipt": "MAT-PRE-2000-00001", "pr_detail": "MAT-PRE-2000-00001-1"}],
}


def test_decimal_round_trip(erp_backend):
    svc, _ = erp_backend
    rec = svc.reader().get_record("prices", "SYN-IP-0004", CTX)
    assert rec["fields"]["price_list_rate"] == "0.85"
    assert isinstance(rec["fields"]["price_list_rate"], str)


def test_allowlisted_fields_only(erp_backend):
    svc, _ = erp_backend
    rec = svc.reader().get_record("customers", "SYN-CUST-001", CTX)
    assert set(rec["fields"]) == {"customer_name", "disabled", "default_currency", "default_price_list"}
    assert rec["synthetic"] is True and rec["backend"] == svc.config().backend
    item = svc.reader().get_record("items", "SYN-ITEM-004", CTX)
    assert [r["uom"] for r in item["rows"]] == ["Nos", "Box"] and item["rows"][1]["conversion_factor"] == "10"


def test_company_scope_without_master_company_field(erp_backend):
    svc, _ = erp_backend
    reader = svc.reader()
    orders = reader.all_records("sales_orders", {}, CTX)
    assert {r["company"] for r in orders} == {"Synthetic Meridian Distribution"}
    assert len(reader.all_records("customers", {}, CTX)) == 4     # masters have no company field
    stock = reader.all_records("availability", {"item_code": "SYN-ITEM-010"}, CTX)
    assert [r["fields"]["warehouse"] for r in stock] == ["SYN-MAIN - SMD"]   # overflow warehouse never shown


def test_party_scope_hides_foreign_records(erp_backend):
    svc, _ = erp_backend
    reader = svc.reader()
    scoped = ERPContext(hub_id="customer-desk", principal="desk", party_scope=("customer", "SYN-CUST-002"))
    mine = reader.all_records("sales_orders", {}, scoped)
    assert mine and {r["fields"]["customer"] for r in mine} == {"SYN-CUST-002"}
    with pytest.raises(ERPError) as err:
        reader.get_record("sales_invoices", "ACC-SINV-2000-00001", scoped)    # another customer's invoice
    assert err.value.code == "not_found"
    with pytest.raises(ERPError):
        reader.get_record("purchase_orders", "PUR-ORD-2000-00001", scoped)   # suppliers' documents
    prices = reader.all_records("prices", {"item_code": "SYN-ITEM-009"}, scoped)
    assert {p["fields"]["customer"] for p in prices} <= {None, "SYN-CUST-002"}
    other = ERPContext(hub_id="customer-desk", principal="desk", party_scope=("customer", "SYN-CUST-001"))
    assert "SYN-CUST-002" not in {p["fields"]["customer"] for p in reader.all_records("prices", {}, other)}


def test_sales_order_delivery_fields_for_customer_desk(erp_backend):
    svc, _ = erp_backend
    so = svc.reader().get_record("sales_orders", "SAL-ORD-2000-00002", CTX)
    f = so["fields"]
    assert f["delivery_status"] == "Partly Delivered" and f["per_delivered"] and f["billing_status"]
    assert so["rows"][0]["delivered_qty"] == "12" and so["rows"][0]["delivery_date"] == "2000-01-12"
    inv = svc.reader().get_record("sales_invoices", "ACC-SINV-2000-00001", CTX)
    assert inv["fields"]["po_no"] == "HF-7002" and inv["fields"]["is_return"] == 0


def test_print_pdf_requires_submitted_invoice_and_permission(erp_backend):
    svc, _ = erp_backend
    reader = svc.reader()
    pdf = reader.get_print_pdf("sales_invoices", "ACC-SINV-2000-00002", CTX)
    assert pdf["bytes"].startswith(b"%PDF-") and pdf["size"] < 5 * 1024 * 1024
    with pytest.raises(ERPError) as err:
        reader.get_print_pdf("sales_orders", "SAL-ORD-2000-00001", CTX)
    assert err.value.code == "print_not_allowed"
    with pytest.raises(ERPError):
        reader.get_print_pdf("sales_invoices", "ACC-SINV-2000-00002", CTX, print_format="Evil Format")
    scoped = ERPContext(hub_id="customer-desk", principal="desk", party_scope=("customer", "SYN-CUST-001"))
    with pytest.raises(ERPError) as err:
        reader.get_print_pdf("sales_invoices", "ACC-SINV-2000-00002", scoped)
    assert err.value.code == "not_found"


def test_cursor_bound_to_principal(erp_backend):
    svc, _ = erp_backend
    reader = svc.reader()
    page = reader.list_records("items", {}, CTX, limit=5)
    assert len(page.records) == 5 and page.next_cursor
    other = ERPContext(hub_id="order-invoice-desk", principal="someone-else@example.test")
    with pytest.raises(ERPError) as err:
        reader.list_records("items", {}, other, cursor=page.next_cursor, limit=5)
    assert err.value.code == "invalid_cursor"
    nxt = reader.list_records("items", {}, CTX, cursor=page.next_cursor, limit=5)
    assert {r["id"] for r in nxt.records}.isdisjoint({r["id"] for r in page.records})


def test_unknown_filter_rejected(erp_backend):
    svc, _ = erp_backend
    with pytest.raises(ERPError) as err:
        svc.reader().list_records("customers", {"bank_account": "x"}, CTX)
    assert err.value.code == "invalid_filter"


def test_draft_payload_rejects_submit_cancel_and_name():
    with pytest.raises(ERPError):
        clean_payload("sales_order", dict(SO, name="SAL-ORD-X"))
    model, stripped = clean_payload("sales_order", dict(SO, docstatus=1, status="Submitted"))
    assert stripped == ["docstatus", "status"]
    with pytest.raises(ERPError):
        clean_payload("sales_order", dict(SO, payment_terms_template="x"))       # unknown field
    with pytest.raises(ERPError):
        clean_payload("purchase_invoice", dict(PI, items=[dict(PI["items"][0], qty=1.5)]))   # a float
    with pytest.raises(ERPError):
        clean_payload("journal_entry", SO)


def test_draft_is_saved_docstatus_zero_and_reread(erp_backend):
    svc, _ = erp_backend
    w = svc.writer()
    out = w.create_draft("sales_order", SO, "op-so-1")
    assert out["docstatus"] == 0 and out["name"].startswith("SAL-ORD-2000-")
    assert out["needs_review"] is False and out["differences"] == []
    assert out["totals"]["grand_total"] == "260.7"           # 24 x 9.80 + 3 Box x 8.50
    assert out["lines"][1]["conversion_factor"] == "10"
    again = w.get("sales_order", out["name"])
    assert again["approval_digest"] == out["approval_digest"]


def test_same_external_ref_creates_one_document(erp_backend):
    svc, _ = erp_backend
    w = svc.writer()
    first = w.create_draft("purchase_invoice", PI, "op-pi-1")
    second = w.create_draft("purchase_invoice", PI, "op-pi-1")
    assert second["name"] == first["name"] and second["deduplicated"] is True
    assert len(w.find("purchase_invoice", "op-pi-1")) == 1
    with pytest.raises(ERPError) as err:     # same operation, different content
        w.create_draft("purchase_invoice", dict(PI, items=[dict(PI["items"][0], qty="59")]), "op-pi-1")
    assert err.value.code == "operation_conflict"


def test_duplicate_business_key_normalization(erp_backend):
    svc, _ = erp_backend
    w = svc.writer()
    with pytest.raises(ERPError) as err:     # SNP-INV-0901 is already booked (submitted)
        w.create_draft("purchase_invoice", dict(PI, bill_no="  snp-inv-0901 "), "op-dup")
    assert err.value.code == "duplicate_found" and err.value.fields["name"] == "ACC-PINV-2000-00001"
    # a cancelled bill's number is not a live duplicate
    w.create_draft("purchase_invoice", dict(PI, supplier="SYN-SUPP-003", bill_no="GCS-3001",
                                            items=[dict(PI["items"][0], purchase_order=None, po_detail=None,
                                                        purchase_receipt=None, pr_detail=None)]), "op-cancelled-ref")


def test_config_scope_enforced_on_writes(erp_backend):
    svc, _ = erp_backend
    w = svc.writer()
    for bad, code in ((dict(SO, company="Synthetic Other Holdings"), "company_denied"),
                      (dict(SO, currency="EUR"), "erp_validation_failed"),
                      (dict(SO, set_warehouse="SYN-OVERFLOW - SMD"), "erp_validation_failed")):
        with pytest.raises(ERPError) as err:
            w.create_draft("sales_order", bad, f"op-{code}")
        assert err.value.code == code


def test_writes_disabled_refuses(tmp_path):
    from tests.conftest import make_service

    svc = make_service(tmp_path, writes=False)
    with pytest.raises(ERPError) as err:
        svc.writer().create_draft("sales_order", SO, "op-off")
    assert err.value.code == "erp_writes_disabled"


def test_submit_digest_and_modified_rechecked(erp_backend):
    svc, server = erp_backend
    w = svc.writer()
    draft = w.create_draft("sales_order", SO, "op-submit")
    with pytest.raises(ERPError) as err:
        w.submit_approved("sales_order", draft["name"], "sha256:" + "0" * 64)
    assert err.value.code == "approval_stale"
    # A person edits the draft in the ERP after approval: the approval is stale.
    _edit(svc, server, draft["name"], {"po_no": "HF-9001-B"})
    with pytest.raises(ERPError) as err:
        w.submit_approved("sales_order", draft["name"], draft["approval_digest"])
    assert err.value.code == "approval_stale"
    current = w.get("sales_order", draft["name"])
    done = w.submit_approved("sales_order", draft["name"], current["approval_digest"])
    assert done["docstatus"] == 1 and done["submitted_content_matches"] is True
    with pytest.raises(ERPError) as err:
        w.submit_approved("sales_order", draft["name"], current["approval_digest"])
    assert err.value.code == "already_submitted"
    assert err.value.fields["content_digest"] == current["content_digest"]


def _edit(svc, server, name, changes):
    if server is None:
        svc.transport().edit_draft("Sales Order", name, changes)
    else:
        httpx.post(f"{server.origin}/__fake/edit", json={"doctype": "Sales Order", "name": name, "changes": changes})


def test_content_digest_is_backend_independent(tmp_path, fake_site):
    from tests.conftest import make_service

    a = make_service(tmp_path / "a").writer().create_draft("sales_order", SO, "op-x")
    b = make_service(tmp_path / "b", "fake_rest", origin=fake_site.origin).writer().create_draft("sales_order", SO, "op-x")
    assert a["content_digest"] == b["content_digest"]


# -- wire faults: the real HTTP adapter against the fake Frappe site ------------------------------

def _faults(server, *faults):
    httpx.post(f"{server.origin}/__fake/faults", json={"faults": list(faults)})


def _fake(tmp_path, server):
    from tests.conftest import make_service

    return make_service(tmp_path, "fake_rest", origin=server.origin)


def test_commit_then_timeout_reconciles_one_record(tmp_path, fake_site):
    w = _fake(tmp_path, fake_site).writer()
    _faults(fake_site, {"op": "insert", "fault": "commit_then_disconnect"})
    with pytest.raises(WriteUncertain):
        w.create_draft("sales_order", SO, "op-lost")
    found = w.find("sales_order", "op-lost")
    assert len(found) == 1 and found[0]["docstatus"] == 0
    again = w.create_draft("sales_order", SO, "op-lost")      # the identical retry finds it
    assert again["deduplicated"] is True and again["name"] == found[0]["name"]


def test_disconnect_before_commit_leaves_nothing(tmp_path, fake_site):
    w = _fake(tmp_path, fake_site).writer()
    _faults(fake_site, {"op": "insert", "fault": "disconnect_before_commit"})
    with pytest.raises(WriteUncertain):
        w.create_draft("sales_order", SO, "op-nothing")
    assert w.find("sales_order", "op-nothing") == []          # empty, but the operation stays uncertain upstream


def test_delayed_commit_is_found_later(tmp_path, fake_site):
    import time

    w = _fake(tmp_path, fake_site).writer()
    _faults(fake_site, {"op": "insert", "fault": "delayed_commit", "seconds": 0.5})
    with pytest.raises(WriteUncertain):
        w.create_draft("sales_order", SO, "op-delayed")
    time.sleep(1.0)
    assert len(w.find("sales_order", "op-delayed")) == 1


def test_auth_and_permission_errors_are_not_retried(tmp_path, fake_site):
    svc = _fake(tmp_path, fake_site)
    _faults(fake_site, {"op": "read", "fault": "status_401"})
    with pytest.raises(ERPError) as err:
        svc.reader().get_record("items", "SYN-ITEM-001", CTX)
    assert err.value.code == "erp_auth_failed"
    _faults(fake_site, {"op": "read", "fault": "status_403"})
    with pytest.raises(ERPError) as err:
        svc.reader().get_record("items", "SYN-ITEM-001", CTX)
    assert err.value.code == "erp_permission_denied"


def test_reads_retry_429_then_succeed(tmp_path, fake_site):
    svc = _fake(tmp_path, fake_site)
    _faults(fake_site, {"op": "read", "fault": "status_429"})
    rec = svc.reader(svc.transport()).get_record("items", "SYN-ITEM-001", CTX)
    assert rec["id"] == "SYN-ITEM-001"


def test_redirect_and_malformed_json_are_protocol_errors(tmp_path, fake_site):
    svc = _fake(tmp_path, fake_site)
    _faults(fake_site, {"op": "read", "fault": "redirect"})
    with pytest.raises(ERPError) as err:
        svc.reader().get_record("items", "SYN-ITEM-001", CTX)
    assert err.value.code == "protocol_error"
    _faults(fake_site, {"op": "insert", "fault": "malformed_json"})
    with pytest.raises(WriteUncertain):
        svc.writer().create_draft("sales_order", SO, "op-malformed")


def test_submit_server_error_is_uncertain(tmp_path, fake_site):
    w = _fake(tmp_path, fake_site).writer()
    draft = w.create_draft("sales_order", SO, "op-s500")
    _faults(fake_site, {"op": "submit", "fault": "status_500"})
    with pytest.raises(WriteUncertain):
        w.submit_approved("sales_order", draft["name"], draft["approval_digest"])


def test_live_config_refuses_unsafe_urls(tmp_path):
    from erp.config import ERPConfig

    base = {"version": 1, "backend": "frappe_rest", "dataset": None, "company": "X", "currency": "USD",
            "warehouses": ["W"], "selling_price_list": "S", "buying_price_list": "B",
            "credential_ref": "secret:hub/order-invoice-desk/erpnext-api"}
    for url in ("http://erp.example.com", "https://user:pw@erp.example.com", "https://erp.example.com/app",
                "https://erp.example.com?x=1", "https://127.0.0.1"):
        with pytest.raises(ValueError):
            ERPConfig.model_validate(dict(base, site_url=url))
    ERPConfig.model_validate(dict(base, site_url="https://erp.example.com"))
    with pytest.raises(ValueError):
        ERPConfig.model_validate(dict(base, site_url="https://erp.example.com", private_network_cidrs=["169.254.0.0/16"]))


def test_pinned_backend_refuses_metadata_and_private(tmp_path):
    import socket

    from erp.http import FrappeHTTP

    def resolver(ip):
        return lambda host, port, type=None: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    for ip in ("169.254.169.254", "10.0.0.5", "127.0.0.1"):
        client = FrappeHTTP("https://erp.example.com", credential="x", timeout=2, read_attempts=1,
                            loopback_only=False, resolver=resolver(ip), sleep=lambda s: None)
        with pytest.raises(ERPError) as err:
            client.get("/api/method/frappe.auth.get_logged_user")
        assert err.value.code == "erp_unavailable"
        client.close()


def test_sample_generation_reproducible():
    from erp.datasets import generate

    assert generate.main(["--check"]) == 0


def test_intake_fixture_matches_hub106_schema():
    from tests.conftest import DATASET

    keys = {"version", "hub_id", "channel_id", "provider", "provider_event_id", "provider_message_id", "thread_key",
            "sender", "received_at", "text", "attachments", "trust", "origin"}
    for name in ("order-invoice-desk.jsonl", "customer-desk.jsonl"):
        rows = [json.loads(l) for l in (DATASET / "messages" / name).read_text().splitlines()]
        assert len(rows) == (12 if name.startswith("order") else 20)
        for r in rows:
            assert set(r) == keys and r["version"] == 1 and r["received_at"].endswith("Z")
            assert set(r["sender"]) == {"external_id", "display_name"}
            assert r["trust"]["decision"] == "pending"
            for a in r["attachments"]:
                assert set(a) == {"attachment_id", "filename", "media_type", "bytes", "sha256", "state"}
            assert "synthetic" in r["text"].lower()


def test_dataset_counts_and_labels():
    from tests.conftest import DATASET

    manifest = json.loads((DATASET / "manifest.json").read_text())
    c = manifest["counts"]
    assert (c["customers"], c["suppliers"], c["items"], c["item_prices"], c["sales_orders"], c["purchase_orders"],
            c["purchase_receipts"], c["sales_invoices"], c["purchase_invoices"]) == (4, 3, 12, 24, 6, 4, 3, 4, 3)
    assert manifest["synthetic"] is True and manifest["currency"] == "USD" and manifest["timezone"] == "UTC"
    for path in (DATASET / "erp").glob("*.json"):
        for doc in json.loads(path.read_text()):
            label = doc.get("customer_name") or doc.get("supplier_name") or doc.get("item_name") or doc.get("company_name")
            if label:
                assert label.startswith("Synthetic"), label
    emails = (DATASET / "bindings.csv").read_text()
    assert all(line.split(",")[2].endswith(".example") or line.split(",")[2].startswith("+1202555")
               for line in emails.splitlines()[1:])
