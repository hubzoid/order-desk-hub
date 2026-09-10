"""From a checked extraction to a proposal: deterministic matching and validation.

Every value in a proposal carries where it came from (`source`: the element
id, its location and text) and why it is what it is (`rule`: an exact item
code, an alias row, a policy rule id, an ERP record and its version). Anything
the rules do not decide becomes a typed question with its options. Money and
quantities are Decimal, ERP currency precision, never floats, and there is no
tolerance that lets a difference through.

Orders (from a bound customer) become a Sales Order payload. Supplier invoices
(from a bound supplier) become a Purchase Invoice payload matched to purchase
order and receipt rows, never billing more than was received and not yet
billed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from erp import digest as erp_digest
from erp.errors import ERPError
from erp.models import ERPContext

from .extract import Extraction, norm_text, parse_iso, to_number
from .policy import ALIASES, INVOICE_POLICY, ORDER_POLICY, Rules, norm

CENT = Decimal("0.01")
_CODE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,}")


def money(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def dstr(d: Decimal | None) -> str | None:
    if d is None:
        return None
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


@dataclass
class Question:
    code: str
    message: str
    field: str
    line: int | None = None
    options: list[dict] = field(default_factory=list)
    answer_type: str = "choice"
    source: dict | None = None
    rule: str | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "field": self.field, "line": self.line,
                "options": self.options, "answer_type": self.answer_type, "source": self.source, "rule": self.rule}


@dataclass
class Context:
    company: str
    currency: str
    warehouse: str
    selling_price_list: str
    buying_price_list: str
    party_type: str | None            # "Customer" | "Supplier" | None (unbound)
    party_id: str | None
    received_date: date
    rules: Rules
    reader: object
    erp_ctx: ERPContext
    overrides: dict = field(default_factory=dict)   # path -> value, from corrections


class _Reads:
    """Cached ERP reads for one proposal, recording each record's version."""

    def __init__(self, reader, ctx: ERPContext):
        self.reader, self.ctx = reader, ctx
        self.cache: dict[tuple, object] = {}
        self.versions: dict[str, str] = {}

    def get(self, resource: str, rid: str) -> dict | None:
        key = ("get", resource, rid)
        if key not in self.cache:
            try:
                rec = self.reader.get_record(resource, rid, self.ctx)
                self.versions[f"{resource}/{rid}"] = rec.get("erp_modified") or rec.get("modified_at") or ""
            except ERPError as exc:
                if exc.code != "not_found":
                    raise
                rec = None
            self.cache[key] = rec
        return self.cache[key]

    def all(self, resource: str, query: dict) -> list[dict]:
        key = ("all", resource, tuple(sorted(query.items())))
        if key not in self.cache:
            self.cache[key] = self.reader.all_records(resource, query, self.ctx)
        return self.cache[key]


def _source(elements_by_id: dict, ref: str | None) -> dict | None:
    if not ref or ref not in elements_by_id:
        return None
    e = elements_by_id[ref]
    return {"element": ref, "loc": e["loc"], "text": e["text"]}


# -- items, units and prices ------------------------------------------------------------------

def _item_candidates(reads: _Reads, description: str | None) -> list[dict]:
    """Suggestions only, for a person to choose from: items whose name holds
    every word of the description."""
    if not description:
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", norm_text(description)) if len(w) > 1]
    if not words:
        return []
    pool = reads.all("items", {"search": max(words, key=len)})
    out = []
    for rec in pool:
        name = norm_text(rec["fields"].get("item_name"))
        if all(w in re.findall(r"[a-z0-9]+", name) for w in words) and not rec["fields"].get("disabled"):
            out.append({"value": rec["id"], "label": f"{rec['id']} {rec['fields'].get('item_name')}"})
    return out[:10]


