from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import time

import pytest

from desk.service import DeskError
from tests.desk_helpers import make_hub, make_desk, triage, ELEMENTS, EXTRACTIONS, envelopes, payload, store_elements
from tests.test_review_fixes import OWNER, _claimed, _expect


def test_two_invoices_cannot_draft_the_same_remaining_sixty_units(tmp_path):
    d = make_desk(make_hub(tmp_path))
    source = json.loads((EXTRACTIONS / 'OID-009.json').read_text())
    # Use the real sample invoice and its actual receipt capacities. Both
    # proposals are prepared before either draft, then race at dispatch.
    first = _claimed(d, triage(d, 'OID-009'))
    env = copy.deepcopy(envelopes()['OID-009'])
    env['provider_event_id'] = 'OID-009-other'
    case, _ = d.open_case(payload(env), work_item_id='second')
    els = json.loads((ELEMENTS / 'OID-009.json').read_text())['elements']
    reference = source['reference']['value']
    source['reference']['value'] = reference + '-OTHER'
    for e in els:
        e['text'] = e['text'].replace(reference, reference + '-OTHER')
    store_elements(d, case['id'], els)
    second = _claimed(d, d.stage_match(case['id'], source, elements=els))
    def send(case):
        expect, prop = _expect(d, case)
        try:
            view = d.dispatch_draft(case['id'], requester=OWNER, expect=expect,
                                   arguments={'kind': prop['kind'], 'payload': prop['payload'], 'external_ref': case['id']})
            d.record_draft(case['id'], work_item_id=case['id'], operation_id=None, view=view, actor=OWNER)
            return view['name']
        except DeskError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(send, (first, second)))
    assert sum(r in ('receipt_capacity_changed', 'receipt_capacity_uncertain') for r in results) == 1, results
    assert sum(r.startswith('PINV-') or r.startswith('ACC-PINV-') for r in results) == 1, results


def test_tax_question_accepts_only_reviewed_template_answer(tmp_path):
    d = make_desk(make_hub(tmp_path))
    source = json.loads((EXTRACTIONS / 'OID-009.json').read_text())
    els = json.loads((ELEMENTS / 'OID-009.json').read_text())['elements']
    els.append({'id': 'E999', 'text': 'Tax 2.00', 'loc': 'tax line', 'doc': 1, 'doc_label': 'invoice'})
    source['stated_tax'] = {'value': '2.00', 'source_ref': 'E999'}
    case, _ = d.open_case(payload(envelopes()['OID-009']), work_item_id='tax')
    store_elements(d, case['id'], els)
    out = d.stage_match(case['id'], source, elements=els)
    claimed = d.claim(OWNER, case['id'], expected_revision=out['revision'])
    answered = d.correct(OWNER, case['id'], expected_revision=claimed['revision'],
                         fields=[{'path': 'tax', 'value': 'erp_template_checked'}])
    assert not any(q['code'] == 'tax_present' for q in answered['questions'])
    assert d.store.proposal(case['id'])['corrections']['tax'] == 'erp_template_checked'


def test_post_submit_content_change_preserves_review_and_authoritative_total(tmp_path):
    d = make_desk(make_hub(tmp_path))
    case = _claimed(d, triage(d, 'OID-001'))
    _, prop = _expect(d, case)
    view = d.erp.writer().create_draft(prop['kind'], prop['payload'], 'post-submit-test')
    view.update(docstatus=1, needs_review=True)
    view['totals']['grand_total'] = '2417.40'
    d.record_submitted(case['id'], work_item_id='submitted', operation_id=None, view=view, actor=OWNER)
    shown = d.show(OWNER, case['id'])
    assert shown['total'] == '2417.40'
    assert shown['needs_review']
    assert shown['state'] == 'needs_review' and shown['erp']['docstatus'] == 1
    assert shown['next_action'] and shown['actions'] == []
    assert d.list(OWNER, state='needs_review')['cases'][0]['case_id'] == case['id']
    from desk.store import OPEN
    assert 'needs_review' in OPEN
    with pytest.raises(DeskError):
        d.rerun(OWNER, case['id'], reason='must not repost')
    assert shown['error']['code'] == 'post_submit_review_required'
    assert d.store.get_case(case['id'])['differences_json']


