"""Four reports over immutable normalized revisions. No model or ERP calls."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .records import number

KINDS = ("register", "orders", "invoices", "exceptions")


def instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamps require an explicit timezone")
    return dt


def in_period(value: str | None, start: str | None, end: str | None, tz: str = "UTC") -> bool:
    zone = ZoneInfo(tz)
    if not value:
        return False
    # A source date is a calendar date. A timestamp is converted explicitly.
    day = instant(value).astimezone(zone).date() if "T" in value else date.fromisoformat(value)
    return (not start or day >= date.fromisoformat(start)) and (not end or day < date.fromisoformat(end))


def safe_cell(value) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet apps can ignore leading whitespace/control characters.
    return "'" + text if text.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")) else text


def latest(records: list[dict]) -> list[dict]:
    selected = {}
    for row in records:
        old = selected.get(row["record_id"])
        if old is None or int(row["revision"]) > int(old["revision"]):
            selected[row["record_id"]] = row
    return list(selected.values())


def build(records: list[dict], kind: str, *, start: str | None = None, end: str | None = None,
          tz: str = "UTC", generated_at: str | None = None, party: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"report must be one of {', '.join(KINDS)}")
    ZoneInfo(tz)
    if start:
        date.fromisoformat(start)
    if end:
        date.fromisoformat(end)
    if start and end and start >= end:
        raise ValueError("period start must precede exclusive end")
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    generated = instant(generated_at)
    rows = [r for r in latest(records) if party is None or r.get("party") == party]
    filtered = [r for r in rows if (not start and not end) or in_period(r.get("document_date"), start, end, tz)]
    out = {"report": kind, "filters": {"start": start, "end_exclusive": end, "timezone": tz, "party": party},
           "generated_at": generated_at, "revisions": [], "excluded": [], "rows": [],
           "undated": [r["record_id"] for r in rows if not r.get("document_date")]}
    if kind == "register":
        out["rows"] = [{k: r.get(k) for k in ("record_id", "revision", "document_kind", "document_date", "party",
                           "reference", "currency", "stated_total", "stated_tax", "tax_basis", "status",
                           "rule_revision", "source_event_id")} | {"exceptions": ", ".join(r.get("exceptions") or []),
                           "evidence": f"desk_case:{r['record_id']}"} for r in filtered]
        out["revisions"] = [f"{r['record_id']}:{r['revision']}" for r in filtered]
        return out
    if kind == "exceptions":
        # Include undated exceptions even under date filters: they cannot be
        # assigned to an accepted period and must not disappear from review.
        selected = [r for r in rows if r.get("status") != "accepted" and
                    (r in filtered or not r.get("document_date"))]
        for r in selected:
            at = r.get("exception_created_at") or r.get("created_at")
            age = max(0, int((generated - instant(at)).total_seconds())) if at else None
            out["rows"].append({"record_id": r["record_id"], "revision": r["revision"], "owner": r.get("owner"),
                                "reasons": r.get("exceptions") or [r.get("reason") or r.get("status")],
                                "created_at": at, "age_seconds": age, "evidence": f"desk_case:{r['record_id']}"})
        return out
    expected_kind = "order" if kind == "orders" else "invoice"
    groups = {}
    converted = []
    for r in rows:
        if r not in filtered or r.get("status") != "accepted":
            out["excluded"].append(r["record_id"])
            continue
        if r.get("document_kind") != expected_kind:
            continue
        gross, tax = number(r.get("stated_total")), number(r.get("stated_tax"))
        currency = r.get("currency")
        if gross is None or tax is None or not currency:
            out["excluded"].append(r["record_id"])
            continue
        g = groups.setdefault(currency, {"count": 0, "net": Decimal(0), "tax": Decimal(0), "total": Decimal(0)})
        g["count"] += 1
        g["net"] += gross - tax
        g["tax"] += tax
        g["total"] += gross
        out["revisions"].append(f"{r['record_id']}:{r['revision']}")
        if kind == "orders":
            qtys = g.setdefault("quantities", {})
            for line in r.get("lines") or []:
                qty = number(line.get("quantity"))
                if qty is not None and line.get("unit"):
                    qtys[line["unit"]] = qtys.get(line["unit"], Decimal(0)) + qty
            for c in r.get("approved_conversions") or []:
                qty = number(r["lines"][c["line"]]["quantity"]) * Decimal(c["factor"])
                converted.append({"record_id": r["record_id"], "revision": r["revision"], "line": c["line"],
                                  "quantity": str(qty), "unit": c["to"]})
    out["totals_by_currency"] = {currency: {k: ({u: str(q) for u, q in v.items()} if isinstance(v, dict)
                                                    else str(v) if isinstance(v, Decimal) else v)
                                            for k, v in group.items()} for currency, group in groups.items()}
    out["converted_quantities"] = converted
    return out


def csv_text(report: dict) -> str:
    if report["report"] != "register":
        raise ValueError("Only the register has a CSV export. Grouped reports use structured JSON.")
    rows = report["rows"]
    columns = list(rows[0]) if rows else (["record_id", "revision", "document_date", "party", "currency",
                                         "stated_total", "status"] if report["report"] == "register" else ["record_id"])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return stream.getvalue()
