"""Two synthetic channels for the sample: a mailbox and a WhatsApp number that
only the hub's sample loader can fill.

They are real Hubzoid channel adapters, registered by the hub, so a sample
message takes the same path as a real one: SDK admission (receipt, duplicate
delivery check), the router, the SDK intake service (attachments stored and
type-checked, quarantine or dispatch) and a work item on the
`order_invoice_intake` workflow. The only difference is where trust comes
from: a real mailbox needs an aligned DKIM signature, a real WhatsApp number
quarantines every sender for a person, and these sample channels accept a
sender only when the desk has a binding for it (or quarantine everything,
with `trust: quarantine`). They never send anything: replies are refused.

Nothing reaches them from outside: there is no webhook and no poller. The
SDK command this stands in for (`hubzoid intake import --synthetic`) is on the
hub's "later for the SDK" list.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROVIDERS = ("sample_email", "sample_whatsapp")


class SampleAttachments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_types: list[str] = Field(default_factory=lambda: [
        "application/pdf", "text/csv", "text/plain", "image/png", "image/jpeg",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"])
    max_count: int = Field(default=10, ge=0, le=10)
    max_each_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    max_total_bytes: int = Field(default=40 * 1024 * 1024, ge=1, le=40 * 1024 * 1024)


class SampleConfig(BaseModel):
    """`provider_config` for the sample channels."""
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=320)
    trust: Literal["bound_senders", "quarantine"] = "bound_senders"
    synthetic: Literal[True] = True
    attachments: SampleAttachments = Field(default_factory=SampleAttachments)


def _base():
    from hubzoid.channels.protocol import Adapter

    return Adapter


def _adapters():
    from hubzoid.channels.errors import ChannelError
    from hubzoid.channels.protocol import Adapter, Check

    class _Sample(Adapter):
        config_model = SampleConfig
        webhook = False
        customer_facing = True
        recovery = "escalate"

        def check(self, config, settings):
            out = []
            if config.agent:
                out.append(("/agent", "A sample channel is intake: set workflow, not agent"))
            if config.run_as not in (None, "workflow"):
                out.append(("/run_as", "Sample senders are outside the organization: run_as is workflow"))
            return out

        def workspace(self, conn):
            return conn.settings.address.lower()

        def allowed_types(self, conn):
            return [t.lower() for t in conn.settings.attachments.allowed_types]

        def attachment_limits(self, conn):
            a = conn.settings.attachments
            return a.max_count, a.max_each_bytes, a.max_total_bytes

        def verify(self, conn, raw, headers, query):
            raise ChannelError("not_found", "Sample channels are filled by the hub's sample loader only.", status=404)

        def parse(self, conn, verified):
            return list(verified.payload or [])

        def route(self, hub_dir, conn, row, thread, event):
            from hubzoid.channels.router import _finish
            from hubzoid.intake import service as intake

            item = intake.capture(conn, row, thread, event)
            if item["state"] == "dispatched":
                _finish(hub_dir, row["id"], "routed", None, target="work_item", work_item_id=item["work_item_id"],
                        intake_id=item["id"])
            else:
                _finish(hub_dir, row["id"], "routed", None, target="intake", intake_id=item["id"], replied="intake")
            return "routed", None

        def reply(self, conn, origin, outbound):
            raise ChannelError("not_supported", "Sample channels never send. Nothing went out.", status=422)

        def doctor(self, conn, inputs):
            return [Check("synthetic", "ok", f"Synthetic sample channel {conn.settings.address}: filled only by "
                          "hubzoid-desk sample load, never sends.")]

    class SampleEmail(_Sample):
        provider = "sample_email"
        title = "Synthetic sample mailbox"

        def fetch_file(self, conn, event, ref):
            from hubzoid.channels.email import mime
            from hubzoid.intake import service as intake

            source = event.data.get("source_file_id")
            _, raw = intake.files_for(conn.hub_dir).content(intake.system_actor(conn.id), source)
            return mime.attachment_bytes(raw, ref["part"])

    class SampleWhatsApp(_Sample):
        provider = "sample_whatsapp"
        title = "Synthetic sample WhatsApp number"

        def fetch_file(self, conn, event, ref):
            raise ChannelError("attachment_unavailable", "Sample WhatsApp messages carry text only.", status=422)

    return SampleEmail(), SampleWhatsApp()


def register() -> None:
    """Make the two sample providers known in this process (idempotent)."""
    try:
        from hubzoid.channels import registry
    except ImportError:
        return
    if all(p in registry._registered for p in PROVIDERS):
        return
    for adapter in _adapters():
        registry.register(adapter)


# -- delivering one sample message ------------------------------------------------------------------

def _trust(desk, conn, kind: str, sender: str) -> dict:
    if conn.settings.trust == "quarantine":
        return {"decision": "quarantined", "reason": "synthetic_quarantine", "evidence": {"synthetic": True}}
    row = desk.store.binding(desk.erp.config().company, kind, sender)
    if row is None:
        return {"decision": "quarantined", "reason": "unknown_sender", "evidence": {"synthetic": True}}
    return {"decision": "accepted", "reason": "synthetic_bound_sender",
            "evidence": {"synthetic": True, "binding": row["source_ref"], "party": row["party_id"]}}


def deliver_email(desk, conn, raw: bytes, provider_id: str) -> dict:
    from hubzoid.channels import admission
    from hubzoid.channels.email import mime
    from hubzoid.channels.models import ExternalActor, InboundEvent, Origin, Verified
    from hubzoid.intake import service as intake

    stored = intake.files_for(conn.hub_dir).register_bytes(
        f"intake:{conn.id}", f"{hashlib.sha256(provider_id.encode()).hexdigest()[:16]}.eml", "message/rfc822", raw,
        origin="intake", check_type=False)
    source = stored["file_id"] if isinstance(stored, dict) else str(stored)
    parsed = mime.parse(raw)
    sender = parsed.from_addr or "unknown"
    trust = _trust(desk, conn, "email", sender)
    root = mime.thread_root(parsed) or f"<{provider_id}@hubzoid.local>"
    thread_key = "email:" + hashlib.sha256(f"{sender}\n{root}".encode()).hexdigest()[:32]
    origin = Origin("sample_email", conn.settings.address.lower(), thread_key,
                    {"reply_to": sender, "subject": parsed.subject, "root_message_id": root})
    key = parsed.message_id or parsed.raw_sha256
    ev = InboundEvent(
        semantic_key="email:" + hashlib.sha256(key.encode()).hexdigest(), kind="email", origin=origin,
        actor=ExternalActor(sender, parsed.from_name[:200], False, {"trust": trust["reason"]}),
        text=(f"Subject: {parsed.subject}\n\n" if parsed.subject else "") + parsed.text, trigger=True,
        message_id=parsed.message_id or provider_id, files=list(parsed.attachments),
        data={"trust": trust, "subject": parsed.subject, "message_id": parsed.message_id, "source_file_id": source,
              "provider_id": provider_id, "problems": parsed.problems,
              "provider_data": {"date": parsed.date, "synthetic": True}})
    verified = Verified(delivery_id=provider_id, workspace_id=conn.settings.address.lower(), method="synthetic_sample",
                        body=raw)
    out = admission.admit(conn, verified, [ev], raw=raw)
    return {"id": provider_id, "status": out.status, "duplicate": out.duplicate, "trust": trust["decision"]}


def deliver_whatsapp(desk, conn, message: dict) -> dict:
    from hubzoid.channels import admission
    from hubzoid.channels.models import ExternalActor, InboundEvent, Origin, Verified

    phone = "+" + "".join(ch for ch in str(message["from"]) if ch.isdigit())
    trust = _trust(desk, conn, "whatsapp", phone)
    digits = phone[1:]
    origin = Origin("sample_whatsapp", conn.settings.address, f"wa:sample:{digits}", {"to": digits})
    ev = InboundEvent(
        semantic_key=f"wa:{message['message_id']}", kind="message", origin=origin,
        actor=ExternalActor(phone, (message.get("profile_name") or "")[:200], False, {"synthetic": True}),
        text=message.get("text") or "", trigger=True, message_id=message["message_id"], files=[],
        data={"trust": trust, "subject": "", "timestamp": message.get("timestamp"),
              "provider_data": {"synthetic": True, "message_type": message.get("type", "text"),
                                "provider_timestamp": message.get("timestamp")}})
    body = json.dumps(message, sort_keys=True).encode()
    verified = Verified(delivery_id=message["message_id"], workspace_id=conn.settings.address,
                        method="synthetic_sample", body=body)
    out = admission.admit(conn, verified, [ev], raw=body)
    return {"id": message["message_id"], "status": out.status, "duplicate": out.duplicate, "trust": trust["decision"]}


def received_header(raw: bytes) -> str | None:
    import email

    msg = email.message_from_bytes(raw)
    date = msg.get("Date")
    if not date:
        return None
    return email.utils.parsedate_to_datetime(date).isoformat()
