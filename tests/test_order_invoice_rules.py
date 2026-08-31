"""The deterministic desk against the fixture oracle: the twelve documents
give ten clean proposals and two typed questions, every value tied to its
source and rule. Recorded parser elements and scripted extractions stand in
for Docling and the model (both are tested on their own)."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from desk import extract as extract_lib
from desk import policy as policy_lib
from desk.store import case_number
from tests.desk_helpers import EXPECTED, envelopes, make_desk, make_hub, triage

TWELVE = [f"OID-{n:03d}" for n in range(1, 13)]


@pytest.fixture
def desk(tmp_path):
    return make_desk(make_hub(tmp_path))


def _lines(desk, out):
    prop = desk.store.proposal(out["case_id"])
    return prop, [[l["item_code"], l["qty"], l["uom"], l["rate"]] for l in (prop["payload"] or {}).get("items", [])]


def test_twelve_documents_give_ten_drafts_and_two_questions(desk):
    envs = envelopes()
    drafts, questions = [], []
    for event in TWELVE:
        out = triage(desk, event, envs=envs)
        expect = EXPECTED[event]
        prop, lines = _lines(desk, out)
        if expect["outcome"] == "draft":
            assert out["state"] == "needs_owner", (event, out["questions"])
            assert prop["kind"] == expect["kind"] and out["party"]["id"] == expect["party"]
            assert lines == [[c, q, u, str(Decimal(r).quantize(Decimal("0.01")))] for c, q, u, r in expect["lines"]] \
                or [l[:3] for l in lines] == [e[:3] for e in expect["lines"]], (event, lines)
            assert Decimal(prop["evidence"]["totals"]["computed"]) == Decimal(expect["grand_total"]), event
            drafts.append(event)
        else:
            assert out["state"] == "needs_clarification", (event, out["state"])
            codes = [q["code"] for q in out["questions"]]
            assert expect["question"] in codes, (event, codes)
            assert prop["payload"] is None
            questions.append(event)
    assert len(drafts) == 10 and questions == ["OID-011", "OID-012"]


def test_every_value_traces_to_source_and_rule(desk):
    for event in TWELVE[:10]:
        out = triage(desk, event)
        prop = desk.store.proposal(out["case_id"])
        for line in prop["evidence"]["lines"]:
            assert line["source"]["element"].startswith("E") and line["source"]["text"]
            assert line["item"]["rule"]["id"] in ("exact_item_code", "alias")
            assert line["unit"]["rule"]["id"] in ("O5", "unit_word", "unit_alias")
            assert line["rate"]["rule"]["id"] in ("O6", "I5")
        assert prop["evidence"]["header"]["reference"]["source"]["element"]


def test_ambiguous_item_offers_both_bolts_and_no_draft(desk):
    out = triage(desk, "OID-011")
    q = next(q for q in out["questions"] if q["code"] == "ambiguous_item")
    assert {o["value"] for o in q["options"]} == {"SYN-ITEM-009", "SYN-ITEM-010"}
    assert q["field"] == "lines[1].item_code" and q["source"]["text"].startswith("80 x")


def test_partial_receipt_prevents_overbilling(desk):
    out = triage(desk, "OID-012")
    q = next(q for q in out["questions"] if q["code"] == "receipt_quantity_exceeded")
    assert "only 40 received" in q["message"] and q["options"][0]["value"] == "40"


def test_previously_billed_quantity_is_respected(desk):
    out = triage(desk, "OID-006")           # 100 received, 40 already billed (ACC-PINV-2000-00001)
    prop = desk.store.proposal(out["case_id"])
    row = prop["payload"]["items"][0]
    assert row["qty"] == "60" and row["pr_detail"] == "MAT-PRE-2000-00001-1" and row["po_detail"] == "PUR-ORD-2000-00001-A"


def test_exact_alias_and_unit_conversion(desk):
    out = triage(desk, "OID-002")
    prop = desk.store.proposal(out["case_id"])
    first, second = prop["evidence"]["lines"]
    assert first["item"]["rule"]["id"] == "alias" and first["item"]["code"] == "SYN-ITEM-009"
    assert first["rate"]["rule"]["text"].startswith("SYN-RETAIL customer contract price")
    assert second["unit"]["uom"] == "Box" and second["unit"]["conversion_factor"] == "10"
    assert second["rate"]["value"] == "8.5"


def test_no_fuzzy_auto_match(tmp_path):
    desk = make_desk(make_hub(tmp_path))
    out = triage(desk, "OID-011")
    prop = desk.store.proposal(out["case_id"])
    assert prop["evidence"]["lines"][0].get("item") is None      # a similar name is never accepted


def test_decimal_currency_rounding(desk):
    from desk.propose import money

    assert money(Decimal("0.125")) == Decimal("0.13") and money(Decimal("2.675")) == Decimal("2.68")
    out = triage(desk, "OID-003")
    prop = desk.store.proposal(out["case_id"])
    assert prop["evidence"]["totals"]["computed"] == "210.3"
    assert all(isinstance(l["qty"], str) and isinstance(l["rate"], str) for l in prop["payload"]["items"])


def test_document_kind_not_authority(desk):
    """A supplier's document that calls itself an order is still a supplier
    document: the binding decides, and the mismatch is a question."""
    envs = envelopes()
    case, _ = desk.open_case(dict(_payload(envs["OID-008"]), intake_id="kind-test"), work_item_id="wi-kind")
    ex = json.loads((_fixtures("extractions") / "OID-008.json").read_text())
    ex["document_kind"] = "customer_order"
    out = desk.stage_match(case["id"], ex, elements=_elements("OID-008"))
    assert "kind_mismatch" in [q["code"] for q in out["questions"]]


def test_unbound_sender_needs_a_person(desk):
    envs = envelopes()
    env = dict(envs["OID-001"], sender={"external_id": "stranger@unbound.example", "display_name": "Synthetic Harbor Foods"})
    case, _ = desk.open_case(dict(_payload(env), intake_id="unbound"), work_item_id="wi-unbound")
    out = desk.stage_match(case["id"], json.loads((_fixtures("extractions") / "OID-001.json").read_text()),
                           elements=_elements("OID-001"))
    assert out["party"]["id"] is None and "party_unknown" in [q["code"] for q in out["questions"]]


def test_duplicate_same_business_reference(desk):
    first = triage(desk, "OID-004")
    second = triage(desk, "OID-A02")          # the same order by email
    assert first["state"] == "needs_owner" and second["state"] == "duplicate"
    assert second["duplicate_of"] == first["number"]


def test_same_reference_different_content_conflicts(desk):
    triage(desk, "OID-004")
    envs = envelopes()
    case, _ = desk.open_case(dict(_payload(envs["OID-A02"]), intake_id="conflict"), work_item_id="wi-conflict")
    ex = json.loads((_fixtures("extractions") / "OID-A02.json").read_text())
    ex["lines"][0]["quantity"] = "10"
    ex["lines"] = ex["lines"][:1]
    out = desk.stage_match(case["id"], ex, elements=_elements("OID-A02"))
    assert out["state"] == "needs_clarification" and "duplicate_conflict" in [q["code"] for q in out["questions"]]


def test_erp_duplicate_bill_number(desk):
    out = triage(desk, "OID-A07")
    assert out["state"] == "duplicate" and out["duplicate_of"] == "ACC-PINV-2000-00001"


def test_prompt_injection_is_data(desk):
    out = triage(desk, "OID-A03")
    prop = desk.store.proposal(out["case_id"])
    assert "instructions_ignored" in [w["code"] for w in prop["warnings"]]
    assert out["state"] == "needs_clarification" and "receipt_missing" in [q["code"] for q in out["questions"]]
    disguised = triage(desk, "OID-A04")       # an injected display name grants nothing
    assert disguised["state"] == "needs_owner" and disguised["party"]["id"] == "SYN-SUPP-003"


def test_wrong_company_is_never_booked(desk):
    out = triage(desk, "OID-A05")
    assert "wrong_company" in [q["code"] for q in out["questions"]]


def test_extraction_values_must_be_in_their_source(desk):
    envs = envelopes()
    ex = json.loads((_fixtures("extractions") / "OID-001.json").read_text())
    ex["lines"][0]["quantity"] = "240"       # not in the document
    ex["lines"][1]["unit_price"] = "1.00"
    verified = extract_lib.verify(extract_lib.parse_output(ex), _elements("OID-001"))
    assert verified.extraction.lines[0].quantity is None and verified.extraction.lines[1].unit_price is None
    assert {p["reason"] for p in verified.problems} == {"value_not_in_source"}
    case, _ = desk.open_case(dict(_payload(envs["OID-001"]), intake_id="tamper"), work_item_id="wi-tamper")
    out = desk.stage_match(case["id"], ex, elements=_elements("OID-001"))
    assert "quantity_missing" in [q["code"] for q in out["questions"]]


def test_carton_rule_changes_only_that_line_and_keeps_history(desk, tmp_path):
    before = triage(desk, "OID-X01")
    assert "unit_unknown" in [q["code"] for q in before["questions"]]
    aliases = desk.hub_dir / "knowledge" / "sku-aliases.csv"
    aliases.write_text(aliases.read_text() + "Synthetic Meridian Distribution,*,SYN-ITEM-001,SYN-ITEM-001,carton,Nos,12,"
                       "builder correction 2000-01-15\n")
    case = desk.store.get_case(before["case_id"])
    after = desk.rerun("cli:test", case["id"], reason="carton means twelve")
    assert after["state"] == "needs_owner"
    old, new = desk.store.proposal(case["id"], 1), desk.store.proposal(case["id"], 2)
    assert old["payload"] is None and [l["qty"] for l in new["payload"]["items"]] == ["24", "6"]
    assert new["evidence"]["lines"][0]["unit"]["rule"]["id"] == "unit_alias"
    assert new["evidence"]["lines"][1]["unit"] == old["evidence"]["lines"][1]["unit"]   # only the carton line changed
    assert old["policy_digest"] != new["policy_digest"]
    # A restart is a new process: the rule is a file and the proposal is stored.
    from tests.desk_helpers import make_desk as again

    fresh = again(desk.hub_dir)
    assert fresh.store.proposal(case["id"])["revision"] == 2


def test_alias_conflict_refuses_rules(tmp_path):
    hub = make_hub(tmp_path)
    p = hub / "knowledge" / "sku-aliases.csv"
    p.write_text(p.read_text() + "Synthetic Meridian Distribution,SYN-CUST-002,AH-B840,SYN-ITEM-010,,,,x\n")
    with pytest.raises(policy_lib.PolicyError) as err:
        policy_lib.load(hub, "Synthetic Meridian Distribution")
    assert err.value.code == "alias_conflict" and err.value.rows == [2, 4]


def test_policy_actions_must_be_question(tmp_path):
    hub = make_hub(tmp_path)
    p = hub / "knowledge" / "order-policy.md"
    p.write_text(p.read_text().replace("price_difference_action: question", "price_difference_action: accept"))
    with pytest.raises(policy_lib.PolicyError) as err:
        policy_lib.load(hub, "Synthetic Meridian Distribution")
    assert err.value.code == "policy_invalid" and err.value.field == "price_difference_action"


def test_corrections_answer_questions_and_stay_case_only(desk):
    out = triage(desk, "OID-011")
    case = desk.store.get_case(out["case_id"])
    desk.claim("op@example.test", case["id"], expected_revision=case["revision"])
    case = desk.store.get_case(case["id"])
    fixed = desk.correct("op@example.test", case["id"], expected_revision=case["revision"],
                         fields=[{"path": "lines[1].item_code", "value": "SYN-ITEM-009", "source_ref": "E3"}])
    assert fixed["state"] == "draft_ready"
    prop = desk.store.proposal(case["id"])
    assert prop["evidence"]["lines"][0]["item"]["rule"]["id"] == "correction"
    assert (desk.hub_dir / "knowledge" / "sku-aliases.csv").read_text().count("\n") == 3   # no rule was written


def test_claim_race(desk):
    import threading

    out = triage(desk, "OID-001")
    case = desk.store.get_case(out["case_id"])
    results = {}

    def go(who):
        try:
            results[who] = desk.claim(who, case["id"], expected_revision=case["revision"])["owner"]
        except Exception as exc:  # noqa: BLE001
            results[who] = getattr(exc, "code", type(exc).__name__)

    threads = [threading.Thread(target=go, args=(w,)) for w in ("ann@example.test", "bob@example.test")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results.values()) == sorted(["owner_conflict", next(v for v in results.values() if "@" in v)])
    assert desk.store.get_case(case["id"])["owner"] in ("ann@example.test", "bob@example.test")


def test_outsider_cannot_see_or_claim(tmp_path):
    desk = make_desk(make_hub(tmp_path), grants=lambda actor, perm: actor != "outsider@example.test")
    out = triage(desk, "OID-001")
    with pytest.raises(Exception) as err:
        desk.list("outsider@example.test")
    assert err.value.code == "permission_denied"
    with pytest.raises(Exception) as err:
        desk.claim("outsider@example.test", out["case_id"], expected_revision=out["revision"])
    assert err.value.code == "permission_denied"


def test_corrupted_attachment_is_a_clear_error(tmp_path):
    desk = make_desk(make_hub(tmp_path))
    from tests.desk_helpers import payload

    out = desk.stage_parse(payload(envelopes()["OID-A06"]), work_item_id="wi-a06")
    assert out["stop"] == "parse_failed"
    case = desk.store.get_case(out["case_id"])
    assert case["state"] == "error" and case["error_code"] == "parse_failed"


def test_case_numbers_are_stable(desk):
    a = triage(desk, "OID-001")
    b = triage(desk, "OID-002")
    assert (a["number"], b["number"]) == ("CASE-000001", "CASE-000002")
    again = desk.open_case(_payload(envelopes()["OID-001"]), work_item_id="wi-retry")[0]
    assert case_number(again) == "CASE-000001"


# -- helpers ----------------------------------------------------------------------------------------

def _fixtures(kind):
    from tests.desk_helpers import EXTRACTIONS, ELEMENTS

    return {"extractions": EXTRACTIONS, "elements": ELEMENTS}[kind]


def _elements(event):
    return json.loads((_fixtures("elements") / f"{event}.json").read_text())["elements"]


def _payload(env):
    from tests.desk_helpers import payload

    return payload(env)
