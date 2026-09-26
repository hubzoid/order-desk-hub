"""Regression tests for the Codex review of 20 Oct 2026 (gpt-6.1-sol, high).

Each test names the finding it covers. Fixtures replay the recorded elements
and scripted extractions, changed where a finding needs a different document.
Receipt and unit cases the distributor-v1 dataset does not contain (gram
receipts, rejected goods, returns, bills straight from an order, a line split
across two receipts) use a small in-memory ERP reader in the read contract's
own record shape.
"""
from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal

import pytest

from desk import extract as extract_lib
from desk import propose as propose_lib
from desk.service import DeskError
from desk.store import StoreError
from erp.errors import ERPError
from erp.models import ERPContext
from tests.desk_helpers import ELEMENTS, EXTRACTIONS, envelopes, make_desk, make_hub, payload, store_elements, triage
from tests.test_erp_contract import PI, SO, _edit

OWNER = "ann@example.test"


@pytest.fixture
def desk(tmp_path):
    return make_desk(make_hub(tmp_path))


def _fixture(event):
    rec = json.loads((ELEMENTS / f"{event}.json").read_text())
    return rec["elements"], json.loads((EXTRACTIONS / f"{event}.json").read_text())


def _triage_with(desk, event, *, extraction=None, elements=None, intake_id=None, preview=False):
    """triage() with a changed extraction or elements, or as another message."""
    env = dict(envelopes()[event])
    if intake_id:
        env["provider_event_id"] = intake_id
    els0, ex0 = _fixture(event)
    els = elements if elements is not None else els0
    case, _ = desk.open_case(payload(env), work_item_id=f"wi-{env['provider_event_id']}", preview=preview)
    store_elements(desk, case["id"], els)
    return desk.stage_match(case["id"], extraction if extraction is not None else ex0, elements=els)


def _claimed(desk, out, who=OWNER):
    case = desk.store.get_case(out["case_id"])
    desk.claim(who, case["id"], expected_revision=case["revision"])
    return desk.store.get_case(case["id"])


def _codes(out):
    return [q["code"] for q in out["questions"]]


def _correct(desk, case_id, who=OWNER, **fields):
    case = desk.store.get_case(case_id)
    return desk.correct(who, case_id, expected_revision=case["revision"],
                        fields=[{"path": k.replace("__", "."), "value": v} for k, v in fields.items()])


# -- findings 1, 2 and 8: the recheck right before an ERP write ----------------------------------

def _expect(desk, case):
    prop = desk.store.proposal(case["id"])
    return {"proposal_revision": prop["revision"], "digest": prop["digest"]}, prop


def test_write_mode_switched_off_stops_an_admitted_draft(desk):
    case = _claimed(desk, triage(desk, "OID-001"))
    expect, prop = _expect(desk, case)
    desk.recheck(case["id"], action="draft", requester=OWNER, expect=expect, payload=prop["payload"])
    (desk.hub_dir / "work-items.yaml").write_text("version: 1\nwrite_modes:\n  erp.submit: \"off\"\n")
    with pytest.raises(DeskError) as err:
        desk.recheck(case["id"], action="draft", requester=OWNER, expect=expect, payload=prop["payload"])
    assert err.value.code == "write_mode_off"


def test_revoked_grant_and_new_owner_stop_a_recovered_draft(tmp_path):
    revoked = set()
    desk = make_desk(make_hub(tmp_path), grants=lambda actor, perm: (actor, perm) not in revoked)
    case = _claimed(desk, triage(desk, "OID-001"))
    expect, prop = _expect(desk, case)
    revoked.add((OWNER, "desk_operate"))
    with pytest.raises(DeskError) as err:
        desk.recheck(case["id"], action="draft", requester=OWNER, expect=expect, payload=prop["payload"])
    assert err.value.code == "permission_denied"
    revoked.clear()
    case = desk.store.get_case(case["id"])
    desk.reassign("admin@example.test", case["id"], to="bob@example.test", expected_revision=case["revision"],
                  reason="holiday")
    with pytest.raises(DeskError) as err:
        desk.recheck(case["id"], action="draft", requester=OWNER, expect=expect, payload=prop["payload"])
    assert err.value.code == "not_owner"