def match_item(ctx: Context, reads: _Reads, line, n: int, src: dict | None) -> tuple[dict | None, list[Question]]:
    party = ctx.party_id or ""
    override = ctx.overrides.get(f"lines[{n}].item_code")
    if override:
        rec = reads.get("items", override)
        if rec is None or rec["fields"].get("disabled"):
            return None, [Question("item_unknown", f"Line {n}: {override} is not an active item.", f"lines[{n}].item_code",
                                   n, answer_type="text", source=src)]
        return {"code": rec["id"], "name": rec["fields"]["item_name"], "record": rec,
                "rule": {"id": "correction", "text": "Chosen by a person for this case"}}, []
    tried = []
    tokens = [t for t in _CODE_TOKEN.findall(line.raw_text or "") if any(ch.isdigit() for ch in t)]
    for text in [line.item_code_seen] + tokens:
        if not text or text in tried:
            continue
        tried.append(text)
        rec = reads.get("items", text.strip())
        if rec is None and text.strip().upper() != text.strip():
            rec = reads.get("items", text.strip().upper())
        if rec is not None:
            if rec["fields"].get("disabled"):
                return None, [Question("item_disabled", f"Line {n}: item {rec['id']} is disabled in the ERP. Choose a "
                                       "replacement or remove the line.", f"lines[{n}].item_code", n,
                                       options=_item_candidates(reads, rec["fields"].get("item_name")),
                                       source=src, rule=f"{ORDER_POLICY}#O4")]
            return {"code": rec["id"], "name": rec["fields"]["item_name"], "record": rec,
                    "rule": {"id": "exact_item_code", "text": f"The document names ERP item {rec['id']} exactly"}}, []
    for text in (line.item_code_seen, line.description_seen):
        hits = ctx.rules.item_alias(ctx.company, party, text)
        codes = {a.item_code for a in hits}
        if len(codes) == 1:
            alias = sorted(hits, key=lambda a: 0 if a.party == party else 1)[0]
            rec = reads.get("items", alias.item_code)
            if rec is None or rec["fields"].get("disabled"):
                return None, [Question("item_unknown", f"Line {n}: alias row {alias.row} points at {alias.item_code}, "
                                       "which is not an active item.", f"lines[{n}].item_code", n, source=src,
                                       rule=alias.cite())]
            return {"code": rec["id"], "name": rec["fields"]["item_name"], "record": rec,
                    "rule": {"id": "alias", "text": f"{text} is {rec['id']} by alias row {alias.row} "
                             f"({alias.source_ref})", "cite": alias.cite()}}, []
    candidates = _item_candidates(reads, line.description_seen or line.item_code_seen or line.raw_text)
    if len(candidates) >= 2:
        return None, [Question("ambiguous_item", f"Line {n}: \"{line.description_seen or line.raw_text}\" matches "
                               f"{len(candidates)} items. Which one is it?", f"lines[{n}].item_code", n,
                               options=candidates, source=src, rule=f"{ORDER_POLICY}#O3")]
    return None, [Question("item_unknown", f"Line {n}: no ERP item matches \"{line.item_code_seen or line.description_seen or line.raw_text}\" "
                           "exactly. Choose the item, or add an alias row to knowledge/sku-aliases.csv.",
                           f"lines[{n}].item_code", n, options=candidates, source=src, rule=f"{ORDER_POLICY}#O3")]


def _singular(word: str) -> list[str]:
    w = norm(word)
    out = [w]
    if len(w) > 3 and w.endswith("es"):
        out.append(w[:-2])
    if len(w) > 3 and w.endswith("s"):
        out.append(w[:-1])
    return out


def match_unit(ctx: Context, item: dict, line, n: int, qty: Decimal, src) -> tuple[dict | None, list[Question]]:
    rec = item["record"]
    stock = rec["fields"]["stock_uom"]
    factors = {r["uom"]: Decimal(r["conversion_factor"]) for r in rec["rows"] or []}
    factors.setdefault(stock, Decimal(1))
    override = ctx.overrides.get(f"lines[{n}].uom")
    word = override or line.unit_seen
    if word and norm(word) in ("x", "*", "@"):
        word = None          # "24 x SYN-ITEM-001": a multiplication sign, not a unit
    if not word:
        return {"uom": stock, "qty": qty, "conversion_factor": Decimal(1), "source_qty": qty, "source_unit": None,
                "rule": {"id": "O5", "text": f"No unit is written, so the item's stock unit {stock}",
                         "cite": f"{ORDER_POLICY}#O5"}}, []
    # 1. a unit alias for this item (and party, or every party)
    for variant in _singular(word):
        hits = ctx.rules.unit_alias(ctx.company, ctx.party_id or "", item["code"], variant)
        if hits:
            a = hits[0]
            if a.target_uom not in factors:
                return None, [Question("unit_unknown", f"Line {n}: alias row {a.row} converts to {a.target_uom}, which "
                                       f"is not a unit of {item['code']}.", f"lines[{n}].uom", n, source=src,
                                       rule=a.cite())]
            converted = qty * a.factor
            return {"uom": a.target_uom, "qty": converted, "conversion_factor": factors[a.target_uom],
                    "source_qty": qty, "source_unit": word, "factor": a.factor,
                    "rule": {"id": "unit_alias", "text": f"{word} is {dstr(a.factor)} {a.target_uom} for {item['code']} "
                             f"by alias row {a.row} ({a.source_ref})", "cite": a.cite()}}, []
    # 2. a unit word that names one of the item's ERP units
    for variant in _singular(word):
        uom, cite = ctx.rules.unit_for(variant)
        if uom is None and variant in {norm(u) for u in factors}:
            uom = next(u for u in factors if norm(u) == variant)
            cite = "erp:item_uom"
        if uom is not None:
            if uom not in factors:
                return None, [Question("unit_unknown", f"Line {n}: {item['code']} has no {uom} unit in the ERP.",
                                       f"lines[{n}].uom", n, options=[{"value": u, "label": u} for u in factors],
                                       source=src, rule=cite)]
            return {"uom": uom, "qty": qty, "conversion_factor": factors[uom], "source_qty": qty, "source_unit": word,
                    "rule": {"id": "unit_word", "text": f"{word} is the ERP unit {uom} (1 {uom} = "
                             f"{dstr(factors[uom])} {stock} in the item master)", "cite": cite}}, []
    forms = _singular(word)
    one = forms[1] if len(forms) > 1 else forms[0]
    return None, [Question("unit_unknown", f"Line {n}: the unit \"{word}\" is not a unit of {item['code']} and no rule "
                           f"converts it. How many {stock} are in one {one}? Answer for this case, or add a unit "
                           "alias row to knowledge/sku-aliases.csv for every future order.", f"lines[{n}].uom", n,
                           options=[{"value": u, "label": u} for u in factors], answer_type="choice", source=src,
                           rule=f"{ORDER_POLICY}#O5")]