def test_answering_an_unowned_case_claims_it_in_the_same_step(tmp_path, monkeypatch):
    """The chat tool claims a case nobody owns before answering its question,
    so a person needs one step, not a claim round trip."""
    import asyncio
    import importlib.util
    import sys

    from agents.tool_context import ToolContext

    from tests.desk_helpers import ROOT

    d = make_desk(make_hub(tmp_path))
    source = json.loads((EXTRACTIONS / 'OID-009.json').read_text())
    els = json.loads((ELEMENTS / 'OID-009.json').read_text())['elements']
    els.append({'id': 'E999', 'text': 'Tax 2.00', 'loc': 'tax line', 'doc': 1, 'doc_label': 'invoice'})
    source['stated_tax'] = {'value': '2.00', 'source_ref': 'E999'}
    case, _ = d.open_case(payload(envelopes()['OID-009']), work_item_id='tax-tool')
    store_elements(d, case['id'], els)
    out = d.stage_match(case['id'], source, elements=els)
    spec = importlib.util.spec_from_file_location("order_tools_answer_test", ROOT / "tools_local/order_invoice.py")
    tools = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, tools)
    spec.loader.exec_module(tools)
    monkeypatch.setattr(tools, "_HUB", d.hub_dir)
    monkeypatch.setattr(tools, "_desk", lambda: d)
    monkeypatch.setattr(tools, "_actor", lambda: OWNER)
    args = json.dumps({"case": case['id'], "expected_revision": out['revision'], "field": "tax",
                       "value": "erp_template_checked", "source_ref": ""})
    ctx = ToolContext(context=None, tool_name="desk_answer", tool_call_id="t1", tool_arguments=args)
    answered = json.loads(asyncio.run(tools.desk_answer.on_invoke_tool(ctx, args)))
    assert not isinstance(answered.get("error"), dict), answered
    assert d.show(OWNER, case['id'])['owner'] == OWNER
    assert d.store.proposal(case['id'])['corrections']['tax'] == 'erp_template_checked'


def test_approving_drafts_for_this_chat_never_covers_a_submission(tmp_path, monkeypatch):
    """"Approve for this chat" on an inert ERP draft is a grant for that one
    tool. A submission in the same chat still asks a person, every time."""
    import shutil

    from hubzoid.hitl import service as hitl

    from tests.desk_helpers import ROOT

    for key in ("DATABASE_URL", "HUBZOID_OPERATIONAL_DB", "HUBZOID_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    hub = tmp_path / "order-desk"
    hub.mkdir()
    shutil.copy(ROOT / "hitl.yaml", hub / "hitl.yaml")
    from hubzoid import migrations
    from hubzoid.db import operational_engine

    migrations.upgrade(operational_engine(hub), "operational")
    me = hitl.Caller(account_id="op-1", email="op@example.test", conversation_id="chat-1")
    draft = hitl.check(hub, "order-desk", me, tool="desk_create_draft", arguments={"case": "CASE-1"})
    assert draft.action == "ask" and draft.request["allow_chat"]
    hitl.decide(hub, "order-desk", draft.request["id"], account_id="op-1", email="op@example.test",
                decision="approve_chat")
    again = hitl.check(hub, "order-desk", me, tool="desk_create_draft", arguments={"case": "CASE-2"})
    assert again.action == "allow"                          # drafts: once per chat
    submit = hitl.check(hub, "order-desk", me, tool="desk_approve_submission",
                        arguments={"case": "CASE-1", "operation_id": "op", "digest": "sha256:x"})
    assert submit.action == "ask" and not submit.request["allow_chat"]


def test_an_unknown_sample_set_names_the_sets(tmp_path):
    from desk import sample

    d = make_desk(make_hub(tmp_path))
    with pytest.raises(DeskError) as err:
        sample.load(d, which="extra")
    assert err.value.code == "validation_failed" and "order-invoice-desk, extras, adversarial" in err.value.message