def test_receipt_capacity_is_rechecked_before_the_write(desk):
    case = _claimed(desk, triage(desk, "OID-006"))           # 60 left on MAT-PRE-2000-00001-1
    expect, prop = _expect(desk, case)
    desk.recheck(case["id"], action="draft", requester=OWNER, expect=expect, payload=prop["payload"])
    # Another bill for the same receipt row is saved first.
    desk.erp.writer().create_draft("purchase_invoice", dict(PI, bill_no="SNP-INV-0999"), "another-desk-op")
    with pytest.raises(DeskError) as err:
        desk.recheck(case["id"], action="draft", requester=OWNER, expect=expect, payload=prop["payload"])
    assert err.value.code == "receipt_capacity_changed"
    assert "MAT-PRE-2000-00001-1" in err.value.message


# -- finding 3, and one element holding several items -----------------------------------------

def test_raw_text_must_come_from_its_element(desk):
    els, ex = _fixture("OID-011")
    ex = copy.deepcopy(ex)
    ex["lines"][0]["raw_text"] = "80 x SYN-ITEM-009"          # not what E3 says
    verified = extract_lib.verify(extract_lib.parse_output(ex), els)
    assert any("raw_text" in (p.get("fields") or []) for p in verified.problems)
    assert verified.extraction.lines[0].raw_text == "80 x Synthetic Hex Bolt M8"
    out = _triage_with(desk, "OID-011", extraction=ex)
    assert "ambiguous_item" in _codes(out) and desk.store.proposal(out["case_id"])["payload"] is None


def test_a_code_in_a_shared_element_counts_only_when_it_is_the_only_one(desk):
    els, ex = _fixture("OID-004")                              # one element, two items
    ex = copy.deepcopy(ex)
    ex["lines"][1]["item_code_seen"] = None
    out = _triage_with(desk, "OID-004", extraction=ex)
    q = next(q for q in out["questions"] if q["code"] == "ambiguous_item")
    assert {o["value"] for o in q["options"]} == {"SYN-ITEM-007", "SYN-ITEM-008"}


# -- finding 4: omitted and repeated lines --------------------------------------------------------

def test_an_omitted_line_is_a_question_and_can_be_added(desk):
    els, ex = _fixture("OID-011")
    ex = copy.deepcopy(ex)
    ex["lines"] = ex["lines"][1:]                              # the bolt line (E3) is left out
    out = _triage_with(desk, "OID-011", extraction=ex)
    q = next(q for q in out["questions"] if q["code"] == "line_not_extracted")
    assert q["field"] == "ack.uncovered.E3" and q["source"]["element"] == "E3"
    _claimed(desk, out)
    fixed = _correct(desk, out["case_id"], **{"lines[2]__source": "E3", "lines[2]__item_code": "SYN-ITEM-009",
                                              "lines[2]__qty": "80"})
    assert fixed["state"] == "draft_ready", fixed["questions"]
    prop = desk.store.proposal(out["case_id"])
    assert [l["item_code"] for l in prop["payload"]["items"]] == ["SYN-ITEM-006", "SYN-ITEM-009"]
    assert prop["evidence"]["lines"][1]["added"]["text"] == "Added by a person from E3"


def test_an_element_that_is_not_a_line_can_be_confirmed(desk):
    els, ex = _fixture("OID-011")
    ex = copy.deepcopy(ex)
    ex["lines"] = ex["lines"][1:]
    out = _triage_with(desk, "OID-011", extraction=ex)
    _claimed(desk, out)
    fixed = _correct(desk, out["case_id"], **{"ack__uncovered__E3": "not_a_line"})
    assert "line_not_extracted" not in _codes(fixed)
    with pytest.raises(DeskError):
        _correct(desk, out["case_id"], **{"ack__uncovered__E3": "yes"})


def test_an_omitted_table_row_is_a_question(desk):
    els, ex = _fixture("OID-003")
    ex = copy.deepcopy(ex)
    ex["lines"] = [l for l in ex["lines"] if l["source_ref"] != "E10"]   # SYN-ITEM-005, a middle row
    out = _triage_with(desk, "OID-003", extraction=ex)
    q = next(q for q in out["questions"] if q["code"] == "line_not_extracted")
    assert q["source"]["element"] == "E10"
    assert "SYN-ITEM-005" in q["message"] and "row among the order's lines" in q["message"]


