"""Shared helpers for the desk tests: a temporary hub on the sample backend,
bound senders, and the recorded fixtures replayed without Docling or a model."""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from erp import config as config_lib
from erp.service import ERPService

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "erp" / "datasets" / "distributor-v1"
ELEMENTS = ROOT / "evals" / "fixtures" / "elements"
EXTRACTIONS = ROOT / "evals" / "fixtures" / "extractions"
EXPECTED = json.loads((DATASET / "expected" / "order-invoice-desk.json").read_text())
PROVIDER = {"email": "sample_email", "whatsapp": "sample_whatsapp"}


def make_hub(tmp_path: Path, *, writes: bool = True, mode: str = "approve") -> Path:
    hub = tmp_path / "order-invoice-desk"
    hub.mkdir(parents=True)
    shutil.copytree(ROOT / "knowledge", hub / "knowledge")
    (hub / "connectors").mkdir()
    (hub / "connectors" / "erpnext.yaml").write_text(config_lib.dump(dict(config_lib.SAMPLE_DEFAULT, writes_enabled=writes)))
    (hub / "work-items.yaml").write_text(f"version: 1\nwrite_modes:\n  erp.submit: {mode}\n")
    # ERP mode is chosen explicitly: an unset value is inbox first on a fresh hub.
    (hub / "hub.settings.yaml").write_text("version: 1\nvalues:\n  DESK_ERP_ENABLED: true\n")
    return hub


def make_desk(hub: Path, grants=None):
    from desk.service import Desk

    erp = ERPService(hub, hub / ".hubzoid" / "data" / "erp", deployment_id="test-deployment",
                     secret_resolver=lambda ref: None)
    desk = Desk(hub, erp=erp, fetch=lambda att: (DATASET / "attachments" / att["filename"]).read_bytes(),
                grants=grants or (lambda actor, perm: True))
    bind_samples(desk)
    return desk


def bind_samples(desk) -> None:
    with (DATASET / "bindings.csv").open() as fh:
        for row in csv.DictReader(fh):
            desk.store.bind(company=row["company"], channel_kind=row["channel_kind"], sender=row["sender"],
                            party_type=row["party_type"], party_id=row["party_id"], source_ref=row["source_ref"],
                            actor="test", synthetic=True)


def envelopes() -> dict[str, dict]:
    out = {}
    for name in ("order-invoice-desk.jsonl", "order-invoice-desk-extras.jsonl", "order-invoice-desk-adversarial.jsonl"):
        for line in (DATASET / "messages" / name).read_text().splitlines():
            env = json.loads(line)
            out[env["provider_event_id"]] = env
    return out


def payload(env: dict) -> dict:
    return {"intake_id": env["provider_event_id"], "provider": PROVIDER[env["provider"]], "channel": env["channel_id"],
            "sender": env["sender"], "text": env["text"], "received_at": env["received_at"],
            "attachments": env["attachments"]}


def triage(desk, event: str, *, envs=None) -> dict:
    """The intake work item's three stages, with recorded elements and the
    scripted extraction in place of Docling and the model."""
    env = (envs or envelopes())[event]
    case, _ = desk.open_case(payload(env), work_item_id=f"wi-{event}")
    rec = json.loads((ELEMENTS / f"{event}.json").read_text())
    store_elements(desk, case["id"], rec["elements"])
    extraction = json.loads((EXTRACTIONS / f"{event}.json").read_text())
    return desk.stage_match(case["id"], extraction, elements=rec["elements"], parse_problems=rec["problems"])


def store_elements(desk, case_id: str, elements: list[dict]) -> None:
    """What the parse stage stores: the elements of each document."""
    by_doc: dict[int, list] = {}
    for e in elements:
        by_doc.setdefault(e["doc"], []).append(e)
    for ordinal, (doc, els) in enumerate(sorted(by_doc.items())):
        desk.store.add_document(case_id, ordinal=ordinal, label=els[0]["doc_label"], filename=els[0]["doc_label"],
                                media_type=None, parse_status="parsed", parser="recorded", elements_json=els)
