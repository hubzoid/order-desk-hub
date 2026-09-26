# Operator guide

For the sales and accounts people who work the desk, and the administrators who keep it running.

## The queue

Every incoming order or supplier invoice is a **case** (CASE-000001 and so on). Ask the desk assistant "What is in the desk queue?", or run `hubzoid-desk list .`. The Hubzoid Console shows the same work under Work Items (one intake item per document, one item per ERP action) and under the agent's Intake tab (every message and attachment as received).

| State | Meaning | What you do |
|---|---|---|
| `needs_owner` | A clean proposal, nobody owns it yet | Claim it |
| `needs_clarification` | A question the rules cannot answer | Claim it, answer the question |
| `draft_ready` | You own it and it has no questions | Review the lines, save the ERP draft |
| `erp_draft_saved` | A real ERPNext draft exists (docstatus 0) | Finish it in ERPNext, or ask to submit (approve mode) |
| `waiting_approval` | You asked to submit the exact draft | Approve or deny it on the card |
| `submitting` | Hubzoid is submitting it | Wait |
| `done` | ERPNext shows docstatus 1 | Nothing |
| `duplicate` | The same document is already a case, or already in the ERP | Nothing, or check the original |
| `error` | The document could not be read, or a setup problem | Follow the message, then restart the work item |
| `ambiguous` | An ERP write may or may not have happened | Reconcile before anything else |
| `cancelled` | Closed by a person | Nothing. An ERP draft it made stays in the ERP |

A proposal is not an ERP draft, and an ERP draft is not a submitted document. The desk never calls a draft posted, booked or confirmed.

## Working a case

1. **Claim it** (`desk_claim` in chat, `hubzoid-desk claim . CASE --expected-revision N`). Only one person owns a case. If someone claimed it a moment before you, you are told who.
2. **Review it** (`desk_case`). The case page shows each source line (the PDF row, the sheet cells, the email line) next to the proposed item, quantity, unit conversion, rate and amount, with the rule behind each value. The same page is in the chat's document panel and in `output/desk/cases/`.
3. **Answer questions** (`desk_answer`, or `hubzoid-desk correct . CASE --set lines[1].item_code=SYN-ITEM-009`). An answer changes this case only and creates a new proposal revision. Earlier revisions are kept. An answer is saved only if the case is still as you saw it: if someone else answered or the case changed owner meanwhile, reload it. Besides a line's `item_code`, `qty`, `uom`, `rate` and `po_detail`, you can remove a line (`lines[N].include=no`), keep a repeated one (`lines[N].include=yes`), add a line the extractor missed from its source element (`lines[N].source=E4` with its `item_code` and `qty`, numbered after the extracted lines), say an element is not a line (`ack.uncovered.E4=not_a_line`) and confirm nothing is missing from an unreadable attachment (`ack.unreadable.N=nothing_missing`).
4. **Save the ERP draft** (`desk_create_draft`, or `hubzoid-desk draft . CASE --expected-revision N --digest sha256:...`). You confirm the proposal digest you reviewed. The draft is read back from ERPNext and compared line by line. If ERPNext computed anything different (taxes, rounding), the case says so and submission waits for your review in ERPNext.
5. **Submit** (approve mode only): ask with `desk_request_submission`. The card shows the company, document, party, reference, every line, the totals, the ERP version and the digest. Approve it yourself on the card, in the Console's Work Items screen, or with `hubzoid operations approve`. If the draft changed in ERPNext since you saw it, Hubzoid asks for a fresh approval and never submits the old one. Deny leaves the draft as it is.

Releasing a case (`desk_release`) cancels your pending draft or submit requests.

## When an ERP write is uncertain

If the connection drops after a write was sent, the case shows **"The ERP may have accepted this operation. Reconcile before retrying."** Nothing is resent on its own.

1. `desk_reconcile` (or `hubzoid-desk reconcile . CASE`) reads ERPNext for the operation's unique marker and reports what it finds. It writes nothing.
2. The person who asked for the write decides in the Console's Work Items screen or with `hubzoid operations resolve`: **done** with the ERP document name as evidence, **retry** (safe, because the marker makes a second draft impossible), or **abandon**.

## Administrators

- **Health:** `desk_health` or `hubzoid-desk health .` shows cases per state with the oldest age, duplicates, uncertain writes, errors, whether intake is on and the ERP's readiness.
- **Access:** grants on the hub: `desk_view`, `desk_operate`, `desk_submit`, `desk_manage` (`hubzoid grant EMAIL desk_operate .`). Hubzoid also checks that a person may start the workflow that does the work: an operator who saves drafts or asks to submit needs `wf_order_invoice_case`, and one who uploads documents needs `wf_order_invoice_intake`. Approving is never a grant: only the requester approves their own submission.
- **Reassign** a case to another operator with `hubzoid-desk reassign` (outstanding requests are cancelled, the new owner starts fresh).
- **Senders:** `hubzoid-desk bindings list|import|revoke`. A sender with no binding waits for a person to choose the customer or supplier.
- **Pause intake:** set `DESK_INTAKE_ENABLED: false` in `hub.settings.yaml`. Messages are still recorded and wait.
- **Doctor:** `hubzoid doctor .` runs the desk's checks with the rest of Hubzoid's. `hubzoid-desk doctor .` shows only the desk's.