def test_a_repeated_line_is_a_question_until_a_person_decides(desk):
    els, ex = _fixture("OID-001")
    ex = copy.deepcopy(ex)
    ex["lines"].append(copy.deepcopy(ex["lines"][1]))
    out = _triage_with(desk, "OID-001", extraction=ex)
    q = next(q for q in out["questions"] if q["code"] == "line_repeated")
    assert q["field"] == "lines[3].include"
    _claimed(desk, out)
    fixed = _correct(desk, out["case_id"], **{"lines[3]__include": "no"})
    assert fixed["state"] == "draft_ready"
    assert len(desk.store.proposal(out["case_id"])["payload"]["items"]) == 2


# -- finding 5: the unit conversion is compared and shown ------------------------------------------

def test_a_different_unit_conversion_is_a_difference(erp_backend):
    svc, _ = erp_backend
    so = copy.deepcopy(SO)
    so["items"][1]["expected_conversion_factor"] = "12"       # the item master says 10
    out = svc.writer().create_draft("sales_order", so, "op-factor")
    assert out["needs_review"] is True
    assert {"field": "line 2.conversion_factor", "proposed": "12", "erp": "10"} in out["differences"]
    doc = svc.writer()._to_doc("sales_order", __import__("erp.write", fromlist=["clean_payload"]).clean_payload(
        "sales_order", so)[0], "m")
    assert all("expected_conversion_factor" not in row for row in doc["items"])   # never sent


def test_the_approval_card_shows_the_unit_conversion(desk):
    case = _claimed(desk, triage(desk, "OID-002"))
    prop = desk.store.proposal(case["id"])
    assert prop["payload"]["items"][1]["expected_conversion_factor"] == "10"
    view = desk.erp.writer().create_draft("sales_order", prop["payload"], "op-card")
    args = desk.submission_arguments(case, view)
    assert args["lines"][1]["uom"] == "Box" and args["lines"][1]["conversion_factor"] == "10"


# -- findings 6 and 7: recovery checks like a fresh create, and needs a unique marker --------------

def test_recover_checks_the_found_draft(erp_backend):
    svc, server = erp_backend
    w = svc.writer()
    assert w.recover("sales_order", SO, "op-rec")["status"] == "absent"
    made = w.create_draft("sales_order", SO, "op-rec")
    found = w.recover("sales_order", SO, "op-rec")
    assert found["status"] == "applied" and found["view"]["name"] == made["name"]
    _edit(svc, server, made["name"], {"delivery_date": "2000-01-31"})
    found = w.recover("sales_order", SO, "op-rec")
    assert found["status"] == "different" and found["view"]["needs_review"] is True
    current = w.get("sales_order", made["name"])
    w.submit_approved("sales_order", made["name"], current["approval_digest"])
    assert w.recover("sales_order", SO, "op-rec")["status"] == "not_inert"
    with pytest.raises(ERPError) as err:                      # the dedupe path refuses a submitted one too
        w.create_draft("sales_order", dict(SO, delivery_date="2000-01-31"), "op-rec")
    assert err.value.code in ("draft_not_inert", "operation_conflict")


def test_no_insert_without_a_unique_marker_on_a_site(tmp_path, fake_site, monkeypatch):
    from erp import bootstrap
    from tests.conftest import make_service

    w = make_service(tmp_path, "fake_rest", origin=fake_site.origin).writer()
    monkeypatch.setattr(bootstrap, "inspect", lambda t: {"Sales Order": "conflict", "Purchase Invoice": "ok"})
    with pytest.raises(ERPError) as err:
        w.create_draft("sales_order", SO, "op-unique")
    assert err.value.code == "erp_schema_unsupported"
    assert w.find("sales_order", "op-unique") == []
    with pytest.raises(ERPError):
        w.require_unique_marker("sales_order")
    monkeypatch.setattr(bootstrap, "inspect", lambda t: {"Sales Order": "ok", "Purchase Invoice": "ok"})
    assert w.create_draft("sales_order", SO, "op-unique")["docstatus"] == 0


# -- finding 16: the business reference check reads every page --------------------------------------

