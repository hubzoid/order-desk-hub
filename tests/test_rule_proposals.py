from __future__ import annotations

import copy

import pytest

from desk import records
from desk.service import DeskError
from tests.test_inbox_workflow import ELEMENTS, extraction, inbox


@pytest.mark.slow
@pytest.mark.parametrize("existing", [False, True])
def test_sdk_rule_proposal_waits_for_authorized_activation_then_changes_next_record(tmp_path, monkeypatch, existing):
    # Real SDK proposal storage, exact-diff handling, grants and activation.
    # No model/provider call. SDK activation may inspect Git through a child
    # process; this temporary hub intentionally has no Git repository.
    monkeypatch.delenv("HUBZOID_DATA_DIR", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    d = inbox(tmp_path)
    d._grants = None
    path = d.hub_dir / records.RULE_FILE
    if not existing:
        path.unlink()
    before = path.read_bytes() if path.exists() else None
    source = d.hub_dir / "knowledge/sources/item.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("Owner reviewed mapping: Widget is Internal Widget. Holdout: HOLDOUT-1.\n")
    from hubzoid import access, migrations
    from hubzoid.db import operational_engine
    from hubzoid.hub_changes import service as hc
    migrations.upgrade(operational_engine(d.hub_dir), "operational")
    gs = access.store_for(d.hub_dir)
    builder, owner, member = "builder@example.org", "owner@example.org", "member@example.org"
    for who, grant in ((builder, "desk_manage"), (builder, "curator"), (owner, "manage_access")):
        gs.grant(who, d.hub_id, grant, actor="test")
    new_rules = d.record_rules().model_dump()
    new_rules["aliases"] = [{"source": "Widget", "target": "Internal Widget", "source_ref": "knowledge/sources/item.md"}]
    with pytest.raises(DeskError) as denied:
        d.propose_record_rules(member, rules=new_rules, reason="Not authorized")
    assert denied.value.code == "permission_denied"
    proposed = d.propose_record_rules(builder, rules=new_rules, reason="Reviewed mapping and holdout")
    assert proposed["status"] == "pending" and proposed["paths"] == [records.RULE_FILE]
    assert (path.read_bytes() if path.exists() else None) == before
    assert "Internal Widget" in proposed["diff"]
    with pytest.raises(hc.ChangeError) as denied:
        hc.decide(d.hub_dir, proposed["id"], actor=builder, decision="approve", expected_revision=proposed["revision"])
    assert denied.value.code == "permission_denied"
    activated = hc.decide(d.hub_dir, proposed["id"], actor=owner, decision="approve", expected_revision=proposed["revision"])
    assert activated["status"] == "applied"
    assert d.record_rules().aliases[0].target == "Internal Widget"
    assert path.read_text().startswith("---\nname:")
    payload = {"intake_id": "holdout-mail", "channel": "orders-mail", "provider": "email", "sender": {"external_id": "supplier@example.org"}}
    case, _ = d.open_case(payload, work_item_id="holdout")
    elements = copy.deepcopy(ELEMENTS)
    elements[0]["text"] = elements[0]["text"].replace("INV-A", "HOLDOUT-1")
    extracted = extraction()
    extracted["reference"]["value"] = "HOLDOUT-1"
    result = d.stage_match(case["id"], extracted, elements=elements)
    assert d.store.record(case["id"])["lines"][0]["item"] == "Internal Widget"
    assert result["record"]["rule_revision"] == d.record_rules().digest()
