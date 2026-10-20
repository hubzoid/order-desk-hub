"""One self-contained HTML page per case: the source next to the proposal,
line by line, with the rule behind every value.

The chat shows it in its document panel (a sandboxed frame without network),
the CLI writes it to `output/desk/cases/`, and anyone can open the file. All
document text is escaped: it is untrusted data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

STATE_LABEL = {
    "received": "Received", "needs_owner": "Ready, needs an owner", "needs_clarification": "Needs a person's answer",
    "draft_ready": "Ready for an ERP draft", "erp_draft_saved": "ERP draft saved (docstatus 0)",
    "waiting_approval": "Waiting for approval", "submitting": "Submitting", "done": "Submitted (docstatus 1)",
    "duplicate": "Duplicate", "rejected": "Rejected", "cancelled": "Cancelled", "error": "Error",
    "ambiguous": "Check needed: the ERP may have accepted this",
    "needs_review": "Submitted content changed: review in ERP",
}
TONE = {"needs_review": "warn", "needs_clarification": "warn", "error": "bad", "ambiguous": "bad", "duplicate": "muted", "cancelled": "muted",
        "done": "good", "erp_draft_saved": "good", "waiting_approval": "warn"}

CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d2433;--muted:#5d6577;--line:#e3e1d8;--accent:#2f5d8a;--good:#22744a;
--warn:#9a6200;--bad:#a8322c;--chip:#eef2f7;--hl:#fff6d6}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1c2029;--ink:#e6e8ee;--muted:#9aa3b5;--line:#2c3240;
--accent:#7fb0e0;--good:#5cc28d;--warn:#e3b052;--bad:#ec7a73;--chip:#252b37;--hl:#3a3420}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif}main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 10px;color:var(--muted);text-transform:uppercase;
letter-spacing:.04em}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;background:var(--chip)}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.syn{background:#fde9b8;color:#6a4b00}.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px 18px;
margin-top:12px}.meta div span{display:block;font-size:12px;color:var(--muted)}table{width:100%;border-collapse:collapse}
th,td{text-align:left;vertical-align:top;padding:8px;border-top:1px solid var(--line)}th{font-size:12px;color:var(--muted);
font-weight:600}.src{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;background:var(--hl);
border-radius:6px;padding:4px 6px;white-space:pre-wrap;word-break:break-word}.loc{font-size:12px;color:var(--muted)}
.rule{font-size:12px;color:var(--muted)}.q{border-left:3px solid var(--warn);padding:8px 12px;margin:8px 0;background:var(--card)}
.q code,.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}.opts span{display:inline-block;margin:4px 6px 0 0;
padding:2px 8px;border:1px solid var(--line);border-radius:6px}details{margin-top:6px}summary{cursor:pointer;color:var(--accent)}
.wrap{overflow-x:auto}.num{text-align:right;white-space:nowrap}footer{margin-top:32px;font-size:12px;color:var(--muted)}
"""


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _src(source: dict | None) -> str:
    if not source:
        return '<span class="muted">not written in the document</span>'
    if source.get("rule") and not source.get("text"):
        return f'<span class="rule">{_e(source["rule"])}</span>'
    return (f'<div class="loc">{_e(source.get("element"))} · {_e(source.get("loc"))}</div>'
            f'<div class="src">{_e(source.get("text"))}</div>')