def test_a_clash_after_the_first_page_is_found(desk):
    w = desk.erp.writer()
    real = w.rt.list_docs

    def paged(doctype, filters=None, fields=None, start=0, limit=20, **kw):
        if doctype == "Sales Order" and fields and "po_no" in fields:
            if start == 0:
                return [{"name": f"SAL-ORD-X-{i}", "po_no": f"OTHER-{i}", "docstatus": 0} for i in range(limit)]
            return [{"name": "SAL-ORD-X-LATE", "po_no": " hf-9001 ", "docstatus": 1}]
        return real(doctype, filters=filters, fields=fields, start=start, limit=limit, **kw)

    w.rt.list_docs = paged
    with pytest.raises(ERPError) as err:
        w.create_draft("sales_order", SO, "op-page")
    assert err.value.code == "duplicate_found" and err.value.fields["name"] == "SAL-ORD-X-LATE"


# -- finding 21: the offline backends behave like Frappe -------------------------------------------

def test_insert_drops_unknown_fields_and_submits_on_docstatus_one(erp_backend):
    svc, _ = erp_backend
    t = svc.transport()
    doc = {"doctype": "Sales Order", "docstatus": 1, "custom_hubzoid_operaton_id": "misspelt",
           **{k: v for k, v in SO.items() if k != "items"},
           "items": [dict(SO["items"][0], bogus_row_field="x")]}
    saved = t.insert("Sales Order", doc)
    again = t.get_doc("Sales Order", saved["name"])
    assert int(again["docstatus"]) == 1
    assert "custom_hubzoid_operaton_id" not in again and not again.get("custom_hubzoid_operation_id")
    assert "bogus_row_field" not in again["items"][0]


# -- findings 9 to 12: billing in stock units, accepted less returned, every bill counts ------------

class _Reader:
    """A tiny ERP reader in the read contract's record shape."""

    def __init__(self, records):
        self.records = records          # resource -> {id: {"fields": {...}, "rows": [...]}}

    def _rec(self, resource, rid):
        r = self.records.get(resource, {}).get(rid)
        return None if r is None else {"resource": resource, "id": rid, "fields": r["fields"], "rows": r.get("rows"),
                                       "erp_modified": "2000-01-01 00:00:00"}

    def get_record(self, resource, rid, ctx):
        rec = self._rec(resource, rid)
        if rec is None:
            raise ERPError("not_found", "no", status=404)
        return rec

    def all_records(self, resource, query, ctx):
        out = []
        for rid, r in self.records.get(resource, {}).items():
            ok = True
            for k, v in query.items():
                if k == "billed_receipt_row":
                    ok &= any(row.get("pr_detail") == v for row in r.get("rows") or [])
                elif k == "billed_order_row":
                    ok &= any(row.get("po_detail") == v for row in r.get("rows") or [])
                elif k in ("item_code", "price_list"):
                    ok &= r["fields"].get(k) == v
                elif k == "search":
                    continue
                else:
                    ok &= r["fields"].get(k) == v
            if ok:
                out.append(self._rec(resource, rid))
        return out


def _erp(*, receipts, invoices=(), po_row=None, item=None):
    item = item or {"fields": {"item_name": "Synthetic Bulk Flour", "stock_uom": "Kg", "disabled": 0},
                    "rows": [{"row_id": "u1", "uom": "Kg", "conversion_factor": "1"},
                             {"row_id": "u2", "uom": "Gram", "conversion_factor": "0.001"}]}
    po_row = po_row or {"row_id": "PO-1-A", "idx": 1, "item_code": "SYN-FLOUR", "qty": "2000", "uom": "Gram",
                        "conversion_factor": "0.001", "stock_qty": "2", "rate": "0.01"}
    return _Reader({
        "suppliers": {"SYN-SUPP-9": {"fields": {"supplier_name": "Synthetic Mill", "disabled": 0}}},
        "items": {"SYN-FLOUR": item},
        "purchase_orders": {"PO-1": {"fields": {"supplier": "SYN-SUPP-9", "docstatus": 1}, "rows": [po_row]}},
        "purchase_receipts": {r["id"]: r for r in receipts},
        "purchase_invoices": {i["id"]: i for i in invoices},
    })


def _receipt(rid, *, qty, stock_qty=None, received=None, returned="0", uom="Kg", factor="1", is_return=0):
    return {"id": rid, "fields": {"supplier": "SYN-SUPP-9", "docstatus": 1, "is_return": is_return},
            "rows": [{"row_id": f"{rid}-1", "idx": 1, "item_code": "SYN-FLOUR", "qty": qty,
                      "received_qty": received or qty, "rejected_qty": "0", "returned_qty": returned, "uom": uom,
                      "conversion_factor": factor, "stock_qty": stock_qty if stock_qty is not None else qty,
                      "purchase_order": "PO-1", "purchase_order_item": "PO-1-A"}]}


