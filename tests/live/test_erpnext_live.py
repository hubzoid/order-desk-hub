"""The ERP contract against a live, disposable ERPNext test site.

    HUBZOID_ERP_LIVE_SITE=http://127.0.0.1:8080 \
    HUBZOID_ERP_LIVE_READ=env:ERP_READ_TOKEN HUBZOID_ERP_LIVE_DRAFT=env:ERP_DRAFT_TOKEN \
    HUBZOID_ERP_LIVE_SUBMIT=env:ERP_SUBMIT_TOKEN \
    pytest -m erp_live tests/live/test_erpnext_live.py

The same checks as the sample and fake REST suite, plus what only a real site
shows: Frappe's own unique index on the operation marker, its `modified`
check inside frappe.client.submit, the draft user's missing submit right, the
print endpoint and doctor's hook inspection. Skipped (and reported as
skipped, never as passed) without a configured test site. The site must be
the seeded `hubzoid-test.localhost` from testing/erpnext/, never production.
"""
from __future__ import annotations

import os

import pytest

from erp import config as config_lib
from erp.errors import ERPError
from erp.models import ERPContext
from erp.service import ERPService

pytestmark = pytest.mark.erp_live
SITE = os.environ.get("HUBZOID_ERP_LIVE_SITE")
if not SITE:
    pytest.skip("No live ERPNext test site configured (HUBZOID_ERP_LIVE_SITE). Live qualification not run.",
                allow_module_level=True)

CTX = ERPContext(hub_id="order-invoice-desk", principal="live-test")
SO = {"company": "Synthetic Meridian Distribution", "customer": "SYN-CUST-001", "po_no": "HF-LIVE-0001",
      "transaction_date": "2000-01-14", "delivery_date": "2000-01-21", "currency": "USD",
      "selling_price_list": "SYN-RETAIL", "set_warehouse": "SYN-MAIN - SMD",
      "items": [{"item_code": "SYN-ITEM-001", "qty": "24", "uom": "Nos", "rate": "9.80"}]}


def _service(tmp_path, ref_env: str, submit_env: str | None = None, writes: bool = True) -> ERPService:
    hub = tmp_path / "hub"
    (hub / "connectors").mkdir(parents=True, exist_ok=True)
    data = dict(config_lib.SAMPLE_DEFAULT, backend="frappe_rest", dataset=None, site_url=SITE,
                local_test_site=SITE.startswith("http://"), credential_ref=os.environ[ref_env],
                submit_credential_ref=os.environ.get(submit_env) if submit_env else None, writes_enabled=writes)
    (hub / "connectors" / "erpnext.yaml").write_text(config_lib.dump(data))
    return ERPService(hub, hub / ".hubzoid" / "data" / "erp", deployment_id="live-test")


@pytest.fixture
def reader_service(tmp_path):
    return _service(tmp_path, "HUBZOID_ERP_LIVE_READ", writes=False)


@pytest.fixture
def writer_service(tmp_path):
    return _service(tmp_path, "HUBZOID_ERP_LIVE_DRAFT", "HUBZOID_ERP_LIVE_SUBMIT")


def test_doctor_ready_and_versions_recorded(writer_service):
    out = writer_service.doctor()
    assert out["ready_read"], out["checks"]
    assert out["ready_write"], [c for c in out["checks"] if c["status"] == "fail"]
    versions = next(c for c in out["checks"] if c["id"] == "versions")
    print("LIVE VERSIONS", versions["details"]["versions"])


def test_reads_match_the_dataset(reader_service):
    r = reader_service.reader()
    assert r.get_record("customers", "SYN-CUST-001", CTX)["fields"]["customer_name"] == "Synthetic Harbor Foods"
    item = r.get_record("items", "SYN-ITEM-004", CTX)
    assert {row["uom"] for row in item["rows"]} == {"Nos", "Box"}
    so = r.get_record("sales_orders", "SAL-ORD-2000-00002", CTX)
    assert so["fields"]["delivery_status"] in ("Partly Delivered", "To Deliver") and so["rows"][0]["delivery_date"]
    scoped = ERPContext(hub_id="customer-desk", principal="live", party_scope=("customer", "SYN-CUST-002"))
    with pytest.raises(ERPError):
        r.get_record("sales_invoices", "ACC-SINV-2000-00001", scoped)
    pdf = r.get_print_pdf("sales_invoices", "ACC-SINV-2000-00002", CTX)
    assert pdf["bytes"].startswith(b"%PDF-")


def test_draft_is_inert_unique_and_submits_only_when_approved(writer_service):
    w = writer_service.writer()
    first = w.create_draft("sales_order", SO, "live-op-1")
    assert first["docstatus"] == 0 and not first["needs_review"]
    again = w.create_draft("sales_order", SO, "live-op-1")
    assert again["name"] == first["name"] and again["deduplicated"]
    with pytest.raises(ERPError) as err:
        w.submit_approved("sales_order", first["name"], "sha256:" + "0" * 64)
    assert err.value.code == "approval_stale"
    done = w.submit_approved("sales_order", first["name"], first["approval_digest"])
    assert done["docstatus"] == 1 and done["submitted_content_matches"]


def test_draft_user_cannot_submit(tmp_path):
    svc = _service(tmp_path, "HUBZOID_ERP_LIVE_DRAFT", writes=True)
    w = svc.writer()
    out = w.create_draft("sales_order", dict(SO, po_no="HF-LIVE-0002"), "live-op-2")
    transport = svc.transport()
    with pytest.raises(ERPError) as err:          # the draft credential holds no submit right
        transport.submit(transport.get_doc("Sales Order", out["name"]))
    assert err.value.code in ("erp_permission_denied", "erp_validation_failed")


def test_marker_is_unique_and_recovery_checks_the_draft(writer_service):
    w = writer_service.writer()
    w.require_unique_marker("sales_order")                 # the bootstrap made the field unique
    w.require_unique_marker("purchase_invoice")
    so = dict(SO, po_no="HF-LIVE-0003")
    assert w.recover("sales_order", so, "live-op-3")["status"] == "absent"
    made = w.create_draft("sales_order", so, "live-op-3")
    found = w.recover("sales_order", so, "live-op-3")
    assert found["status"] == "applied" and found["view"]["name"] == made["name"]


def test_site_drops_unknown_fields_as_the_fake_does(writer_service):
    t = writer_service.transport()
    doc = {"doctype": "Sales Order", "docstatus": 0, **{k: v for k, v in SO.items() if k != "items"},
           "po_no": "HF-LIVE-0004", "custom_hubzoid_operaton_id": "misspelt", "items": SO["items"]}
    saved = t.get_doc("Sales Order", t.insert("Sales Order", doc)["name"])
    assert "custom_hubzoid_operaton_id" not in saved and not saved.get("custom_hubzoid_operation_id")
