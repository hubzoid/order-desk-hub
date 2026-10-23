"""Generate the `distributor-v1` dataset, byte for byte reproducibly.

    python -m erp.datasets.generate            # writes erp/datasets/distributor-v1/
    python -m erp.datasets.generate --check    # regenerates in a temp folder and compares

Never regenerate with a model. The files are original synthetic data, labelled
synthetic everywhere, Apache-2.0. PDFs use ReportLab's invariant mode, the
spreadsheet's zip entries and document properties carry a fixed time, and the
images are drawn with Pillow's bundled font, so the same library versions give
the same bytes (recorded in the manifest).
"""
from __future__ import annotations

import os

# openpyxl serialises differently when lxml is installed. Pin its own writer so
# the spreadsheets are the same bytes on every machine. Set before any import.
os.environ["OPENPYXL_LXML"] = "False"

import argparse
import csv
import email.utils
import hashlib
import io
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from . import spec_distributor_v1 as S

NAME = "distributor-v1"
OUT = Path(__file__).resolve().parent / NAME
SCHEMA_VERSION = 1
BASE = datetime(2000, 1, 15, 8, 0, tzinfo=timezone.utc)


def money(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def jnum(v: Decimal):
    """Numbers as JSON numbers with exact decimal text (parsed back as Decimal)."""
    return _Raw(format(Decimal(v), "f"))


class _Raw(str):
    pass


def _dumps(value) -> str:
    def enc(v, indent=0):
        pad = "  " * indent
        if isinstance(v, _Raw):
            return str(v)
        if isinstance(v, dict):
            if not v:
                return "{}"
            items = [f'{pad}  {json.dumps(k)}: {enc(x, indent + 1)}' for k, x in v.items()]
            return "{\n" + ",\n".join(items) + f"\n{pad}}}"
        if isinstance(v, list):
            if not v:
                return "[]"
            items = [f"{pad}  {enc(x, indent + 1)}" for x in v]
            return "[\n" + ",\n".join(items) + f"\n{pad}]"
        return json.dumps(v, ensure_ascii=False)
    return enc(value) + "\n"


def item_index():
    return {code: (name, box, disabled, desc) for code, name, box, disabled, desc in S.ITEMS}


# -- ERP documents --------------------------------------------------------------------------------

def erp_docs() -> dict[str, list[dict]]:
    items = item_index()
    docs: dict[str, list[dict]] = {}
    docs["company"] = [{"doctype": "Company", "name": S.COMPANY, "company_name": S.COMPANY, "abbr": S.ABBR,
                        "default_currency": S.CURRENCY, "country": "United States", "modified": S.TS}]
    docs["warehouses"] = [
        {"doctype": "Warehouse", "name": S.MAIN, "warehouse_name": "SYN-MAIN", "company": S.COMPANY, "modified": S.TS},
        {"doctype": "Warehouse", "name": S.OVERFLOW, "warehouse_name": "SYN-OVERFLOW", "company": S.COMPANY,
         "modified": S.TS}]
    docs["uoms"] = [{"doctype": "UOM", "name": u, "uom_name": u, "must_be_whole_number": 1, "modified": S.TS}
                    for u in ("Nos", "Box")]
    docs["price_lists"] = [
        {"doctype": "Price List", "name": "SYN-RETAIL", "price_list_name": "SYN-RETAIL", "currency": S.CURRENCY,
         "selling": 1, "buying": 0, "enabled": 1, "modified": S.TS},
        {"doctype": "Price List", "name": "SYN-BUYING", "price_list_name": "SYN-BUYING", "currency": S.CURRENCY,
         "selling": 0, "buying": 1, "enabled": 1, "modified": S.TS}]
    docs["customers"] = [{"doctype": "Customer", "name": cid, "customer_name": name, "customer_type": "Company",
                          "customer_group": "Commercial", "territory": "All Territories", "disabled": 0,
                          "default_currency": S.CURRENCY, "default_price_list": "SYN-RETAIL", "modified": S.TS}
                         for cid, name, *_ in S.CUSTOMERS]
    docs["suppliers"] = [{"doctype": "Supplier", "name": sid, "supplier_name": name, "supplier_group": "All Supplier Groups",
                          "disabled": 0, "default_currency": S.CURRENCY, "modified": S.TS}
                         for sid, name, *_ in S.SUPPLIERS]
    docs["contacts"] = []
    for kind, rows in (("Customer", S.CUSTOMERS), ("Supplier", S.SUPPLIERS)):
        for pid, name, mail, phone, contact in rows:
            docs["contacts"].append({"doctype": "Contact", "name": contact, "first_name": f"{name} contact",
                                     "email_id": mail, "mobile_no": phone,
                                     "links": [{"link_doctype": kind, "link_name": pid}], "modified": S.TS})
    docs["items"] = []
    for code, (name, box, disabled, desc) in items.items():
        uoms = [{"name": f"{code}-UOM-1", "idx": 1, "uom": "Nos", "conversion_factor": jnum(1)}]
        if box:
            uoms.append({"name": f"{code}-UOM-2", "idx": 2, "uom": "Box", "conversion_factor": jnum(box)})
        docs["items"].append({"doctype": "Item", "name": code, "item_code": code, "item_name": name,
                              "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1, "disabled": disabled,
                              "description": desc, "uoms": uoms, "modified": S.TS})
    prices = []
    n = 0
    for code, rate in S.SELLING.items():
        n += 1
        prices.append({"doctype": "Item Price", "name": f"SYN-IP-{n:04d}", "item_code": code, "price_list": "SYN-RETAIL",
                       "price_list_rate": jnum(Decimal(rate)), "currency": S.CURRENCY, "uom": "Nos", "customer": None,
                       "supplier": None, "valid_from": "1999-01-01", "valid_upto": None, "selling": 1, "buying": 0,
                       "modified": S.TS})
    for code, rate in S.BUYING.items():
        n += 1
        prices.append({"doctype": "Item Price", "name": f"SYN-IP-{n:04d}", "item_code": code, "price_list": "SYN-BUYING",
                       "price_list_rate": jnum(Decimal(rate)), "currency": S.CURRENCY, "uom": "Nos", "customer": None,
                       "supplier": None, "valid_from": "1999-01-01", "valid_upto": None, "selling": 0, "buying": 1,
                       "modified": S.TS})
    for extra in S.EXTRA_PRICES:
        n += 1
        prices.append({"doctype": "Item Price", "name": f"SYN-IP-{n:04d}", "item_code": extra["item_code"],
                       "price_list": extra["price_list"], "price_list_rate": jnum(Decimal(extra["price_list_rate"])),
                       "currency": S.CURRENCY, "uom": "Nos", "customer": extra["customer"], "supplier": None,
                       "valid_from": extra["valid_from"], "valid_upto": extra["valid_upto"], "selling": 1, "buying": 0,
                       "note": extra["note"], "modified": S.TS})
    docs["item_prices"] = prices
    bins = []
    for code, (actual, reserved) in S.STOCK.items():
        bins.append({"doctype": "Bin", "name": f"BIN-{code}-MAIN", "item_code": code, "warehouse": S.MAIN,
                     "actual_qty": jnum(actual), "reserved_qty": jnum(reserved), "projected_qty": jnum(actual - reserved),
                     "modified": S.TS})
    for code, (actual, reserved) in S.OVERFLOW_STOCK.items():
        bins.append({"doctype": "Bin", "name": f"BIN-{code}-OVERFLOW", "item_code": code, "warehouse": S.OVERFLOW,
                     "actual_qty": jnum(actual), "reserved_qty": jnum(reserved), "projected_qty": jnum(actual - reserved),
                     "modified": S.TS})
    docs["bins"] = bins

    def lines_total(rows):
        total = sum((money(r["amount"]) for r in rows), Decimal("0"))
        return {"total_qty": jnum(sum((Decimal(str(r["qty"])) for r in rows), Decimal("0"))), "total": jnum(total),
                "net_total": jnum(total), "total_taxes_and_charges": jnum(Decimal("0.00")),
                "grand_total": jnum(total), "rounded_total": jnum(total)}

    sos = []
    for name, cust, po, date, delivery, docstatus, status, lines in S.SALES_ORDERS:
        rows, qty_all, delivered_all = [], Decimal(0), Decimal(0)
        for i, (code, qty, delivered) in enumerate(lines, 1):
            rate = Decimal(S.SELLING[code])
            rows.append({"name": f"{name}-{i}", "idx": i, "doctype": "Sales Order Item", "item_code": code,
                         "item_name": items[code][0], "qty": jnum(qty), "delivered_qty": jnum(delivered), "uom": "Nos",
                         "conversion_factor": jnum(1), "stock_qty": jnum(qty), "rate": jnum(rate),
                         "amount": jnum(money(rate * qty)), "delivery_date": delivery, "warehouse": S.MAIN})
            qty_all += qty
            delivered_all += delivered
        per_delivered = (delivered_all * 100 / qty_all).quantize(Decimal("0.01")) if docstatus == 1 else Decimal("0")
        billed = {"SAL-ORD-2000-00002": Decimal("75.86"), "SAL-ORD-2000-00003": Decimal("100")}.get(name, Decimal("0"))
        delivery_status = ("Fully Delivered" if per_delivered >= 100 else "Partly Delivered" if per_delivered > 0
                           else "Not Delivered") if docstatus == 1 else "Not Delivered"
        billing_status = ("Fully Billed" if billed >= 100 else "Partly Billed" if billed > 0 else "Not Billed")
        sos.append({"doctype": "Sales Order", "name": name, "company": S.COMPANY, "customer": cust,
                    "transaction_date": date, "delivery_date": delivery, "po_no": po, "currency": S.CURRENCY,
                    "selling_price_list": "SYN-RETAIL", "set_warehouse": S.MAIN, "docstatus": docstatus,
                    "status": status, "per_delivered": jnum(per_delivered), "per_billed": jnum(billed),
                    "delivery_status": delivery_status, "billing_status": billing_status, **lines_total(rows),
                    "custom_hubzoid_operation_id": None, "items": rows, "modified": S.TS})
    docs["sales_orders"] = sos

    sinvs = []
    for name, cust, po, so, posting, due, status, paid, lines in S.SALES_INVOICES:
        rows = []
        for i, (code, qty, so_row) in enumerate(lines, 1):
            rate = Decimal(S.SELLING[code])
            rows.append({"name": f"{name}-{i}", "idx": i, "doctype": "Sales Invoice Item", "item_code": code,
                         "item_name": items[code][0], "qty": jnum(qty), "uom": "Nos", "rate": jnum(rate),
                         "amount": jnum(money(rate * qty)), "sales_order": so,
                         "so_detail": f"{so}-{so_row}" if so and so_row else None})
        totals = lines_total(rows)
        sinvs.append({"doctype": "Sales Invoice", "name": name, "company": S.COMPANY, "customer": cust, "po_no": po,
                      "is_return": 0, "posting_date": posting, "due_date": due, "docstatus": 1, "status": status,
                      "currency": S.CURRENCY, "outstanding_amount": jnum(Decimal("0.00")) if paid else totals["grand_total"],
                      **totals, "items": rows, "modified": S.TS})
    docs["sales_invoices"] = sinvs

    pos = []
    for name, supp, date, schedule, docstatus, status, lines in S.PURCHASE_ORDERS:
        rows, qty_all, rec_all = [], Decimal(0), Decimal(0)
        for i, (row_id, code, qty, rate, received) in enumerate(lines, 1):
            rows.append({"name": row_id, "idx": i, "doctype": "Purchase Order Item", "item_code": code,
                         "item_name": items[code][0], "qty": jnum(qty), "received_qty": jnum(received), "uom": "Nos",
                         "conversion_factor": jnum(1), "stock_qty": jnum(qty), "rate": jnum(Decimal(rate)),
                         "amount": jnum(money(Decimal(rate) * qty)), "schedule_date": schedule, "warehouse": S.MAIN})
            qty_all += qty
            rec_all += received
        pos.append({"doctype": "Purchase Order", "name": name, "company": S.COMPANY, "supplier": supp,
                    "transaction_date": date, "schedule_date": schedule, "docstatus": docstatus, "status": status,
                    "currency": S.CURRENCY, "buying_price_list": "SYN-BUYING", "set_warehouse": S.MAIN,
                    "per_received": jnum((rec_all * 100 / qty_all).quantize(Decimal("0.01"))),
                    "per_billed": jnum(Decimal("0")), **lines_total(rows), "items": rows, "modified": S.TS})
    docs["purchase_orders"] = pos

    prs = []
    for name, supp, posting, lines in S.PURCHASE_RECEIPTS:
        rows = []
        for i, (row_id, po, po_row, code, qty, rate) in enumerate(lines, 1):
            rows.append({"name": row_id, "idx": i, "doctype": "Purchase Receipt Item", "item_code": code,
                         "item_name": items[code][0], "qty": jnum(qty), "received_qty": jnum(qty),
                         "rejected_qty": jnum(0), "uom": "Nos", "conversion_factor": jnum(1), "stock_qty": jnum(qty),
                         "rate": jnum(Decimal(rate)), "amount": jnum(money(Decimal(rate) * qty)),
                         "purchase_order": po, "purchase_order_item": po_row, "warehouse": S.MAIN})
        prs.append({"doctype": "Purchase Receipt", "name": name, "company": S.COMPANY, "supplier": supp,
                    "posting_date": posting, "docstatus": 1, "status": "To Bill", "currency": S.CURRENCY,
                    "set_warehouse": S.MAIN, **lines_total(rows), "items": rows, "modified": S.TS})
    docs["purchase_receipts"] = prs

    pis = []
    for name, supp, bill, bill_date, docstatus, status, lines in S.PURCHASE_INVOICES:
        rows = []
        for i, (code, qty, rate, po, po_row, pr, pr_row) in enumerate(lines, 1):
            rows.append({"name": f"{name}-{i}", "idx": i, "doctype": "Purchase Invoice Item", "item_code": code,
                         "item_name": items[code][0], "qty": jnum(qty), "uom": "Nos", "conversion_factor": jnum(1),
                         "stock_qty": jnum(qty), "rate": jnum(Decimal(rate)), "amount": jnum(money(Decimal(rate) * qty)),
                         "purchase_order": po, "po_detail": po_row, "purchase_receipt": pr, "pr_detail": pr_row,
                         "warehouse": S.MAIN})
        totals = lines_total(rows)
        due = (datetime.fromisoformat(bill_date) + timedelta(days=30)).date().isoformat()
        pis.append({"doctype": "Purchase Invoice", "name": name, "company": S.COMPANY, "supplier": supp,
                    "bill_no": bill, "bill_date": bill_date, "posting_date": bill_date, "due_date": due,
                    "docstatus": docstatus, "status": status, "currency": S.CURRENCY,
                    "buying_price_list": "SYN-BUYING", "update_stock": 0,
                    "outstanding_amount": totals["grand_total"] if docstatus == 1 else jnum(Decimal("0.00")),
                    **totals, "custom_hubzoid_operation_id": None, "items": rows, "modified": S.TS})
    docs["purchase_invoices"] = pis
    return docs


# -- attachments --------------------------------------------------------------------------------

def render_pdf(spec: dict) -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER, invariant=1, pageCompression=0)
    c.setTitle(f"SYNTHETIC SAMPLE {spec['title']} {spec['filename']}")
    c.setAuthor("Hubzoid synthetic data (distributor-v1)")
    width, height = LETTER
    y = height - 54
    c.setFont("Helvetica-Bold", 9)
    c.drawString(54, y, "SYNTHETIC SAMPLE DOCUMENT. Not a real order or invoice.")
    y -= 30
    c.setFont("Helvetica-Bold", 18)
    c.drawString(54, y, spec["title"])
    y -= 28
    c.setFont("Helvetica", 10)
    for label, value in spec["meta"]:
        c.drawString(54, y, f"{label}: {value}")
        y -= 15
    y -= 12
    cols = spec["columns"]
    xs = [54, 140, 330, 380, 430, 500][:len(cols)]
    c.setFont("Helvetica-Bold", 10)
    for x, col in zip(xs, cols):
        c.drawString(x, y, col)
    y -= 6
    c.line(54, y, width - 54, y)
    y -= 14
    c.setFont("Helvetica", 10)
    for row in spec["rows"]:
        for x, cell in zip(xs, row):
            c.drawString(x, y, str(cell))
        y -= 15
    y -= 6
    c.line(54, y, width - 54, y)
    y -= 16
    for label, value in spec.get("footer", []):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(380, y, label)
        c.drawString(500, y, value)
        y -= 15
    for note in spec.get("notes", []):
        y -= 10
        c.setFont("Helvetica", 9)
        words, line = note.split(), ""
        for w in words:
            if len(line) + len(w) > 95:
                c.drawString(54, y, line)
                y -= 12
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            c.drawString(54, y, line)
            y -= 12
    c.showPage()
    c.save()
    return buf.getvalue()