def _bill(iid, *, qty, pr_detail=None, docstatus=1):
    return {"id": iid, "fields": {"supplier": "SYN-SUPP-9", "docstatus": docstatus, "bill_no": iid},
            "rows": [{"row_id": f"{iid}-1", "item_code": "SYN-FLOUR", "qty": qty, "uom": "Kg", "conversion_factor": "1",
                      "stock_qty": qty, "po_detail": "PO-1-A", "pr_detail": pr_detail}]}


def _invoice(reader, desk, *, qty, uom="Kg", rate=None, total=None):
    text = f"SYN-FLOUR flour {qty} {uom}" + (f" @ {rate}" if rate else "")
    els = [{"id": "E1", "kind": "text", "loc": "message line 1", "text": "Invoice SM-1 PO PO-1"},
           {"id": "E2", "kind": "text", "loc": "message line 2", "text": "Bill to: Synthetic Meridian Distribution"},
           {"id": "E3", "kind": "text", "loc": "message line 3", "text": "Date 2000-01-10 Due 2000-02-09"},
           {"id": "E4", "kind": "text", "loc": "message line 4", "text": text},
           {"id": "E5", "kind": "text", "loc": "message line 5", "text": f"Total USD {total or 0}"}]
    ex = {"document_kind": "supplier_invoice", "party_name_seen": None,
          "bill_to_seen": {"value": "Synthetic Meridian Distribution", "source_ref": "E2"},
          "reference": {"value": "SM-1", "source_ref": "E1"},
          "document_date": {"value": "2000-01-10", "source_ref": "E3"}, "delivery_date": None,
          "due_date": {"value": "2000-02-09", "source_ref": "E3"},
          "purchase_order_refs": [{"value": "PO-1", "source_ref": "E1"}], "currency_seen": None,
          "lines": [{"source_ref": "E4", "raw_text": text, "item_code_seen": "SYN-FLOUR", "description_seen": None,
                     "quantity": str(qty), "unit_seen": uom, "unit_price": str(rate) if rate else None, "amount": None,
                     "purchase_order_seen": None}],
          "stated_total": {"value": str(total), "source_ref": "E5"} if total else None, "stated_tax": None,
          "instructions_seen": False, "notes": ""}
    cfg = desk.erp.config()
    ctx = propose_lib.Context(company=cfg.company, currency=cfg.currency, warehouse=cfg.warehouses[0],
                              selling_price_list=cfg.selling_price_list, buying_price_list=cfg.buying_price_list,
                              party_type="Supplier", party_id="SYN-SUPP-9", received_date=date(2000, 1, 10),
                              rules=desk.rules(), reader=reader,
                              erp_ctx=ERPContext(hub_id="t", principal="desk:t", party_scope=("supplier", "SYN-SUPP-9")))
    verified = extract_lib.verify(extract_lib.parse_output(ex), els)
    return propose_lib.build(ctx, verified.extraction, verified.problems, els)


def test_gram_receipts_are_converted_to_stock_units(desk):
    reader = _erp(receipts=[_receipt("PR-1", qty="1000", stock_qty="1", uom="Gram", factor="0.001")])
    ok = _invoice(reader, desk, qty=1, rate="10", total="10")             # 1 Kg at 10 = the order's 0.01 per gram
    assert ok.payload and ok.payload["items"][0]["qty"] == "1" and ok.payload["items"][0]["rate"] == "10"
    over = _invoice(reader, desk, qty=2, rate="10", total="20")            # only 1 Kg was received
    q = next(q for q in over.questions if q["code"] == "receipt_quantity_exceeded")
    assert "only 1 received" in q["message"]


