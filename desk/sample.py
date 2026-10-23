"""The hub's sample loader: set up the synthetic distributor and send its
messages in through the real intake path.

    hubzoid-desk sample install .        # dataset, sender bindings, the two sample channels
    hubzoid-desk sample load .           # the twelve messages (or --set extras | adversarial)

Each message is admitted as a channel delivery on a sample channel, so the
SDK records the receipt, stores the attachments, decides trust and starts the
`order_invoice_intake` work item, exactly as for a real mailbox. The running
hub (`hubzoid run`) routes the deliveries within a few seconds.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import sample_channels

SETS = {"order-invoice-desk": "order-invoice-desk.jsonl", "extras": "order-invoice-desk-extras.jsonl",
        "adversarial": "order-invoice-desk-adversarial.jsonl"}
CHANNELS = {
    "sample-email": {"version": 1, "id": "sample-email", "provider": "sample_email", "enabled": True,
                     "workflow": "order_invoice_intake", "run_as": "workflow",
                     "provider_config": {"address": "orders@synthetic-meridian.example", "trust": "bound_senders",
                                         "synthetic": True}},
    "sample-whatsapp": {"version": 1, "id": "sample-whatsapp", "provider": "sample_whatsapp", "enabled": True,
                        "workflow": "order_invoice_intake", "run_as": "workflow",
                        "provider_config": {"address": "+12025550100", "trust": "bound_senders", "synthetic": True}},
}


def dataset_dir(desk) -> Path:
    installed = desk.hub_dir / "raw_data" / "synthetic" / "distributor-v1"
    if installed.is_dir():
        return installed
    from erp.service import DATASETS_ROOT

    return DATASETS_ROOT / "distributor-v1"


def import_bindings(desk, path: Path, *, actor: str, synthetic: bool) -> dict:
    company = desk.erp.config().company
    count = 0
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["company"] != company:
                raise ValueError(f"{path}: row for company {row['company']!r}, expected {company!r}")
            if row["party_type"] not in ("Customer", "Supplier") or row["channel_kind"] not in ("email", "whatsapp"):
                raise ValueError(f"{path}: party_type is Customer or Supplier, channel_kind is email or whatsapp")
            desk.store.bind(company=company, channel_kind=row["channel_kind"], sender=row["sender"],
                            party_type=row["party_type"], party_id=row["party_id"], source_ref=row["source_ref"],
                            actor=actor, synthetic=synthetic)
            count += 1
    return {"bindings": count, "synthetic": synthetic}


def install_channels(desk, *, actor: str, trust: str = "bound_senders") -> list[dict]:
    from hubzoid.channels import config as channel_config
    from hubzoid.channels import store as channel_store

    sample_channels.register()
    out = []
    for cid, data in CHANNELS.items():
        data = json.loads(json.dumps(data))
        data["provider_config"]["trust"] = trust
        current = channel_store.get_connection(desk.hub_dir, channel_config.hub_of(desk.hub_dir), cid)
        expected = current["revision"] if current else "absent"
        if current and current.get("config_json") == data:
            out.append({"id": cid, "revision": current["revision"], "status": "unchanged"})
            continue
        result = channel_config.apply(desk.hub_dir, data, expected_revision=expected, actor=actor)
        out.append({"id": cid, "revision": result.get("revision"), "status": result.get("status")})
    return out


def load(desk, *, which: str = "order-invoice-desk", events: list[str] | None = None) -> list[dict]:
    from hubzoid.channels import config as channel_config

    if which not in SETS:
        from .service import DeskError

        raise DeskError("validation_failed", f"There is no sample set {which!r}. Sets: {', '.join(SETS)}.",
                        status=422)

    sample_channels.register()
    root = dataset_dir(desk)
    rows = [json.loads(l) for l in (root / "messages" / SETS[which]).read_text().splitlines()]
    email_conn = channel_config.active(desk.hub_dir, "sample-email")
    wa_conn = channel_config.active(desk.hub_dir, "sample-whatsapp")
    out = []
    for env in rows:
        event = env["provider_event_id"]
        if events and event not in events:
            continue
        if env["provider"] == "email":
            raw = (root / "raw" / f"{event}.eml").read_bytes()
            # A replayed message keeps its provider id, so the SDK sees the same delivery twice.
            provider_id = env["provider_message_id"].strip("<>")
            result = sample_channels.deliver_email(desk, email_conn, raw, provider_id)
        else:
            message = json.loads((root / "raw" / f"{event}.whatsapp.json").read_text())
            result = sample_channels.deliver_whatsapp(desk, wa_conn, message)
        out.append(dict(result, event=event, channel=env["provider"], synthetic=True))
    return out
