"""The desk's rules, as hub files a builder can read and change.

* `knowledge/order-policy.md` and `knowledge/invoice-policy.md`: frontmatter
  settings and the numbered rules (O1, I1, ...) every proposal cites.
* `knowledge/sku-aliases.csv`: exact item and unit aliases, scoped by company
  and party: `company,party,item_alias,item_code,source_uom,target_uom,factor,source_ref`.
  `party` is a customer or supplier id, or `*` for every party. A row with
  `source_uom` and `target_uom` is a unit alias ("carton" means 12 Nos).
  `factor` is a positive decimal string. Every row cites its source.
* `knowledge/unit-words.csv`: `word,uom,source_ref`, the words documents use
  for ERP units ("boxes" is Box). Exact words only.

Nothing here is fuzzy. A value that no rule maps becomes a question.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

ORDER_POLICY = "knowledge/order-policy.md"
INVOICE_POLICY = "knowledge/invoice-policy.md"
ALIASES = "knowledge/sku-aliases.csv"
UNIT_WORDS = "knowledge/unit-words.csv"
ACTION_KEYS = ("price_difference_action", "quantity_difference_action", "substitution_action",
               "missing_reference_action")
ALIAS_COLUMNS = ["company", "party", "item_alias", "item_code", "source_uom", "target_uom", "factor", "source_ref"]


class PolicyError(Exception):
    def __init__(self, code: str, message: str, *, file: str | None = None, line: int | None = None,
                 field: str | None = None, rows: list | None = None):
        super().__init__(message)
        self.code, self.message, self.file, self.line, self.field, self.rows = code, message, file, line, field, rows

    def detail(self) -> dict:
        return {k: v for k, v in {"code": self.code, "message": self.message, "file": self.file, "line": self.line,
                                  "field": self.field, "rows": self.rows}.items() if v is not None}


def norm(text) -> str:
    """Unicode NFKC, trimmed, inner spaces collapsed, casefolded."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or ""))).strip().casefold()


@dataclass(frozen=True)
class Alias:
    row: int
    company: str
    party: str
    item_alias: str
    item_code: str
    source_uom: str
    target_uom: str
    factor: Decimal | None
    source_ref: str

    @property
    def is_unit(self) -> bool:
        return bool(self.source_uom)

    def cite(self) -> str:
        return f"{ALIASES}:{self.row}"


@dataclass
class Rules:
    order: dict
    invoice: dict
    aliases: list[Alias] = field(default_factory=list)
    unit_words: dict[str, tuple[str, int]] = field(default_factory=dict)   # word -> (uom, line)
    digest: str = ""
    files: dict[str, str] = field(default_factory=dict)

    def unit_for(self, word: str | None) -> tuple[str | None, str | None]:
        """(ERP uom, rule citation) for a unit word, or (None, None)."""
        if not word:
            return None, None
        hit = self.unit_words.get(norm(word))
        if hit:
            return hit[0], f"{UNIT_WORDS}:{hit[1]}"
        return None, None

    def item_alias(self, company: str, party: str, text: str | None) -> list[Alias]:
        if not text:
            return []
        key = norm(text)
        return [a for a in self.aliases if not a.is_unit and a.company == company and a.party in (party, "*")
                and norm(a.item_alias) == key]

    def unit_alias(self, company: str, party: str, item_code: str, unit_word: str | None) -> list[Alias]:
        if not unit_word:
            return []
        key = norm(unit_word)
        out = [a for a in self.aliases if a.is_unit and a.company == company and a.party in (party, "*")
               and a.item_code == item_code and norm(a.source_uom) == key]
        # The most specific party wins: a party's own row before a `*` row.
        return sorted(out, key=lambda a: 0 if a.party == party else 1)


def _frontmatter(path: Path, kind: str) -> dict:
    rel = str(path.name if not path.is_absolute() else path)
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise PolicyError("policy_invalid", f"{rel}: the file must start with --- frontmatter ---", file=rel, line=1)
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        raise PolicyError("policy_invalid", f"{rel}: the frontmatter is not valid YAML", file=rel, line=1) from None
    allowed = {"version", "kind", "currency_rounding", *ACTION_KEYS}
    lines = {k: n + 2 for n, line in enumerate(m.group(1).splitlines()) for k in [line.split(":", 1)[0].strip()]}
    for key in data:
        if key not in allowed:
            raise PolicyError("policy_invalid", f"{rel}:{lines.get(key)}: unknown key {key!r}", file=rel,
                              line=lines.get(key), field=key)
    if data.get("version") != 1:
        raise PolicyError("policy_invalid", f"{rel}: version must be 1", file=rel, line=lines.get("version"),
                          field="version")
    if data.get("kind") != kind:
        raise PolicyError("policy_invalid", f"{rel}: kind must be {kind}", file=rel, line=lines.get("kind"), field="kind")
    if data.get("currency_rounding") != "ERPNext":
        raise PolicyError("policy_invalid", f"{rel}: currency_rounding must be ERPNext (the ERP computes totals)",
                          file=rel, line=lines.get("currency_rounding"), field="currency_rounding")
    for key in ACTION_KEYS:
        if data.get(key) != "question":
            raise PolicyError("policy_invalid", f"{rel}:{lines.get(key)}: {key} must be question. This hub never "
                              "accepts a difference silently", file=rel, line=lines.get(key), field=key)
    rules = re.findall(r"^\*\*([OI]\d+)\.\*\*", text, re.M)
    data["rule_ids"] = rules
    return data