def test_rejected_and_returned_goods_are_not_billable(desk):
    receipt = _receipt("PR-1", qty="60", received="100", returned="10")   # 100 in, 40 rejected, 10 returned
    reader = _erp(receipts=[receipt, _receipt("PR-RET", qty="-10", is_return=1)],
                  invoices=[_bill("PI-1", qty="40", pr_detail="PR-1-1")],
                  po_row={"row_id": "PO-1-A", "idx": 1, "item_code": "SYN-FLOUR", "qty": "100", "uom": "Kg",
                          "conversion_factor": "1", "stock_qty": "100", "rate": "2"})
    out = _invoice(reader, desk, qty=20, rate="2", total="40")
    q = next(q for q in out.questions if q["code"] == "receipt_quantity_exceeded")
    assert "only 10 received" in q["message"]


def test_bills_made_straight_from_the_order_count(desk):
    reader = _erp(receipts=[_receipt("PR-1", qty="100")], invoices=[_bill("PI-PO", qty="40")],   # no pr_detail
                  po_row={"row_id": "PO-1-A", "idx": 1, "item_code": "SYN-FLOUR", "qty": "100", "uom": "Kg",
                          "conversion_factor": "1", "stock_qty": "100", "rate": "2"})
    out = _invoice(reader, desk, qty=95, rate="2", total="190")
    q = next(q for q in out.questions if q["code"] == "receipt_quantity_exceeded")
    assert "only 60 received" in q["message"]
    problems = propose_lib.capacity_problems(propose_lib._Reads(reader, None), "SYN-SUPP-9",
                                             {"items": [{"qty": "95", "po_detail": "PO-1-A", "pr_detail": "PR-1-1"}]})
    assert problems and "order row PO-1-A" in problems[0]


def test_a_line_split_across_receipts_still_checks_the_total(desk):
    reader = _erp(receipts=[_receipt("PR-1", qty="60"), _receipt("PR-2", qty="40")],
                  po_row={"row_id": "PO-1-A", "idx": 1, "item_code": "SYN-FLOUR", "qty": "100", "uom": "Kg",
                          "conversion_factor": "1", "stock_qty": "100", "rate": "2"})
    out = _invoice(reader, desk, qty=100, rate="2", total="999")
    assert len([l for l in out.lines if l.get("receipt")][0]["receipt"]["rows"]) == 2
    assert "total_mismatch" in [q["code"] for q in out.questions]
    assert _invoice(reader, desk, qty=100, rate="2", total="200").payload is not None


# -- finding 13: rates are never rounded ----------------------------------------------------------

def test_a_rate_keeps_its_precision(desk):
    out = triage(desk, "OID-001")
    _claimed(desk, out)
    fixed = _correct(desk, out["case_id"], **{"lines[1]__rate": "0.125"})
    prop = desk.store.proposal(out["case_id"])
    assert fixed["state"] == "draft_ready"
    assert prop["payload"]["items"][0]["rate"] == "0.125"
    assert prop["evidence"]["lines"][0]["amount"] == "3"                 # 24 x 0.125, rounded only as an amount


# -- finding 14: a no is a no, and a different company is not ours -------------------------------

def test_a_different_company_name_is_a_question(desk):
    els, ex = _fixture("OID-008")
    els, ex = copy.deepcopy(els), copy.deepcopy(ex)
    e = next(e for e in els if e["id"] == ex["bill_to_seen"]["source_ref"])
    e["text"] = "Bill to: Synthetic Meridian Distribution Overseas"
    ex["bill_to_seen"]["value"] = "Synthetic Meridian Distribution Overseas"
    out = _triage_with(desk, "OID-008", extraction=ex, elements=els)
    assert "wrong_company" in _codes(out)


def test_answering_no_never_books_the_invoice(desk):
    out = triage(desk, "OID-008")
    _claimed(desk, out)
    fixed = _correct(desk, out["case_id"], bill_to_confirmed="no")
    assert fixed["state"] == "needs_clarification" and "not_our_invoice" in _codes(fixed)
    assert desk.store.proposal(out["case_id"])["payload"] is None
    with pytest.raises(DeskError):
        _correct(desk, out["case_id"], bill_to_confirmed="maybe")


# -- finding 15: an unreadable attachment blocks until a person says nothing is missing -------------

def test_an_unreadable_attachment_is_a_question(desk):
    out = triage(desk, "OID-008")
    desk.store.add_document(out["case_id"], ordinal=9, label="invoice-page-2.pdf", filename="invoice-page-2.pdf",
                            media_type="application/pdf", parse_status="failed", error_code="parse_failed",
                            elements_json=[])
    again = desk.rerun("cli:test", out["case_id"], reason="second attachment arrived broken")
    assert "attachment_unreadable" in _codes(again) and desk.store.proposal(out["case_id"])["payload"] is None
    _claimed(desk, again)
    fixed = _correct(desk, out["case_id"], **{"ack__unreadable__9": "nothing_missing"})
    assert fixed["state"] == "draft_ready"


