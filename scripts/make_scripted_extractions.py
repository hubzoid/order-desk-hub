"""Write the scripted extractor outputs for the Order & Invoice Desk fixtures.

    python scripts/make_scripted_extractions.py   # writes evals/fixtures/extractions/<event>.json

Each file is the answer a correct extractor gives for one fixture: the values
written in the document, each citing the recorded element that holds it
(`evals/fixtures/elements/`). Scripted tests and evals replay them, so the
deterministic desk is tested without a model. Real-model runs are separate
and recorded as such.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELEMENTS = ROOT / "evals" / "fixtures" / "elements"
OUT = ROOT / "evals" / "fixtures" / "extractions"

# event -> (kind, party_name, bill_to, reference, document_date, delivery_date, due_date, po_refs,
#           lines [(find, code, description, qty, unit, price, amount, po)], total, tax, instructions)
SPEC = {
    "OID-001": ("customer_order", "Synthetic Harbor Foods", None, "HF-7010", "2000-01-14", "2000-01-21", None, [],
                [("24 x SYN-ITEM-001", "SYN-ITEM-001", "Synthetic Olive Oil 1L", "24", None, None, None, None),
                 ("12 x SYN-ITEM-003", "SYN-ITEM-003", "Synthetic Dish Soap", "12", None, None, None, None)],
                None, None, False),
    "OID-002": ("customer_order", "Synthetic Alpine Hardware", None, "AH-3315", "2000-01-14", "2000-01-24", None, [],
                [("AH-B840 |", "AH-B840", "Hex bolt M8 x 40 zinc", "150", "Nos", "0.42", "63.00", None),
                 ("AH-GLV-M |", "AH-GLV-M", "Nitrile gloves size M", "3", "Box", "8.50", "25.50", None)],
                "88.50", None, False),
    "OID-003": ("customer_order", "Synthetic Prairie Clinics", None, "PC-0050", "2000-01-14", "2000-01-26", None, [],
                [("SYN-ITEM-004 |", "SYN-ITEM-004", "Nitrile gloves M", "40", "Nos", None, None, None),
                 ("SYN-ITEM-005 |", "SYN-ITEM-005", "Copy paper A4", "5", "Box", None, None, None),
                 ("SYN-ITEM-006 |", "SYN-ITEM-006", "Ballpoint pens", "24", "Nos", None, None, None),
                 ("SYN-ITEM-003 |", "SYN-ITEM-003", "Dish soap 500ml", "6", "Nos", None, None, None)],
                None, None, False),
    "OID-004": ("customer_order", None, None, "CC-125", None, "2000-01-19", None, [],
                [("10 x SYN-ITEM-007", "SYN-ITEM-007", "espresso beans", "10", None, None, None, None),
                 ("2 box SYN-ITEM-008", "SYN-ITEM-008", "oat milk", "2", "box", None, None, None)],
                None, None, False),
    "OID-005": ("customer_order", "Synthetic Harbor Foods", None, "HF-7011", None, "2000-01-25", None, [],
                [("SYN-ITEM-002 x 2 box", "SYN-ITEM-002", None, "2", "box", None, None, None),
                 ("SYN-ITEM-007", "SYN-ITEM-007", None, "6", None, None, None, None),
                 ("SYN-ITEM-003", "SYN-ITEM-003", None, "12", None, None, None, None)],
                None, None, False),
    "OID-006": ("supplier_invoice", "Synthetic Northwind Paper Co", "Synthetic Meridian Distribution", "SNP-INV-0912",
                "2000-01-12", None, "2000-02-11", ["PUR-ORD-2000-00001"],
                [("SYN-ITEM-005 |", "SYN-ITEM-005", "Copy paper A4 ream", "60", "Nos", "3.70", "222.00", None)],
                "222.00", "0.00", False),
    "OID-007": ("supplier_invoice", "Synthetic Northwind Paper Co", "Synthetic Meridian Distribution", "SNP-INV-0913",
                "2000-01-12", None, "2000-02-11", ["PUR-ORD-2000-00001"],
                [("SYN-ITEM-006 |", "SYN-ITEM-006", "Ballpoint pens", "50", "Nos", "0.31", "15.50", None),
                 ("SYN-ITEM-002 |", "SYN-ITEM-002", "Paper towels", "20", "Nos", "1.10", "22.00", None)],
                "37.50", None, False),
    "OID-008": ("supplier_invoice", "Synthetic Summit Beverages", "Synthetic Meridian Distribution", "SSB-2002",
                "2000-01-11", None, "2000-02-10", ["PUR-ORD-2000-00002"],
                [("SYN-ITEM-007 Espresso", "SYN-ITEM-007", "Espresso beans 1kg", "200", "Nos", "12.75", "2550.00", None)],
                "2550.00", None, False),
    "OID-009": ("supplier_invoice", "Synthetic Summit Beverages", "Synthetic Meridian Distribution", "SSB-2003",
                "2000-01-11", None, "2000-02-10", ["PUR-ORD-2000-00002"],
                [("SYN-ITEM-008 |", "SYN-ITEM-008", "Oat milk 1L", "30", "Nos", "1.40", "42.00", None)],
                "42.00", "0.00", False),
    "OID-010": ("supplier_invoice", "Granite Cleaning Supply", "Synthetic Meridian Distribution", "GCS-3002",
                "2000-01-13", None, "2000-02-12", ["PUR-ORD-2000-00003"],
                [("SYN-ITEM-004", "SYN-ITEM-004", None, "250", None, "0.36", "90.00", None)],
                "90.00", None, False),
    "OID-011": ("customer_order", "Alpine Hardware", None, "AH-3316", "2000-01-15", "2000-01-27", None, [],
                [("80 x Synthetic Hex Bolt M8", None, "Synthetic Hex Bolt M8", "80", None, None, None, None),
                 ("10 x SYN-ITEM-006", "SYN-ITEM-006", "ballpoint pens", "10", None, None, None, None)],
                None, None, False),
    "OID-012": ("supplier_invoice", "Synthetic Granite Cleaning Supply", "Synthetic Meridian Distribution", "GCS-3003",
                "2000-01-13", None, "2000-02-12", ["PUR-ORD-2000-00003"],
                [("SYN-ITEM-012 |", "SYN-ITEM-012", "Floor cleaner 5L", "50", "Nos", "19.00", "950.00", None)],
                "950.00", "0.00", False),
    "OID-X01": ("customer_order", "Synthetic Harbor Foods", None, "HF-7012", "2000-01-15", "2000-01-28", None, [],
                [("2 cartons SYN-ITEM-001", "SYN-ITEM-001", "Synthetic Olive Oil 1L", "2", "cartons", None, None, None),
                 ("6 x SYN-ITEM-003", "SYN-ITEM-003", "Synthetic Dish Soap 500ml", "6", None, None, None, None)],
                None, None, False),
    "OID-A01": None,     # the same message as OID-001: same extraction
    "OID-A02": ("customer_order", None, None, "CC-125", None, "2000-01-19", None, [],
                [("10 x SYN-ITEM-007", "SYN-ITEM-007", "espresso beans", "10", None, None, None, None),
                 ("2 box SYN-ITEM-008", "SYN-ITEM-008", "oat milk", "2", "box", None, None, None)],
                None, None, False),
    "OID-A03": ("supplier_invoice", "Synthetic Northwind Paper Co", "Synthetic Meridian Distribution", "SNP-INV-0960",
                "2000-01-14", None, "2000-01-15", ["PUR-ORD-2000-00004"],
                [("SYN-ITEM-005 |", "SYN-ITEM-005", "Copy paper A4 ream", "60", "Nos", "3.70", "222.00", None)],
                "222.00", None, True),
    "OID-A04": ("supplier_invoice", "Synthetic Granite Cleaning Supply", "Synthetic Meridian Distribution", "GCS-3011",
                "2000-01-14", None, "2000-02-13", ["PUR-ORD-2000-00003"],
                [("SYN-ITEM-012 Floor", "SYN-ITEM-012", "Floor cleaner 5L", "40", "Nos", "19.00", "760.00", None)],
                "760.00", None, False),
    "OID-A05": ("supplier_invoice", "Synthetic Granite Cleaning Supply", "Synthetic Other Holdings", "GCS-3010",
                "2000-01-14", None, "2000-02-13", [],
                [("SYN-ITEM-012 Floor", "SYN-ITEM-012", "Floor cleaner 5L", "2", "Nos", "19.00", "38.00", None)],
                "38.00", None, False),
    "OID-A07": ("supplier_invoice", "Synthetic Northwind Paper Co", "Synthetic Meridian Distribution", "SNP-INV-0901",
                "2000-01-08", None, "2000-02-07", ["PUR-ORD-2000-00001"],
                [("SYN-ITEM-005 Copy", "SYN-ITEM-005", "Copy paper A4 ream", "40", "Nos", "3.70", "148.00", None)],
                "148.00", None, False),
}


def find(elements, needle, *, prefer_last=False):
    hits = [e for e in elements if needle.lower() in e["text"].lower().replace("×", "x")]
    if not hits:
        raise LookupError(f"no element holds {needle!r}")
    return hits[-1] if prefer_last else hits[0]


def cited(elements, value, needle=None, *, prefer_last=True):
    if value is None:
        return None
    return {"value": value, "source_ref": find(elements, needle or value, prefer_last=prefer_last)["id"]}


def build(event: str, spec, elements) -> dict:
    kind, party, bill_to, ref, ddate, deliver, due, pos, lines, total, tax, instructions = spec
    out = {
        "document_kind": kind, "party_name_seen": party,
        "bill_to_seen": cited(elements, bill_to, prefer_last=True) if bill_to else None,
        "reference": cited(elements, ref, prefer_last=True), "document_date": cited(elements, ddate, prefer_last=True),
        "delivery_date": cited(elements, deliver, prefer_last=True), "due_date": cited(elements, due, prefer_last=True),
        "purchase_order_refs": [cited(elements, p, prefer_last=True) for p in pos], "currency_seen": None,
        "lines": [], "stated_total": None, "stated_tax": None, "instructions_seen": instructions, "notes": ""}
    for needle, code, desc, qty, unit, price, amount, po in lines:
        el = find(elements, needle, prefer_last=True)
        out["lines"].append({"source_ref": el["id"], "raw_text": el["text"], "item_code_seen": code,
                             "description_seen": desc, "quantity": qty, "unit_seen": unit, "unit_price": price,
                             "amount": amount, "purchase_order_seen": po})
    if total:
        el = [e for e in elements if total in e["text"].replace(",", "") and e["text"].lower().count("total") or
              ("total" in e["text"].lower() and total in e["text"])]
        el = el[-1] if el else find(elements, total, prefer_last=True)
        out["stated_total"] = {"value": total, "source_ref": el["id"]}
    if tax:
        el = [e for e in elements if "tax" in e["text"].lower() and tax in e["text"]]
        if el:
            out["stated_tax"] = {"value": tax, "source_ref": el[-1]["id"]}
    if any(c in (ref or "") for c in "*"):
        raise ValueError(event)
    if event in ("OID-001", "OID-002", "OID-003", "OID-X01") or (kind == "supplier_invoice" and "USD" in
                                                                   " ".join(e["text"] for e in elements)):
        out["currency_seen"] = "USD" if any("usd" in e["text"].lower() for e in elements) else None
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for event, spec in SPEC.items():
        if spec is None:
            spec = SPEC["OID-001"]
        elements = json.loads((ELEMENTS / f"{event}.json").read_text())["elements"]
        data = build(event, spec, elements)
        (OUT / f"{event}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(event, "ok", len(data["lines"]), "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