def render_invoice_print(inv: dict, customer_name: str) -> bytes:
    rows = [[r["item_code"], r["item_name"], str(r["qty"]), r["uom"], str(r["rate"]), str(r["amount"])]
            for r in inv["items"]]
    return render_pdf({"title": "SALES INVOICE", "filename": f"{inv['name']}.pdf",
                       "meta": [("Invoice", inv["name"]), ("Company", S.COMPANY), ("Customer", customer_name),
                                ("Customer PO", inv["po_no"] or ""), ("Posting date", inv["posting_date"]),
                                ("Due date", inv["due_date"]), ("Status", inv["status"])],
                       "columns": ["Item", "Description", "Qty", "Unit", "Rate", "Amount"], "rows": rows,
                       "footer": [("Grand total (USD)", str(inv["grand_total"])),
                                  ("Outstanding (USD)", str(inv["outstanding_amount"]))]})


def render_xlsx(spec: dict) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = spec["sheet"]
    ws.append(["SYNTHETIC SAMPLE DOCUMENT. Not a real order or invoice."])
    for label, value in spec["meta"]:
        ws.append([label, value])
    ws.append([])
    ws.append(spec["columns"])
    for row in spec["rows"]:
        ws.append(row)
    for label, value in spec.get("footer", []):
        ws.append([None] * (len(spec["columns"]) - 2) + [label, value])
    fixed = datetime(2000, 1, 15, 10, 0, 0)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    wb.properties.creator = "Hubzoid synthetic data (distributor-v1)"
    raw = io.BytesIO()
    wb.save(raw)
    return _fix_zip(raw.getvalue())