# -- finding 17: a correction is saved only on the case it started from ----------------------------

def test_a_correction_never_lands_after_the_owner_changed(desk, monkeypatch):
    out = triage(desk, "OID-011")
    case = _claimed(desk, out)
    real = propose_lib.build
    calls = []

    def build_then_reassign(*a, **kw):
        result = real(*a, **kw)
        if not calls:
            calls.append(1)
            current = desk.store.get_case(case["id"])
            desk.reassign("admin@example.test", case["id"], to="bob@example.test",
                          expected_revision=current["revision"], reason="moved while ann was answering")
        return result

    monkeypatch.setattr(propose_lib, "build", build_then_reassign)
    before = desk.store.proposal(case["id"])["revision"]
    with pytest.raises((StoreError, DeskError)) as err:
        _correct(desk, case["id"], **{"lines[1]__item_code": "SYN-ITEM-009"})
    assert err.value.code == "revision_conflict"
    after = desk.store.get_case(case["id"])
    assert after["owner"] == "bob@example.test" and desk.store.proposal(case["id"])["revision"] == before


# -- findings 18, 23 and 24: business keys -------------------------------------------------------

def test_a_preview_reserves_no_business_key(desk):
    preview = _triage_with(desk, "OID-001", intake_id="preview-OID-001", preview=True)
    assert preview["state"] != "duplicate"
    real = triage(desk, "OID-001")
    assert real["state"] == "needs_owner"


def test_a_corrected_reference_frees_the_old_one(desk):
    out = triage(desk, "OID-001")                                # HF-7010
    _claimed(desk, out)
    _correct(desk, out["case_id"], reference="HF-7010-B")
    later = _triage_with(desk, "OID-001", intake_id="later-OID-001")
    assert later["state"] == "needs_owner", later["questions"]


def test_same_reference_different_price_is_not_a_silent_duplicate(desk):
    triage(desk, "OID-001")
    els, ex = _fixture("OID-001")
    els, ex = copy.deepcopy(els), copy.deepcopy(ex)
    line = ex["lines"][0]
    e = next(e for e in els if e["id"] == line["source_ref"])
    e["text"] += " @ 9.90"
    line["unit_price"] = "9.90"
    second = _triage_with(desk, "OID-001", extraction=ex, elements=els, intake_id="second-OID-001")
    assert second["state"] == "needs_clarification" and "duplicate_conflict" in _codes(second)


# -- finding 19 is on the engine (tests/test_desk_engine.py); 20 and 22 here -------------------

def test_saving_a_draft_needs_the_workflow_grant_too(tmp_path):
    desk = make_desk(make_hub(tmp_path),
                     grants=lambda actor, perm: perm not in ("wf_order_invoice_case", "workflows_manage"))
    case = _claimed(desk, triage(desk, "OID-001"))
    prop = desk.store.proposal(case["id"])
    with pytest.raises(DeskError) as err:
        desk.request_draft(OWNER, case["id"], expected_revision=case["revision"], digest=prop["digest"])
    assert err.value.code == "permission_denied" and err.value.fields["grant"] == "wf_order_invoice_case"
    assert "hubzoid grant" in err.value.message


def test_a_missing_price_is_answered_by_a_rate(desk, monkeypatch):
    def no_price(ctx, reads, item, unit, on, n, src):
        return None, [propose_lib.Question("price_missing", f"Line {n}: no price.", f"lines[{n}].rate", n,
                                           answer_type="decimal")]

    monkeypatch.setattr(propose_lib, "selling_rate", no_price)
    out = triage(desk, "OID-001")
    assert "price_missing" in _codes(out)
    _claimed(desk, out)
    fixed = _correct(desk, out["case_id"], **{"lines[1]__rate": "9.80", "lines[2]__rate": "3.45"})
    assert fixed["state"] == "draft_ready", fixed["questions"]
    assert Decimal(desk.store.proposal(out["case_id"])["evidence"]["totals"]["computed"]) == Decimal("276.6")