def selling_rate(ctx: Context, reads: _Reads, item: dict, unit: dict, on: date, n: int, src) -> tuple[dict | None, list]:
    prices = reads.all("prices", {"item_code": item["code"], "price_list": ctx.selling_price_list})
    valid = []
    for p in prices:
        f = p["fields"]
        start, end = parse_iso(f.get("valid_from")), parse_iso(f.get("valid_upto"))
        if (start and on < start) or (end and on > end) or f.get("supplier"):
            continue
        if f.get("customer") not in (None, "", ctx.party_id):
            continue
        valid.append(p)
    own = [p for p in valid if p["fields"].get("customer") == ctx.party_id]
    pick = own or [p for p in valid if not p["fields"].get("customer")]
    if not pick:
        return None, [Question("price_missing", f"Line {n}: {item['code']} has no valid price in {ctx.selling_price_list} "
                               f"on {on.isoformat()}. Add one in the ERP, or give the rate.", f"lines[{n}].rate", n,
                               answer_type="decimal", source=src, rule=f"{ORDER_POLICY}#O6")]
    if len(pick) > 1:
        return None, [Question("price_ambiguous", f"Line {n}: {item['code']} has {len(pick)} valid prices. Which one "
                               "applies?", f"lines[{n}].rate", n,
                               options=[{"value": p["fields"]["price_list_rate"], "label": f"{p['id']}: "
                                         f"{p['fields']['price_list_rate']}"} for p in pick],
                               source=src, rule=f"{ORDER_POLICY}#O6")]
    p = pick[0]
    base = Decimal(p["fields"]["price_list_rate"])
    price_uom = p["fields"].get("uom") or item["record"]["fields"]["stock_uom"]
    if price_uom == unit["uom"]:
        rate = base
    elif price_uom == item["record"]["fields"]["stock_uom"]:
        rate = base * unit["conversion_factor"]
    else:
        return None, [Question("price_missing", f"Line {n}: the price {p['id']} is per {price_uom}, which this line "
                               "cannot convert.", f"lines[{n}].rate", n, answer_type="decimal", source=src)]
    kind = "customer contract price" if p["fields"].get("customer") else "price list"
    return {"rate": money(rate), "record": p["id"], "rule": {"id": "O6", "text":
            f"{ctx.selling_price_list} {kind} {p['id']}: {dstr(base)} per {price_uom}"
            + (f" x {dstr(unit['conversion_factor'])}" if rate != base else ""), "cite": f"{ORDER_POLICY}#O6"}}, []


# -- the proposal ---------------------------------------------------------------------------------

@dataclass
class Proposal:
    kind: str | None
    payload: dict | None
    header: dict
    lines: list[dict]
    questions: list[dict]
    warnings: list[dict]
    totals: dict
    duplicate: dict | None
    erp_versions: dict
    content_key: dict | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload, "header": self.header, "lines": self.lines,
                "questions": self.questions, "warnings": self.warnings, "totals": self.totals,
                "duplicate": self.duplicate, "erp_versions": self.erp_versions, "content_key": self.content_key,
                "digest": erp_digest.sha(self.payload) if self.payload else None}


INJECTION = re.compile(r"(?i)\b(ignore (all|your|previous)|system notice|assistant|pre-?approved|submit (it|this|every)|"
                       r"approve (it|this)|bank (account|details)|wire (the|to)|change (our|the) (bank|account))\b")