def _fix_zip(data: bytes) -> bytes:
    import re

    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename):
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = re.sub(rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:)", rb"\g<1>2000-01-15T10:00:00Z\g<3>",
                              body)
            zi = zipfile.ZipInfo(info.filename, date_time=(2000, 1, 15, 10, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            dst.writestr(zi, body)
    return out.getvalue()


def render_png(spec: dict) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=18)
    width, line_h = 900, 48
    img = Image.new("RGB", (width, 80 + line_h * len(spec["lines"])), (251, 247, 236))
    draw = ImageDraw.Draw(img)
    for i in range(len(spec["lines"]) + 2):
        y = 60 + i * line_h
        draw.line([(30, y), (width - 30, y)], fill=(214, 224, 238), width=1)
    draw.text((32, 14), "synthetic sample image (distributor-v1)", fill=(120, 120, 120), font=small)
    for i, text in enumerate(spec["lines"]):
        draw.text((40, 66 + i * line_h), text, fill=(30, 40, 90), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def corrupt_pdf() -> bytes:
    good = render_pdf({"title": "PURCHASE ORDER", "filename": "PC-0051.pdf",
                       "meta": [("PO number", "PC-0051")], "columns": ["Item"], "rows": [["SYN-ITEM-004"]]})
    return good[: len(good) // 3]   # truncated: a damaged upload


# -- messages -----------------------------------------------------------------------------------

def _party(pid: str):
    for rows in (S.CUSTOMERS, S.SUPPLIERS):
        for row in rows:
            if row[0] == pid:
                return row
    raise KeyError(pid)


def _received(minute: int) -> datetime:
    return BASE + timedelta(minutes=minute)


def build_eml(msg_spec: dict, attachment: tuple[str, str, bytes] | None, *, message_id: str) -> bytes:
    pid, name, mail, phone, _ = _party(msg_spec["party"])
    m = EmailMessage(policy=SMTP)
    m["From"] = email.utils.formataddr((msg_spec.get("sender_name") or f"{name} (synthetic)", mail))
    m["To"] = S.DESK_MAILBOX
    m["Subject"] = msg_spec["subject"]
    m["Date"] = email.utils.format_datetime(_received(msg_spec["minute"]))
    m["Message-ID"] = message_id
    m["X-Hubzoid-Synthetic"] = "distributor-v1"
    m.set_content(msg_spec["body"] + "\n")
    if attachment:
        filename, media, data = attachment
        maintype, subtype = media.split("/", 1)
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        m.set_boundary(f"=_synthetic_{msg_spec['id']}")
    return bytes(m)


MEDIA = {"pdf": "application/pdf", "corrupt_pdf": "application/pdf",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "png": "image/png"}


def envelope(hub_id: str, spec: dict, channel: str, *, text: str, attachments: list[dict], message_id: str,
             sender: tuple[str, str]) -> dict:
    thread = "email:" if channel == "email" else "whatsapp:"
    thread_key = thread + hashlib.sha256(f"{sender[0]}\n{message_id}".encode()).hexdigest()[:32]
    return {
        "version": 1, "hub_id": hub_id, "channel_id": "email-inbox" if channel == "email" else "whatsapp",
        "provider": channel, "provider_event_id": spec["id"], "provider_message_id": message_id,
        "thread_key": thread_key, "sender": {"external_id": sender[0], "display_name": sender[1]},
        "received_at": _received(spec["minute"]).isoformat().replace("+00:00", "Z"), "text": text,
        "attachments": attachments,
        "trust": {"decision": "pending", "reason": "derived_by_synthetic_import", "evidence_ref": None},
        "origin": {"channel_id": "email-inbox" if channel == "email" else "whatsapp", "thread_key": thread_key,
                   "message_id": message_id},
    }


def order_desk_messages(root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    main, extras, adversarial = [], [], []
    built: dict[str, dict] = {}
    for group, specs in (("main", S.ORDER_DESK), ("extras", S.ORDER_DESK_EXTRAS), ("adversarial", S.ORDER_DESK_ADVERSARIAL)):
        for spec in specs:
            if spec.get("replay_of"):
                original = built[spec["replay_of"]]
                env = dict(original, provider_event_id=spec["id"],
                           received_at=_received(spec["minute"]).isoformat().replace("+00:00", "Z"))
                (root / "raw" / f"{spec['id']}.eml").write_bytes((root / "raw" / f"{spec['replay_of']}.eml").read_bytes())
                adversarial.append(env)
                continue
            pid, name, mail, phone, _ = _party(spec["party"])
            attachment = None
            att_meta = []
            if spec.get("attachment"):
                a = spec["attachment"]
                data = {"pdf": lambda: render_pdf(a), "xlsx": lambda: render_xlsx(a), "png": lambda: render_png(a),
                        "corrupt_pdf": corrupt_pdf}[a["kind"]]()
                (root / "attachments" / a["filename"]).write_bytes(data)
                attachment = (a["filename"], MEDIA[a["kind"]], data)
                att_meta.append({"attachment_id": f"{spec['id']}-1", "filename": a["filename"],
                                 "media_type": MEDIA[a["kind"]], "bytes": len(data),
                                 "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "state": "available"})
            if spec["channel"] == "email":
                message_id = f"<{spec['id']}@synthetic-sample.example>"
                raw = build_eml(spec, attachment, message_id=message_id)
                (root / "raw" / f"{spec['id']}.eml").write_bytes(raw)
                sender = (mail, spec.get("sender_name") or f"{name} (synthetic)")
                text = (f"Subject: {spec['subject']}\n\n" if spec["subject"] else "") + spec["body"]
            else:
                message_id = f"wamid.SYNTHETIC-{spec['id']}"
                sender = (phone, f"{name} (synthetic)")
                text = spec["body"]
                raw = {"from": phone, "profile_name": sender[1], "message_id": message_id,
                       "timestamp": int(_received(spec["minute"]).timestamp()), "type": "text", "text": spec["body"]}
                (root / "raw" / f"{spec['id']}.whatsapp.json").write_text(json.dumps(raw, indent=2) + "\n")
            env = envelope("order-invoice-desk", spec, spec["channel"], text=text, attachments=att_meta,
                           message_id=message_id, sender=sender)
            built[spec["id"]] = env
            {"main": main, "extras": extras, "adversarial": adversarial}[group].append(env)
    return main, extras, adversarial


def customer_desk_messages(root: Path) -> tuple[list[dict], list[dict], dict]:
    main, adversarial, index = [], [], {}
    for n, row in enumerate(S.CUSTOMER_DESK):
        eid, channel, pid, subject, body, category = row
        spec = {"id": eid, "party": pid, "minute": 100 + n, "subject": subject, "body": body + " (synthetic sample)"}
        main.append(_cd_message(root, spec, channel))
        index[eid] = {"category": category, "party": pid, "channel": channel}
    for n, row in enumerate(S.CUSTOMER_DESK_ADVERSARIAL):
        eid, channel, pid, subject, body, category, unbound = row
        spec = {"id": eid, "party": pid, "minute": 140 + n, "subject": subject, "body": body + " (synthetic sample)",
                "unbound": unbound}
        adversarial.append(_cd_message(root, spec, channel))
        index[eid] = {"category": category, "party": pid, "channel": channel, "adversarial": True}
    return main, adversarial, index


def _cd_message(root: Path, spec: dict, channel: str) -> dict:
    if spec.get("unbound"):
        mail, name, phone = spec["unbound"], "Unknown sender (synthetic)", "+12025550199"
    else:
        _, pname, mail, phone, _ = _party(spec["party"])
        name = f"{pname} (synthetic)"
    if channel == "email":
        message_id = f"<{spec['id']}@synthetic-sample.example>"
        eml_spec = dict(spec, subject=spec["subject"] or "Message", sender_name=name)
        m = EmailMessage(policy=SMTP)
        m["From"] = email.utils.formataddr((name, mail))
        m["To"] = "service@synthetic-meridian.example"
        m["Subject"] = eml_spec["subject"]
        m["Date"] = email.utils.format_datetime(_received(spec["minute"]))
        m["Message-ID"] = message_id
        m["X-Hubzoid-Synthetic"] = "distributor-v1"
        m.set_content(spec["body"] + "\n")
        (root / "raw" / f"{spec['id']}.eml").write_bytes(bytes(m))
        text = f"Subject: {eml_spec['subject']}\n\n{spec['body']}"
        sender = (mail, name)
    else:
        message_id = f"wamid.SYNTHETIC-{spec['id']}"
        raw = {"from": phone, "profile_name": name, "message_id": message_id,
               "timestamp": int(_received(spec["minute"]).timestamp()), "type": "text", "text": spec["body"]}
        (root / "raw" / f"{spec['id']}.whatsapp.json").write_text(json.dumps(raw, indent=2) + "\n")
        text = spec["body"]
        sender = (phone, name)
    return envelope("customer-desk", spec, channel, text=text, attachments=[], message_id=message_id, sender=sender)


# -- everything -----------------------------------------------------------------------------------

def write(root: Path) -> dict:
    if root.exists():
        shutil.rmtree(root)
    for sub in ("erp", "attachments", "print", "raw", "messages", "expected"):
        (root / sub).mkdir(parents=True)
    docs = erp_docs()
    for name, rows in docs.items():
        (root / "erp" / f"{name}.json").write_text(_dumps(rows), encoding="utf-8")
    customers = {c[0]: c[1] for c in S.CUSTOMERS}
    for inv in docs["sales_invoices"]:
        (root / "print" / f"{inv['name']}.pdf").write_bytes(render_invoice_print(inv, customers[inv["customer"]]))
    od_main, od_extras, od_adv = order_desk_messages(root)
    cd_main, cd_adv, cd_index = customer_desk_messages(root)
    for fname, rows in (("order-invoice-desk.jsonl", od_main), ("order-invoice-desk-extras.jsonl", od_extras),
                        ("order-invoice-desk-adversarial.jsonl", od_adv), ("customer-desk.jsonl", cd_main),
                        ("customer-desk-adversarial.jsonl", cd_adv)):
        (root / "messages" / fname).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                                               encoding="utf-8")
    (root / "messages" / "customer-desk.index.json").write_text(json.dumps(cd_index, indent=2, sort_keys=True) + "\n")
    expected = {s["id"]: s["expect"] for s in S.ORDER_DESK + S.ORDER_DESK_EXTRAS + S.ORDER_DESK_ADVERSARIAL}
    (root / "expected" / "order-invoice-desk.json").write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    with (root / "bindings.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["company", "channel_kind", "sender", "party_type", "party_id", "source_ref"])
        for kind, rows in (("Customer", S.CUSTOMERS), ("Supplier", S.SUPPLIERS)):
            for pid, _, mail, phone, contact in rows:
                w.writerow([S.COMPANY, "email", mail, kind, pid, f"erp-contact:{contact}"])
                w.writerow([S.COMPANY, "whatsapp", phone, kind, pid, f"erp-contact:{contact}"])
    (root / "README.md").write_text(README, encoding="utf-8")
    files = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        files[str(path.relative_to(root))] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    import openpyxl
    import PIL
    import reportlab

    manifest = {"dataset": NAME, "schema_version": SCHEMA_VERSION, "seed": S.SEED, "clock": S.CLOCK,
                "synthetic": True, "licence": "Apache-2.0", "company": S.COMPANY, "currency": S.CURRENCY,
                "timezone": "UTC", "warehouses": [S.MAIN, S.OVERFLOW],
                "counts": {k: len(v) for k, v in docs.items()} | {
                    "order_invoice_desk_messages": len(od_main), "customer_desk_messages": len(cd_main),
                    "order_invoice_desk_adversarial": len(od_adv), "customer_desk_adversarial": len(cd_adv)},
                "generator": {"module": "erp.datasets.generate", "reportlab": reportlab.Version,
                              "openpyxl": openpyxl.__version__, "pillow": PIL.__version__},
                "files": files}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


README = """# distributor-v1: a synthetic distributor

**SYNTHETIC DATA.** Synthetic Meridian Distribution is a fictional company. Every
name starts with "Synthetic", every email address uses a reserved `.example`
domain, phone numbers are in the reserved 555-01XX range, and there are no tax
ids, bank details or real records. Money is USD and times are UTC. Licence:
Apache-2.0, original data, made by `erp/datasets/generate.py` (never by a model).

## What is here

| Folder | Contents |
|---|---|
| `erp/` | ERPNext-shaped documents for every backend: company, warehouses, UOMs, price lists, 4 customers, 3 suppliers, 12 items, 24 item prices, stock bins, 6 sales orders, 4 purchase orders, 3 purchase receipts, 4 sales invoices, 3 purchase invoices, contacts. Draft, submitted and cancelled states, a disabled item, missing stock, two similar SKUs, an expired price, a contract price, a partial receipt and conflicting bill references are planted on purpose. |
| `messages/` | Intake envelopes (Hubzoid `IntakeEnvelope` v1): the twelve Order & Invoice Desk messages, the carton correction extra, the twenty Customer Desk requests, and adversarial companions for both desks. `trust` is `pending`: the synthetic importer derives it from `bindings.csv`, a message never asserts it. |
| `raw/` | The same messages as provider bytes: RFC 822 email and WhatsApp-style JSON. |
| `attachments/` | Original PDF, spreadsheet and image fixtures, each labelled synthetic. One PDF is truncated on purpose. |
| `print/` | Deterministic print PDFs of the four submitted sales invoices (for invoice copies). |
| `expected/order-invoice-desk.json` | The Order & Invoice Desk oracle: ten drafts and two questions for the twelve, plus the extras and adversarial cases. Fixture metadata, never model input. The Customer Desk owns its own oracle (`messages/customer-desk.index.json` only names each request's planted category). |
| `bindings.csv` | Which sender address or phone belongs to which customer or supplier, for the synthetic importer. |
| `manifest.json` | Schema version, seed, fixed clock and a SHA-256 for every file. |

## The twelve Order & Invoice Desk messages

Five orders (email body, PDF, spreadsheet, WhatsApp text, photo of a list), five
supplier bills that match their purchase order and receipt (PDF, spreadsheet,
email body, PDF, photo), one order with an ambiguous item ("Synthetic Hex Bolt
M8" matches two items) and one bill for more than was received (50 billed, 40
received). These counts are a fixture oracle, not a quality claim.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="regenerate in a temporary folder and compare bytes")
    args = ap.parse_args(argv)
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = write(Path(tmp) / NAME)
            current = json.loads((OUT / "manifest.json").read_text())
            if fresh["files"] != current["files"]:
                changed = sorted(set(fresh["files"].items()) ^ set(current["files"].items()))
                print("dataset differs from a fresh generation:", *{c[0] for c in changed}, sep="\n  ")
                return 1
        print(f"{NAME}: {len(current['files'])} files match a fresh generation")
        return 0
    manifest = write(OUT)
    print(f"wrote {OUT} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