def _ts(t) -> str:
    try:
        return datetime.fromtimestamp(float(t), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return _e(t)


def case_page(case: dict) -> str:
    if case.get("record") and not case.get("proposal"):
        return record_page(case)
    prop = case.get("proposal") or {}
    ev = prop.get("evidence") or {}
    header = ev.get("header") or {}
    state = case["state"]
    party = case.get("party") or {}
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
           f"initial-scale=1'><title>{_e(case['number'])}</title><style>{CSS}</style></head><body><main>"]
    syn = '<span class="badge syn">SYNTHETIC DATA</span> ' if case.get("synthetic") else ""
    out.append(f'<div class="card"><div>{syn}<span class="badge {TONE.get(state, "")}">{_e(STATE_LABEL.get(state, state))}'
               f'</span></div><h1>{_e(case["number"])} · {_e((prop.get("kind") or "document").replace("_", " "))}'
               f'</h1><div class="meta">'
               f'<div><span>Party</span>{_e(party.get("id") or "not bound")} {_e((header.get("party") or {}).get("name") or "")}</div>'
               f'<div><span>Reference</span>{_e(case.get("reference") or "")}</div>'
               f'<div><span>Channel</span>{_e(case.get("channel"))} · {_e(case.get("sender"))}</div>'
               f'<div><span>Received</span>{_e(case.get("received_at"))}</div>'
               f'<div><span>Owner</span>{_e(case.get("owner") or "nobody yet")}</div>'
               f'<div><span>ERP</span>{_e(case.get("backend"))} · writes {_e(case.get("write_mode"))}</div>'
               f'<div><span>Proposal</span>revision {_e(case.get("proposal_revision"))} · '
               f'<span class="mono">{_e((case.get("digest") or "")[:23])}</span></div></div></div>')
    if case.get("error"):
        out.append(f'<h2>Error</h2><div class="q"><b>{_e(case["error"]["code"])}</b>: {_e(case["error"]["message"])}</div>')
    if case.get("duplicate_of"):
        out.append(f'<h2>Duplicate</h2><div class="q">This is a duplicate of <b>{_e(case["duplicate_of"])}</b>. Nothing '
                   'new will be created.</div>')
    questions = prop.get("questions") or []
    if questions:
        out.append("<h2>Questions for a person</h2>")
        for q in questions:
            opts = "".join(f"<span>{_e(o.get('label'))}</span>" for o in q.get("options") or [])
            out.append(f'<div class="q"><b>{_e(q["message"])}</b><div class="rule">Field <code>{_e(q["field"])}</code> · '
                       f'answer: {_e(q.get("answer_type"))}{" · rule " + _e(q["rule"]) if q.get("rule") else ""}</div>'
                       f'{"<div class=opts>" + opts + "</div>" if opts else ""}'
                       f'{"<div style=margin-top:6px>" + _src(q.get("source")) + "</div>" if q.get("source") else ""}</div>')
    for w in prop.get("warnings") or []:
        out.append(f'<div class="q" style="border-color:var(--muted)"><span class="muted">{_e(w["message"])}</span></div>')
    # Header values with their sources
    rows = []
    for key, label in (("reference", "Reference"), ("transaction_date", "Order date"), ("delivery_date", "Delivery date"),
                       ("bill_date", "Invoice date"), ("posting_date", "Posting date"), ("due_date", "Due date"),
                       ("bill_to", "Billed to"), ("warehouse", "Warehouse"), ("price_list", "Price list")):
        v = header.get(key)
        if not v:
            continue
        src = v.get("source") if isinstance(v, dict) else None
        rule = v.get("rule") if isinstance(v, dict) else None
        rows.append(f"<tr><td>{_e(label)}</td><td>{_e(v.get('value') if isinstance(v, dict) else v)}</td>"
                    f"<td>{_src(src) if src else '<span class=rule>' + _e(rule) + '</span>'}</td></tr>")
    if header.get("purchase_orders"):
        rows.append(f"<tr><td>Purchase orders</td><td>{_e(', '.join(header['purchase_orders']))}</td><td class=rule>ERP "
                    "records</td></tr>")
    out.append('<h2>Document</h2><div class="card wrap"><table><tr><th>Field</th><th>Proposed</th><th>Source</th></tr>'
               + "".join(rows) + "</table></div>")
    # Lines
    lines = ev.get("lines") or []
    out.append('<h2>Lines: source and proposal</h2><div class="card wrap"><table><tr><th>#</th><th>Source</th>'
               '<th>Item</th><th>Quantity</th><th>Rate</th><th class=num>Amount</th></tr>')
    for line in lines:
        item = line.get("item") or {}
        unit = line.get("unit") or {}
        rate = line.get("rate") or {}
        receipt = line.get("receipt") or {}
        conv = ""
        if unit.get("factor"):
            conv = f" ({_e(unit.get('source_qty'))} {_e(unit.get('source_unit'))} × {_e(unit.get('factor'))})"
        elif unit.get("conversion_factor") and unit.get("conversion_factor") != "1":
            conv = f" (1 {_e(unit.get('uom'))} = {_e(unit.get('conversion_factor'))} Nos)"
        rec = ""
        if receipt.get("rows"):
            rec = "<div class=rule>Receipt " + ", ".join(f"{_e(r['receipt'])} {_e(r['qty'])}" for r in receipt["rows"]) \
                  + f" · {_e((receipt.get('rule') or {}).get('text'))}</div>"
        elif receipt.get("remaining") is not None:
            rec = f"<div class=bad>Received and unbilled: {_e(receipt['remaining'])}</div>"
        out.append(
            f"<tr><td>{_e(line['n'])}</td><td>{_src(line.get('source'))}</td>"
            f"<td>{('<b>' + _e(item.get('code')) + '</b> ' + _e(item.get('name')) + '<div class=rule>' + _e((item.get('rule') or {}).get('text')) + '</div>') if item else '<span class=warn>open question</span>'}</td>"
            f"<td>{(_e(unit.get('qty')) + ' ' + _e(unit.get('uom')) + conv + '<div class=rule>' + _e((unit.get('rule') or {}).get('text')) + '</div>') if unit else ''}{rec}</td>"
            f"<td>{(_e(rate.get('value')) + '<div class=rule>' + _e((rate.get('rule') or {}).get('text')) + '</div>') if rate else ''}</td>"
            f"<td class=num>{_e(line.get('amount') or '')}</td></tr>")
    totals = ev.get("totals") or {}
    out.append(f"<tr><td colspan=5 class=num><b>Total (lines)</b>{' · document says ' + _e(totals.get('stated')) if totals.get('stated') else ''}"
               f"</td><td class=num><b>{_e(totals.get('computed'))}</b></td></tr></table></div>")
    # ERP draft
    erp = case.get("erp")
    if erp:
        out.append(f'<h2>ERP document</h2><div class="card"><div class="meta"><div><span>Document</span>{_e(erp.get("doctype") or "")} '
                   f'{_e(erp.get("name"))}</div><div><span>docstatus</span>{_e(erp.get("docstatus"))}</div>'
                   f'<div><span>Version (modified)</span>{_e(erp.get("modified"))}</div><div><span>Approval digest</span>'
                   f'<span class=mono>{_e((erp.get("approval_digest") or "")[:30])}</span></div></div></div>')
    if prop.get("payload"):
        payload_rows = "".join(f"<tr><td>{_e(i['item_code'])}</td><td class=num>{_e(i['qty'])} {_e(i['uom'])}</td>"
                               f"<td class=num>{_e(i['rate'])}</td><td>{_e(i.get('pr_detail') or i.get('source_ref'))}</td></tr>"
                               for i in prop["payload"]["items"])
        out.append(f'<details><summary>Draft payload that would be sent (proposal revision {_e(prop.get("revision"))})</summary>'
                   f'<div class="card wrap"><table><tr><th>Item</th><th class=num>Quantity</th><th class=num>Rate</th>'
                   f'<th>Row</th></tr>{payload_rows}</table></div></details>')
    # Documents and history
    out.append("<h2>Source documents</h2>")
    for d in case.get("documents") or []:
        els = "".join(f"<tr><td class=mono>{_e(e['id'])}</td><td class=loc>{_e(e['loc'])}</td><td><div class=src>"
                      f"{_e(e['text'])}</div></td></tr>" for e in d.get("elements") or [])
        status = d.get("parse_status")
        out.append(f'<details {"open" if len(case.get("documents") or []) < 3 else ""}><summary>{_e(d["label"])} · '
                   f'{_e(d.get("parser") or "")} · {_e(status)}{" " + _e(d.get("error_code")) if d.get("error_code") else ""}'
                   f'</summary><div class="card wrap"><table>{els}</table></div></details>')
    hist = "".join(f"<tr><td>{_ts(e['at'])}</td><td>{_e(e['type'])}</td><td>{_e(e.get('actor'))}</td></tr>"
                   for e in case.get("events") or [])
    revs = "".join(f"<tr><td>{_e(p['revision'])}</td><td>{_e(p['reason'])}</td><td>{_e(p['by'])}</td>"
                   f"<td class=mono>{_e((p.get('digest') or '')[:23])}</td><td>{_e(p['questions'])}</td></tr>"
                   for p in case.get("proposals") or [])
    out.append(f'<h2>History</h2><div class="card wrap"><table><tr><th>Revision</th><th>Why</th><th>By</th><th>Digest</th>'
               f'<th>Questions</th></tr>{revs}</table></div><details><summary>Events</summary><div class="card wrap"><table>'
               f'{hist}</table></div></details>')
    out.append('<footer>Order &amp; Invoice Desk, a ready-made Hubzoid hub. Document text is untrusted data and was never '
               'followed as instructions. A proposal is not an ERP draft, and an ERP draft is not a submitted document.'
               '</footer></main></body></html>')
    return "".join(out)