def _decimal(value: str) -> Decimal | None:
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


def _aliases(path: Path, company: str) -> list[Alias]:
    text = path.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header != ALIAS_COLUMNS:
        raise PolicyError("policy_invalid", f"{ALIASES}:1: columns must be {','.join(ALIAS_COLUMNS)}", file=ALIASES,
                          line=1)
    out = []
    for n, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(ALIAS_COLUMNS):
            raise PolicyError("policy_invalid", f"{ALIASES}:{n}: expected {len(ALIAS_COLUMNS)} columns", file=ALIASES,
                              line=n)
        c = dict(zip(ALIAS_COLUMNS, (v.strip() for v in row)))
        if c["company"] != company:
            raise PolicyError("policy_invalid", f"{ALIASES}:{n}: company must be {company}", file=ALIASES, line=n,
                              field="company")
        for key in ("party", "item_alias", "item_code", "source_ref"):
            if not c[key]:
                raise PolicyError("policy_invalid", f"{ALIASES}:{n}: {key} is required", file=ALIASES, line=n,
                                  field=key)
        if bool(c["source_uom"]) != bool(c["target_uom"]):
            raise PolicyError("policy_invalid", f"{ALIASES}:{n}: a unit alias needs both source_uom and target_uom",
                              file=ALIASES, line=n, field="source_uom")
        factor = None
        if c["source_uom"]:
            factor = _decimal(c["factor"])
            if factor is None:
                raise PolicyError("policy_invalid", f"{ALIASES}:{n}: factor must be a positive decimal", file=ALIASES,
                                  line=n, field="factor")
        elif c["factor"]:
            raise PolicyError("policy_invalid", f"{ALIASES}:{n}: factor is only for unit aliases", file=ALIASES, line=n,
                              field="factor")
        out.append(Alias(n, c["company"], c["party"], c["item_alias"], c["item_code"], c["source_uom"],
                         c["target_uom"], factor, c["source_ref"]))
    groups: dict[tuple, list[Alias]] = {}
    for a in out:
        groups.setdefault((a.company, a.party, norm(a.item_alias), norm(a.source_uom), a.is_unit and a.item_code),
                          []).append(a)
    for key, rows in groups.items():
        targets = {(a.item_code, a.target_uom, a.factor) for a in rows}
        if len(targets) > 1:
            raise PolicyError("alias_conflict", f"{ALIASES}: rows {', '.join(str(a.row) for a in rows)} map "
                              f"{rows[0].item_alias!r} to different targets. Keep one.", file=ALIASES,
                              rows=[a.row for a in rows])
    return out


def _unit_words(path: Path) -> dict[str, tuple[str, int]]:
    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8")))
    header = next(reader, None)
    if header != ["word", "uom", "source_ref"]:
        raise PolicyError("policy_invalid", f"{UNIT_WORDS}:1: columns must be word,uom,source_ref", file=UNIT_WORDS,
                          line=1)
    out: dict[str, tuple[str, int]] = {}
    for n, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != 3 or not all(c.strip() for c in row):
            raise PolicyError("policy_invalid", f"{UNIT_WORDS}:{n}: word, uom and source_ref are required",
                              file=UNIT_WORDS, line=n)
        word = norm(row[0])
        if word in out and out[word][0] != row[1].strip():
            raise PolicyError("alias_conflict", f"{UNIT_WORDS}: rows {out[word][1]} and {n} map {row[0]!r} to "
                              "different units", file=UNIT_WORDS, rows=[out[word][1], n])
        out[word] = (row[1].strip(), n)
    return out


def load(hub_dir, company: str) -> Rules:
    hub_dir = Path(hub_dir)
    files = {}
    for rel in (ORDER_POLICY, INVOICE_POLICY, ALIASES, UNIT_WORDS):
        path = hub_dir / rel
        if not path.is_file():
            raise PolicyError("policy_invalid", f"{rel} is missing", file=rel)
        files[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    order = _frontmatter(hub_dir / ORDER_POLICY, "order")
    invoice = _frontmatter(hub_dir / INVOICE_POLICY, "invoice")
    aliases = _aliases(hub_dir / ALIASES, company)
    units = _unit_words(hub_dir / UNIT_WORDS)
    digest = "sha256:" + hashlib.sha256("\n".join(f"{k}={v}" for k, v in sorted(files.items())).encode()).hexdigest()
    return Rules(order=order, invoice=invoice, aliases=aliases, unit_words=units, digest=digest, files=files)