def build(ctx: Context, extraction: Extraction, problems: list[dict], elements: list[dict]) -> Proposal:
    by_id = {e["id"]: e for e in elements}
    reads = _Reads(ctx.reader, ctx.erp_ctx)
    questions: list[Question] = []
    warnings: list[dict] = []
    header: dict = {}
    ov = ctx.overrides

    # Hostile or odd text is data: say so, act on nothing.
    flagged = [e["id"] for e in elements if INJECTION.search(e["text"] or "")]
    if extraction.instructions_seen or flagged:
        warnings.append({"code": "instructions_ignored", "message": "This document contains text addressed to an "
                         "assistant or asking to approve, submit or change payment details. It was treated as data "
                         "and ignored.", "elements": flagged})
    for p in problems:
        warnings.append({"code": "extraction_dropped", "message": "A value the extractor gave was not found in the "
                         f"element it cited, so it was dropped ({p.get('field')}).", "detail": p})

    # Which document is this? The bound sender decides, the model only agrees.
    expected = {"Customer": "customer_order", "Supplier": "supplier_invoice"}.get(ctx.party_type or "")
    if ctx.party_type is None:
        questions.append(Question("party_unknown", "This sender is not bound to a customer or supplier. Who sent it? "
                                  "The name in a message is never proof.", "party", answer_type="text",
                                  rule=f"{ORDER_POLICY}#O1"))
    if extraction.document_kind == "credit_note":
        questions.append(Question("not_supported", "This looks like a credit note or a return. The desk drafts orders "
                                  "and supplier invoices only. Handle it in the ERP.", "kind", answer_type="text",
                                  rule=f"{INVOICE_POLICY}#I8"))
    elif expected and extraction.document_kind not in (expected,) and not ov.get("kind"):
        questions.append(Question("kind_mismatch", f"The sender is a {ctx.party_type.lower()} but the document reads as "
                                  f"{extraction.document_kind.replace('_', ' ')}. Is it an order or an invoice?", "kind",
                                  options=[{"value": "customer_order", "label": "Customer order"},
                                           {"value": "supplier_invoice", "label": "Supplier invoice"}],
                                  rule=f"{ORDER_POLICY}#O1"))
    kind = {"customer_order": "sales_order", "supplier_invoice": "purchase_invoice"}.get(
        ov.get("kind") or expected or extraction.document_kind)
    if extraction.currency_seen:
        cur = norm_text(extraction.currency_seen)
        if cur not in (ctx.currency.lower(), "$", "us$", "usd", "us dollars", "dollars"):
            questions.append(Question("currency_mismatch", f"The document is in {extraction.currency_seen}, but this desk "
                                      f"books {ctx.currency} only and never converts.", "currency", answer_type="text",
                                      rule=f"{ORDER_POLICY}#O7"))

    # Reference and dates.
    ref = ov.get("reference") or (extraction.reference.value.strip() if extraction.reference else None)
    header["reference"] = {"value": ref, "source": _source(by_id, extraction.reference.source_ref)
                           if extraction.reference and not ov.get("reference") else None,
                           "rule": "correction" if ov.get("reference") else None}
    if not ref:
        questions.append(Question("missing_reference", "The " + ("customer's purchase order number" if kind == "sales_order"
                                  else "supplier's invoice number") + " was not found. What is it?", "reference",
                                  answer_type="text", rule=f"{ORDER_POLICY}#O2" if kind == "sales_order"
                                  else f"{INVOICE_POLICY}#I2"))

    def cited_date(name: str, cited) -> tuple[date | None, dict | None]:
        if ov.get(name):
            return parse_iso(ov[name]), {"rule": "correction"}
        if cited is None:
            return None, None
        return parse_iso(cited.value), _source(by_id, cited.source_ref)

    party_rec = None
    if ctx.party_id:
        party_rec = reads.get("customers" if ctx.party_type == "Customer" else "suppliers", ctx.party_id)
        if party_rec is None or party_rec["fields"].get("disabled"):
            questions.append(Question("party_disabled", f"{ctx.party_id} is not an active {ctx.party_type} in the ERP.",
                                      "party", answer_type="text"))
    header["party"] = {"type": ctx.party_type, "id": ctx.party_id,
                       "name": party_rec["fields"].get(f"{(ctx.party_type or '').lower()}_name") if party_rec else None,
                       "name_seen": extraction.party_name_seen}

    lines_out: list[dict] = []
    payload_lines: list[dict] = []
    computed = Decimal(0)

    if kind == "sales_order":
        on, src = cited_date("transaction_date", extraction.document_date)
        if on is None:
            on, src = ctx.received_date, {"rule": "O8: no order date is written, so the date it was received"}
        header["transaction_date"] = {"value": on.isoformat(), "source": src}
        deliver, dsrc = cited_date("delivery_date", extraction.delivery_date)
        header["delivery_date"] = {"value": deliver.isoformat() if deliver else None, "source": dsrc}
        if deliver is None:
            questions.append(Question("missing_delivery_date", "No delivery date was found. When should it be "
                                      "delivered?", "delivery_date", answer_type="date", rule=f"{ORDER_POLICY}#O9"))
        elif deliver < on:
            questions.append(Question("delivery_before_order", f"The delivery date {deliver} is before the order date "
                                      f"{on}.", "delivery_date", answer_type="date", rule=f"{ORDER_POLICY}#O9"))
        header["warehouse"] = {"value": ctx.warehouse, "rule": "connectors/erpnext.yaml warehouses[0]"}
        header["price_list"] = {"value": ctx.selling_price_list, "rule": "connectors/erpnext.yaml selling_price_list"}
        for n, line in enumerate(extraction.lines, 1):
            src_line = _source(by_id, line.source_ref)
            out = {"n": n, "source": src_line, "raw_text": line.raw_text, "questions": []}
            qty = to_number(ov.get(f"lines[{n}].qty")) if ov.get(f"lines[{n}].qty") else to_number(line.quantity)
            if qty is None or qty <= 0:
                q = Question("quantity_missing", f"Line {n}: no quantity was found in \"{line.raw_text}\".",
                             f"lines[{n}].qty", n, answer_type="decimal", source=src_line)
                questions.append(q)
                lines_out.append(out)
                continue
            item, qs = match_item(ctx, reads, line, n, src_line)
            questions += qs
            if item is None:
                lines_out.append(out)
                continue
            out["item"] = {k: v for k, v in item.items() if k != "record"}
            unit, qs = match_unit(ctx, item, line, n, qty, src_line)
            questions += qs
            if unit is None:
                lines_out.append(out)
                continue
            if unit["qty"] != unit["qty"].to_integral_value():
                questions.append(Question("quantity_not_whole", f"Line {n}: {dstr(unit['qty'])} {unit['uom']} is not a "
                                          "whole number.", f"lines[{n}].qty", n, answer_type="decimal",
                                          source=src_line))
            out["unit"] = {k: dstr(v) if isinstance(v, Decimal) else v for k, v in unit.items()}
            rate, qs = selling_rate(ctx, reads, item, unit, on, n, src_line)
            questions += qs
            if ov.get(f"lines[{n}].rate") and rate is not None:
                rate = {"rate": money(to_number(ov[f"lines[{n}].rate"])), "record": None,
                        "rule": {"id": "correction", "text": "Rate chosen by a person for this case"}}
            if rate is None:
                lines_out.append(out)
                continue
            stated = to_number(line.unit_price)
            if stated is not None and money(stated) != rate["rate"] and not ov.get(f"lines[{n}].rate"):
                questions.append(Question("price_difference", f"Line {n}: the order says {dstr(stated)} per "
                                          f"{unit['uom']} but the ERP price is {dstr(rate['rate'])}. Which applies?",
                                          f"lines[{n}].rate", n,
                                          options=[{"value": dstr(rate["rate"]), "label": f"ERP price {dstr(rate['rate'])}"},
                                                   {"value": dstr(money(stated)), "label": f"The order's {dstr(stated)}"}],
                                          source=src_line, rule=f"{ORDER_POLICY}#O6"))
            amount = money(unit["qty"] * rate["rate"])
            stated_amount = to_number(line.amount)
            if stated_amount is not None and money(stated_amount) != amount:
                questions.append(Question("amount_mismatch", f"Line {n}: the amount {dstr(stated_amount)} is not "
                                          f"{dstr(unit['qty'])} x {dstr(rate['rate'])} = {dstr(amount)}.",
                                          f"lines[{n}].qty", n, answer_type="decimal", source=src_line,
                                          rule=f"{ORDER_POLICY}#O10"))
            out["rate"] = {"value": dstr(rate["rate"]), "record": rate["record"], "rule": rate["rule"],
                           "stated": dstr(stated)}
            out["amount"] = dstr(amount)
            computed += amount
            payload_lines.append({"item_code": item["code"], "qty": dstr(unit["qty"]), "uom": unit["uom"],
                                  "rate": dstr(rate["rate"]), "source_ref": line.source_ref})
            lines_out.append(out)
        if not extraction.lines:
            questions.append(Question("no_lines", "No order lines were found. What should be ordered?", "lines",
                                      answer_type="text"))
    elif kind == "purchase_invoice":
        _invoice(ctx, reads, extraction, by_id, header, lines_out, payload_lines, questions, cited_date)
        computed = sum((Decimal(l["amount"]) for l in lines_out if l.get("amount")), Decimal(0))

    # Totals.
    stated_total = to_number(extraction.stated_total.value) if extraction.stated_total else None
    totals = {"computed": dstr(money(computed)), "stated": dstr(stated_total),
              "source": _source(by_id, extraction.stated_total.source_ref) if extraction.stated_total else None}
    if stated_total is not None and money(stated_total) != money(computed) and payload_lines and \
            len(payload_lines) == len(extraction.lines):
        questions.append(Question("total_mismatch", f"The document's total {dstr(stated_total)} is not the sum of its "
                                  f"lines {dstr(money(computed))}.", "total", answer_type="text",
                                  source=totals["source"], rule=f"{ORDER_POLICY}#O10" if kind == "sales_order"
                                  else f"{INVOICE_POLICY}#I6"))

    # Duplicates in the ERP (the desk's own keys are checked by the case service).
    duplicate = None
    if ref and ctx.party_id and kind:
        resource, party_field, ref_field = (("sales_orders", "customer", "po_no") if kind == "sales_order"
                                            else ("purchase_invoices", "supplier", "bill_no"))
        rows = reads.all(resource, {party_field: ctx.party_id, ref_field: ref})
        live = [r for r in rows if r["fields"].get("docstatus") != 2
                and erp_digest.normalize_reference(r["fields"].get(ref_field)) == erp_digest.normalize_reference(ref)]
        if live:
            duplicate = {"erp_record": live[0]["id"], "resource": resource, "reference": ref,
                         "docstatus": live[0]["fields"].get("docstatus"),
                         "message": f"{live[0]['id']} already has {ref_field} {ref} for {ctx.party_id}."}

    payload = None
    blocking = [q for q in questions]
    if kind and not blocking and payload_lines and ctx.party_id and ref and duplicate is None:
        if kind == "sales_order":
            payload = {"company": ctx.company, "customer": ctx.party_id, "po_no": ref,
                       "transaction_date": header["transaction_date"]["value"],
                       "delivery_date": header["delivery_date"]["value"], "currency": ctx.currency,
                       "selling_price_list": ctx.selling_price_list, "set_warehouse": ctx.warehouse,
                       "items": payload_lines}
        else:
            payload = {"company": ctx.company, "supplier": ctx.party_id, "bill_no": ref,
                       "bill_date": header["bill_date"]["value"], "posting_date": header["posting_date"]["value"],
                       "due_date": header["due_date"]["value"], "currency": ctx.currency,
                       "buying_price_list": ctx.buying_price_list, "set_warehouse": ctx.warehouse,
                       "items": payload_lines}
    content_key = None
    if kind and ctx.party_id and ref:
        content_key = {"kind": kind, "party": ctx.party_id, "reference": erp_digest.normalize_reference(ref),
                       "lines": [[l["item_code"], l["qty"], l["uom"]] for l in payload_lines]
                       or [[norm_text(l.raw_text)] for l in extraction.lines]}
    return Proposal(kind=kind, payload=payload, header=header, lines=lines_out,
                    questions=[q.to_dict() for q in questions], warnings=warnings, totals=totals,
                    duplicate=duplicate, erp_versions=dict(reads.versions), content_key=content_key)