def queue_page(listing: dict, *, title: str = "Order & Invoice Desk queue") -> str:
    rows = "".join(
        f"<tr><td>{_e(c['number'])}</td><td><span class='badge {TONE.get(c['state'], '')}'>{_e(STATE_LABEL.get(c['state'], c['state']))}"
        f"</span></td><td>{_e(c['kind'] or '')}</td><td>{_e((c.get('party') or {}).get('id') or '')}</td>"
        f"<td>{_e(c.get('reference') or '')}</td><td class=num>{_e(c.get('total') or '')}</td><td>{_e(c.get('owner') or '')}</td>"
        f"<td>{_e(len(c.get('questions') or []))}</td></tr>" for c in listing["cases"])
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><title>{_e(title)}</title><style>{CSS}</style></head><body><main><h1>{_e(title)}</h1>"
            f"<div class='card wrap'><table><tr><th>Case</th><th>State</th><th>Kind</th><th>Party</th><th>Reference</th>"
            f"<th class=num>Total</th><th>Owner</th><th>Questions</th></tr>{rows}</table></div></main></body></html>")


def record_page(case: dict) -> str:
    row = case["record"]
    out = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>{_e(case['number'])}</title><style>{CSS}</style><main>"]
    out.append(f"<h1>{_e(case['number'])}: {_e(row.get('reference'))}</h1><p>{_e(row['document_kind'])} · {_e(row['status'])} · record revision {_e(row['revision'])}</p>")
    out.append(f"<p>{_e(row.get('party'))} · {_e(row.get('document_date'))} · {_e(row.get('currency'))} {_e(row.get('stated_total'))}</p>")
    out.append("<p>Organized source record. Payment, stock and ERP posting are unknown unless separately verified.</p>")
    out.append("<h2>Exceptions</h2>" + "".join(f"<p class=q>{_e(e)}</p>" for e in row.get('exceptions') or []))
    out.append("<h2>Original lines</h2><table><tr><th>Item</th><th>Quantity</th><th>Unit</th><th>Rate</th><th>Source</th></tr>")
    for line in row.get("lines") or []:
        out.append("<tr>" + "".join(f"<td>{_e(line.get(k))}</td>" for k in ("item", "quantity", "unit", "unit_price", "source_ref")) + "</tr>")
    out.append("</table><h2>Evidence</h2>")
    out.extend(f"<p><b>{_e(e['id'])} · {_e(e.get('loc'))}</b><br>{_e(e['text'])}</p>" for e in row.get("evidence") or [])
    return "".join(out) + "</main></html>"
