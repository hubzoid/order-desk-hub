"""ERP-independent, source-grounded records and reviewed deterministic rules."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .extract import Extraction, norm_text, to_number

RULE_FILE = "knowledge/record-rules.md"
LEGACY_RULE_FILE = "knowledge/record-rules.yaml"


class Conversion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = Field(min_length=1)
    source_unit: str = Field(min_length=1)
    target_unit: str = Field(min_length=1)
    factor: str
    source_ref: str = Field(min_length=1)

    @field_validator("factor")
    @classmethod
    def positive(cls, v):
        if number(v) is None or number(v) <= 0:
            raise ValueError("factor must be a finite positive decimal")
        return v


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class RecordRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    company: str = Field(default="My company", min_length=1, max_length=140)
    auto_accept: bool = False
    tolerance: str = "0"
    rounding: Literal["exact", "half_up"] = "exact"
    default_tax_basis: Literal["unknown", "exclusive", "inclusive"] = "unknown"
    aliases: list[Alias] = Field(default_factory=list)
    conversions: list[Conversion] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("tolerance")
    @classmethod
    def finite(cls, v):
        if number(v) is None or number(v) < 0:
            raise ValueError("tolerance must be a finite nonnegative decimal")
        return v

    def digest(self):
        return "sha256:" + hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def number(value) -> Decimal | None:
    if value is None or isinstance(value, (float, bool)):
        return None
    try:
        out = Decimal(str(value))
        return out if out.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def read_rule_text(text: str) -> RecordRules:
    if text.startswith("---\n"):
        front, separator, _ = text[4:].partition("\n---")
        if not separator:
            raise ValueError("record rules need closing Markdown frontmatter")
        metadata = yaml.safe_load(front) or {}
        return RecordRules.model_validate(metadata.get("rules") or {})
    # Legacy YAML remains readable. New changes always propose Markdown.
    return RecordRules.model_validate(yaml.safe_load(text) or {})


def rule_text(rules: RecordRules) -> str:
    metadata = {"name": "Order Desk record rules", "description": "Reviewed typed rules for ERP-free orders, invoices and reports.",
                "rules": rules.model_dump()}
    return "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n\n# Order Desk record rules\n\nOnly the typed frontmatter rules execute. Review sources and holdout results before activating a change.\n"


def load_rules(hub_dir) -> RecordRules:
    path = Path(hub_dir) / RULE_FILE
    if not path.is_file():
        path = Path(hub_dir) / LEGACY_RULE_FILE
    if not path.is_file():
        return RecordRules()
    if not path.resolve().is_relative_to(Path(hub_dir).resolve()):
        raise ValueError("record rules must remain inside the hub")
    return check_rules(read_rule_text(path.read_text()), hub_dir)


def check_rules(rules: RecordRules, hub_dir) -> RecordRules:
    aliases = {}
    conversions = {}
    for row in rules.aliases:
        key = norm_text(row.source)
        if key in aliases and aliases[key] != row.target:
            raise ValueError(f"conflicting alias: {row.source}")
        aliases[key] = row.target
    for row in rules.conversions:
        key = (norm_text(row.item), norm_text(row.source_unit))
        target = (row.target_unit, number(row.factor))
        if key in conversions and conversions[key] != target:
            raise ValueError(f"conflicting unit conversion: {row.item}/{row.source_unit}")
        conversions[key] = target
    for ref in rules.source_refs + [r.source_ref for r in rules.aliases + rules.conversions]:
        path = Path(hub_dir) / ref
        if not path.resolve().is_relative_to(Path(hub_dir).resolve()) or not path.is_file():
            raise ValueError(f"source_ref must name an existing file inside the hub: {ref}")
    return rules


def validate(record: dict, rules: RecordRules | None = None) -> dict:
    """Return a copy with deterministic facts/errors. Never coerce unknown to zero."""
    from decimal import ROUND_HALF_UP

    rules = rules or RecordRules()
    row = json.loads(json.dumps(record))
    errors = list(row.get("source_problems") or [])
    def error(reason):
        if reason not in errors:
            errors.append(reason)
    if row.get("document_kind") not in ("order", "invoice"):
        error("unsupported_document_kind")
    if not re.fullmatch(r"[A-Z]{3}", row.get("currency") or ""):
        error("currency_unknown")
    try:
        date.fromisoformat(row.get("document_date") or "")
    except ValueError:
        error("document_date_unknown")
    if not row.get("party"):
        error("party_unknown")
    if not row.get("reference") and row.get("requires_reference", True):
        error("reference_unknown")
    total, tax = number(row.get("stated_total")), number(row.get("stated_tax"))
    if total is None:
        error("total_unknown")
    elif total < 0:
        error("credit_or_cancellation")
    if tax is None or tax < 0:
        error("tax_unknown")
    basis = row.get("tax_basis", "unknown")
    if basis not in ("exclusive", "inclusive"):
        error("tax_basis_unknown")
    subtotal = Decimal("0")
    lines = row.get("lines") or []
    if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
        error("lines_invalid")
        lines = []
        row["lines"] = []
    if not lines:
        error("lines_unknown")
    for line in lines:
        qty, rate = number(line.get("quantity")), number(line.get("unit_price"))
        stated = number(line.get("amount"))
        if qty is None or qty <= 0 or rate is None or rate < 0 or not line.get("unit"):
            error("line_quantity_unit_or_rate_unknown")
            continue
        computed = qty * rate
        if stated is not None and computed != stated:
            error("line_amount_mismatch")
        subtotal += computed
    if total is not None and tax is not None and basis in ("exclusive", "inclusive"):
        expected = subtotal + tax if basis == "exclusive" else subtotal
        # The literal header precision is the stated precision. Exact mode does
        # not round away a discrepancy. A reviewed half_up rule can do so.
        if rules.rounding == "half_up":
            expected = expected.quantize(Decimal(1).scaleb(total.as_tuple().exponent), rounding=ROUND_HALF_UP)
        if abs(expected - total) > Decimal(rules.tolerance):
            error("total_mismatch_or_unsupported_rounding")
        row["net"] = str(total - tax)
    else:
        row["net"] = None
    if row.get("duplicate_of"):
        error("duplicate_candidate")
    row["exceptions"] = errors
    row["rule_revision"] = rules.digest()
    prior = row.get("status")
    row["status"] = "superseded" if prior == "superseded" else "exception" if errors else ("accepted" if prior == "accepted" or rules.auto_accept else "unreviewed")
    return row


def from_extraction(ex: Extraction, elements: list[dict], *, case: dict, rules: RecordRules,
                    source_problems=(), changes: dict | None = None) -> dict:
    text = "\n".join(e["text"] for e in elements)
    basis = rules.default_tax_basis
    if re.search(r"tax[ -]inclusive|includ(?:es|ing)\s+tax", text, re.I):
        basis = "inclusive"
    elif re.search(r"tax[ -]exclusive|exclud(?:es|ing)\s+tax", text, re.I):
        basis = "exclusive"
    elif ex.stated_tax and to_number(ex.stated_tax.value) == 0:
        basis = "exclusive"  # zero tax makes these equivalent, not inferred tax
    aliases = {norm_text(a.source): a.target for a in rules.aliases}
    lines, converted = [], []
    for i, line in enumerate(ex.lines):
        item = line.item_code_seen or line.description_seen or ""
        mapped = aliases.get(norm_text(item), item)
        lines.append({"item": mapped, "description": line.description_seen, "quantity": line.quantity,
                      "unit": line.unit_seen, "unit_price": line.unit_price, "amount": line.amount,
                      "source_ref": line.source_ref, "raw_text": line.raw_text})
    row = {"record_id": case["id"], "company": case["company"], "case_number": case.get("number"),
           "document_kind": {"customer_order": "order", "supplier_invoice": "invoice"}.get(ex.document_kind, ex.document_kind),
           "reference": ex.reference.value if ex.reference else None, "party": ex.party_name_seen,
           "document_date": ex.document_date.value if ex.document_date else None, "currency": ex.currency_seen,
           "stated_total": ex.stated_total.value if ex.stated_total else None,
           "stated_tax": ex.stated_tax.value if ex.stated_tax else None, "tax_basis": basis, "lines": lines,
           "approved_conversions": converted, "source_problems": [p["reason"] for p in source_problems],
           "extraction": ex.model_dump(mode="json"), "evidence": elements,
           "source_event_id": case["source_event_id"], "received_at": case["received_at"],
           "owner": case.get("owner"), "status": "unreviewed"}
    for field in ("party", "currency"):
        if row.get(field) and norm_text(row[field]) not in norm_text(text):
            row[field] = None
            row["source_problems"].append(field + "_not_in_source")
    if ex.instructions_seen:
        row["source_problems"].append("untrusted_instructions")
    row.update(changes or {})
    row["case_changes"] = dict(changes or {})
    row["approved_conversions"] = approved_conversions(row["lines"], rules)
    row["source_problems"] = [p for p in row["source_problems"] if p not in row.get("source_acknowledgements", [])]
    return validate(row, rules)


def approved_conversions(lines: list[dict], rules: RecordRules) -> list[dict]:
    converted = []
    for i, line in enumerate(lines if isinstance(lines, list) else []):
        if not isinstance(line, dict):
            continue
        for c in rules.conversions:
            if norm_text(c.item) == norm_text(line.get("item")) and norm_text(c.source_unit) == norm_text(line.get("unit")):
                converted.append({"line": i, "item": line.get("item"), "from": line.get("unit"), "to": c.target_unit,
                                  "factor": c.factor, "source_ref": c.source_ref})
    return converted