def _invoice(ctx: Context, reads: _Reads, extraction: Extraction, by_id, header, lines_out, payload_lines, questions,
             cited_date):
    ov = ctx.overrides
    bill_date, bsrc = cited_date("bill_date", extraction.document_date)
    header["bill_date"] = {"value": bill_date.isoformat() if bill_date else None, "source": bsrc}
    header["posting_date"] = {"value": bill_date.isoformat() if bill_date else None,
                              "rule": "I3: the posting date is the bill date"}
    due, dsrc = cited_date("due_date", extraction.due_date)
    header["due_date"] = {"value": due.isoformat() if due else None, "source": dsrc}
    header["warehouse"] = {"value": ctx.warehouse, "rule": "connectors/erpnext.yaml warehouses[0]"}
    if bill_date is None:
        questions.append(Question("missing_bill_date", "No invoice date was found. What is it?", "bill_date",
                                  answer_type="date", rule=f"{INVOICE_POLICY}#I2"))
    if due is None:
        questions.append(Question("missing_due_date", "No due date was found. What is it?", "due_date",
                                  answer_type="date", rule=f"{INVOICE_POLICY}#I2"))
    elif bill_date and due < bill_date:
        questions.append(Question("due_before_bill", f"The due date {due} is before the invoice date {bill_date}.",
                                  "due_date", answer_type="date", rule=f"{INVOICE_POLICY}#I2"))
    billed_to = extraction.bill_to_seen
    header["bill_to"] = {"value": billed_to.value if billed_to else None,
                         "source": _source(by_id, billed_to.source_ref) if billed_to else None}
    if not ov.get("bill_to_confirmed"):
        if billed_to is None:
            questions.append(Question("bill_to_missing", f"The invoice does not say it is billed to {ctx.company}. Is "
                                      "it ours?", "bill_to_confirmed", options=[{"value": "yes", "label": "Yes, ours"}],
                                      rule=f"{INVOICE_POLICY}#I1"))
        elif norm(ctx.company) not in norm(billed_to.value):
            questions.append(Question("wrong_company", f"The invoice is billed to \"{billed_to.value}\", not "
                                      f"{ctx.company}. It is never booked here.", "bill_to_confirmed",
                                      answer_type="text", source=_source(by_id, billed_to.source_ref),
                                      rule=f"{INVOICE_POLICY}#I1"))
    if extraction.stated_tax:
        tax = to_number(extraction.stated_tax.value)
        if tax:
            questions.append(Question("tax_present", f"The invoice shows tax of {dstr(tax)}. This desk sends no tax "
                                      "lines: ERPNext applies its own tax template. Check the template before going "
                                      "on.", "tax", answer_type="text", source=_source(by_id, extraction.stated_tax.source_ref),
                                      rule=f"{INVOICE_POLICY}#I6"))
    po_ids = []
    for c in extraction.purchase_order_refs:
        if c.value.strip() not in po_ids:
            po_ids.append(c.value.strip())
    for line in extraction.lines:
        if line.purchase_order_seen and line.purchase_order_seen.strip() not in po_ids:
            po_ids.append(line.purchase_order_seen.strip())
    if ov.get("purchase_order"):
        po_ids = [ov["purchase_order"]]
    pos = []
    for pid in po_ids:
        po = reads.get("purchase_orders", pid)
        if po is None or po["fields"].get("docstatus") != 1:
            questions.append(Question("purchase_order_unknown", f"{pid} is not a submitted purchase order of "
                                      f"{ctx.party_id}.", "purchase_order", answer_type="text", rule=f"{INVOICE_POLICY}#I4"))
        else:
            pos.append(po)
    header["purchase_orders"] = [p["id"] for p in pos]
    if not po_ids:
        questions.append(Question("missing_purchase_order", "The invoice cites no purchase order. Which order does it "
                                  "bill?", "purchase_order", answer_type="text", rule=f"{INVOICE_POLICY}#I4"))
    receipts = None
    used: dict[str, Decimal] = {}

    def receipt_rows(po_row: str) -> list[dict]:
        nonlocal receipts
        if receipts is None:
            receipts = []
            for rec in reads.all("purchase_receipts", {"supplier": ctx.party_id, "docstatus": 1}):
                full = reads.get("purchase_receipts", rec["id"])
                if full:
                    receipts.append(full)
        out = []
        for pr in receipts:
            for row in pr["rows"] or []:
                if row.get("purchase_order_item") == po_row:
                    out.append({"receipt": pr["id"], **row})
        return out

    def billed(pr_row: str) -> Decimal:
        total = Decimal(0)
        for rec in reads.all("purchase_invoices", {"billed_receipt_row": pr_row}):
            full = reads.get("purchase_invoices", rec["id"])
            if full is None or full["fields"].get("docstatus") == 2:
                continue
            for row in full["rows"] or []:
                if row.get("pr_detail") == pr_row:
                    total += Decimal(row["qty"])
        return total

    for n, line in enumerate(extraction.lines, 1):
        src = _source(by_id, line.source_ref)
        out = {"n": n, "source": src, "raw_text": line.raw_text}
        qty = to_number(ov.get(f"lines[{n}].qty")) if ov.get(f"lines[{n}].qty") else to_number(line.quantity)
        if qty is None or qty <= 0:
            questions.append(Question("quantity_missing", f"Line {n}: no quantity was found.", f"lines[{n}].qty", n,
                                      answer_type="decimal", source=src))
            lines_out.append(out)
            continue
        item, qs = match_item(ctx, reads, line, n, src)
        questions.extend(qs)
        if item is None:
            lines_out.append(out)
            continue
        out["item"] = {k: v for k, v in item.items() if k != "record"}
        unit, qs = match_unit(ctx, item, line, n, qty, src)
        questions.extend(qs)
        if unit is None:
            lines_out.append(out)
            continue
        out["unit"] = {k: dstr(v) if isinstance(v, Decimal) else v for k, v in unit.items()}
        rows = [(po, r) for po in pos for r in (po["rows"] or []) if r.get("item_code") == item["code"]]
        if ov.get(f"lines[{n}].po_detail"):
            rows = [(po, r) for po, r in rows if r["row_id"] == ov[f"lines[{n}].po_detail"]]
        if not rows:
            if pos:
                questions.append(Question("po_line_missing", f"Line {n}: {item['code']} is not on "
                                          f"{', '.join(p['id'] for p in pos)}.", f"lines[{n}].po_detail", n,
                                          answer_type="text", source=src, rule=f"{INVOICE_POLICY}#I4"))
            lines_out.append(out)
            continue
        if len(rows) > 1:
            questions.append(Question("po_line_ambiguous", f"Line {n}: {item['code']} appears on more than one order "
                                      "row. Which one does this bill?", f"lines[{n}].po_detail", n,
                                      options=[{"value": r["row_id"], "label": f"{po['id']} row {r['idx']}: {r['qty']} "
                                                f"{r['uom']} at {r['rate']}"} for po, r in rows],
                                      source=src, rule=f"{INVOICE_POLICY}#I4"))
            lines_out.append(out)
            continue
        po, po_row = rows[0]
        po_rate = Decimal(po_row["rate"])
        rate = to_number(line.unit_price)
        if ov.get(f"lines[{n}].rate"):
            rate = to_number(ov[f"lines[{n}].rate"])
        if rate is None:
            rate = po_rate
            rate_rule = {"id": "I5", "text": f"No rate is written, so the order's {dstr(po_rate)}"}
        elif money(rate) != money(po_rate) and not ov.get(f"lines[{n}].rate"):
            questions.append(Question("price_difference", f"Line {n}: the invoice says {dstr(rate)} but "
                                      f"{po['id']} ordered at {dstr(po_rate)}.", f"lines[{n}].rate", n,
                                      options=[{"value": dstr(po_rate), "label": f"Order price {dstr(po_rate)}"},
                                               {"value": dstr(rate), "label": f"Invoice price {dstr(rate)}"}],
                                      source=src, rule=f"{INVOICE_POLICY}#I5"))
            lines_out.append(out)
            continue
        else:
            rate_rule = {"id": "I5", "text": f"Matches {po['id']} row {po_row['idx']} at {dstr(po_rate)}"}
        prs = receipt_rows(po_row["row_id"])
        if not prs:
            questions.append(Question("receipt_missing", f"Line {n}: nothing has been received against {po['id']} row "
                                      f"{po_row['idx']} yet, so it cannot be billed.", f"lines[{n}].qty", n,
                                      answer_type="text", source=src, rule=f"{INVOICE_POLICY}#I7"))
            lines_out.append(out)
            continue
        remaining_total = Decimal(0)
        allocation = []
        need = unit["qty"] * unit["conversion_factor"]
        for pr in prs:
            free = Decimal(pr["received_qty"] or pr["qty"]) - billed(pr["row_id"]) - used.get(pr["row_id"], Decimal(0))
            remaining_total += max(free, Decimal(0))
            if need > 0 and free > 0:
                take = min(free, need)
                allocation.append((pr, take))
                need -= take
        if need > 0:
            got = unit["qty"] * unit["conversion_factor"]
            questions.append(Question("receipt_quantity_exceeded", f"Line {n}: the invoice bills {dstr(got)} "
                                      f"{item['record']['fields']['stock_uom']} of {item['code']}, but only "
                                      f"{dstr(remaining_total)} received on {', '.join(sorted({p['receipt'] for p in prs}))} "
                                      "are not billed yet.", f"lines[{n}].qty", n,
                                      options=[{"value": dstr(remaining_total), "label": f"Bill the {dstr(remaining_total)} "
                                                "received"}] if remaining_total > 0 else [],
                                      answer_type="decimal", source=src, rule=f"{INVOICE_POLICY}#I7"))
            out["receipt"] = {"remaining": dstr(remaining_total), "billed_on_invoice": dstr(got)}
            lines_out.append(out)
            continue
        for pr, take in allocation:
            used[pr["row_id"]] = used.get(pr["row_id"], Decimal(0)) + take
        line_amount = Decimal(0)
        for pr, take in allocation:
            q = take / unit["conversion_factor"]
            amount = money(q * money(rate))
            line_amount += amount
            payload_lines.append({"item_code": item["code"], "qty": dstr(q), "uom": unit["uom"], "rate": dstr(money(rate)),
                                  "purchase_order": po["id"], "po_detail": po_row["row_id"],
                                  "purchase_receipt": pr["receipt"], "pr_detail": pr["row_id"],
                                  "source_ref": line.source_ref})
        stated_amount = to_number(line.amount)
        if stated_amount is not None and money(stated_amount) != line_amount:
            questions.append(Question("amount_mismatch", f"Line {n}: the amount {dstr(stated_amount)} is not "
                                      f"{dstr(unit['qty'])} x {dstr(rate)} = {dstr(line_amount)}.", f"lines[{n}].qty", n,
                                      answer_type="decimal", source=src, rule=f"{INVOICE_POLICY}#I6"))
        out["rate"] = {"value": dstr(money(rate)), "rule": rate_rule, "record": po["id"]}
        out["receipt"] = {"rows": [{"receipt": pr["receipt"], "row": pr["row_id"], "qty": dstr(take)} for pr, take in allocation],
                          "rule": {"id": "I7", "text": "Received and not yet billed (submitted and draft bills count)"}}
        out["order"] = {"purchase_order": po["id"], "row": po_row["row_id"], "rate": po_row["rate"]}
        out["amount"] = dstr(line_amount)
        lines_out.append(out)
    if not extraction.lines:
        questions.append(Question("no_lines", "No invoice lines were found.", "lines", answer_type="text"))
