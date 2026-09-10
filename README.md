# Order & Invoice Desk

**Inbox to ERP.** A ready-made [Hubzoid](https://github.com/hubzoid/hubzoid) hub that turns customer orders into Sales Order drafts and supplier invoices into Purchase Invoice drafts in ERPNext. Every proposed line shows the exact source line it came from and the rule that decided it. Anything the rules do not decide comes back as a precise question. A person approves every submission.

> Status: in development, not released. Proven on the labelled synthetic sample and on a fake ERPNext REST server. Not yet qualified against a live ERPNext site. See [What is proven](#what-is-proven).

## The ten-minute result

A fictional distributor, Synthetic Meridian Distribution, receives twelve messy documents: orders as email text, a PDF, a spreadsheet, a WhatsApp message and a photo of a handwritten list, and supplier invoices as PDFs, a spreadsheet, an email and a photo. The desk turns ten of them into ERP draft proposals, each line tied to its source, and comes back with two questions:

- "Synthetic Hex Bolt M8" matches two items. Which one?
- The invoice bills 50 floor cleaners, but only 40 were received and not yet billed.

Then you correct a rule ("a carton of SYN-ITEM-001 is 12 units"), rerun the document, and the next result follows it, after a restart too.

## How it works

```
email, WhatsApp, upload ──> Hubzoid intake (sender check, attachments stored)
        │
        ▼  work item: order_invoice_intake (unattended, never writes to the ERP)
   parse ─────────> extract ─────────────> match and validate ──────> case
   (Docling,        (desk-extractor        (exact item codes, alias     needs an owner,
    openpyxl)        station, typed         rows, units, prices,         or a question,
                     output, every value    purchase orders and          or a duplicate
                     checked against        receipts, Decimal money,
                     its source)            duplicates)
        │
        ▼  a person claims the case
   work item: order_invoice_case, requester = that person
   draft:   hub.operation.run → create_draft → an inert ERPNext draft (docstatus 0), read back and compared
   submit:  approve mode only → the requester approves the exact draft (lines, totals, version, digest)
            → digest, access and ERP version rechecked → submit_approved → docstatus 1
```

The model only reads documents. Matching, money, duplicates, permissions and every write are deterministic code. Writes go through Hubzoid's operation ledger, so a lost answer is never retried blindly: a unique marker in ERPNext tells whether the draft exists.

## Quick start (sample, no ERP account, no API key)

You need Python 3.11 or later, Hubzoid 2.0 and a signed-in Claude Code (the extractor runs on your `claude-local` login).

Tell Claude Code or Codex:

> Set up the Order & Invoice Desk in this folder on the synthetic sample. Show me the files and rules, run the twelve sample documents, show every line's source, and stop before any real ERP connection or submission.

Or by hand:

```bash
pip install -e '.[parsing]'          # the desk, its ERP module and Docling
hubzoid-desk parser prefetch         # Docling's model assets, once
hubzoid-desk sample install .        # dataset, sender bindings, two sample channels
hubzoid-desk doctor .                # every prerequisite at once
echo 'HUBZOID_SCHEDULES=1' >> .env   # desk workflows run on this box
hubzoid run . --port 8711 --bridge-port 8712
hubzoid-desk sample load .           # the twelve messages, through Hubzoid intake
hubzoid-desk list .                  # ten proposals, two questions
```

Open the web app, ask "What is in the desk queue?", and open a case: the chat shows the case page with each source line next to its proposal.

## Make it yours

- **Rules are files.** `knowledge/order-policy.md` and `knowledge/invoice-policy.md` hold the numbered rules every proposal cites. `knowledge/sku-aliases.csv` maps your customers' part numbers and pack sizes to your items. `knowledge/unit-words.csv` maps unit words to ERP units. See [docs/rule-authoring.md](docs/rule-authoring.md).
- **Corrections stay case-only.** Answering a question changes that case. A rule change is a reviewed edit of the files, with its source in `knowledge/sources/`. `hubzoid-desk rerun` matches a document again with the new rules.
- **Your ERP.** Point `connectors/erpnext.yaml` at your ERPNext site. See [docs/erpnext-setup.md](docs/erpnext-setup.md).

## Write modes

`work-items.yaml` sets `erp.submit`:

| Mode | What the desk may do |
|---|---|
| `off` | Prepare proposals. Nothing is written to the ERP. |
| `draft` | Save inert drafts (docstatus 0) for a person to finish in ERPNext. |
| `approve` | Also submit, only after the person who asked approves the exact draft. |

There is no automatic posting. A model, a document or a workflow can never approve.

## Safety by default

- Document text, sender names and ERP descriptions are untrusted data. Instructions inside them are flagged and ignored.
- A sender is bound to a customer or supplier by an administrator, never by what a message says.
- An invoice can never bill more than was received and not yet billed. A duplicate bill number or customer PO is never entered twice.
- Every ERP write is recorded before it is sent. A crash or a lost answer is reconciled from the ERP, never resent on a guess.
- The ERP integration user cannot submit. Submission uses a separate credential, only in approve mode.

## What is proven

| Claim | Evidence |
|---|---|
| Twelve sample documents give ten proposals and two questions | Real run on Hubzoid with Docling and the `claude-local` extractor, 2 min 19 s. Deterministic oracle tests. |
| Drafts, approved submission, denial, stale approvals, crash and lost-answer recovery | Tests on the real Hubzoid workflow engine. |
| The ERP adapter speaks ERPNext's REST protocol | The same contract suite on the sample backend and on a fake Frappe REST server with injected faults. |
| Live ERPNext | **Not yet run.** The kit is in [testing/erpnext/](testing/erpnext/README.md). |

## Files

```
AGENTS.md                       the desk assistant in chat
agents/desk-extractor/          the extraction station and its JSON schema
workflows/order_invoice/        the two work-item workflows
tools_local/order_invoice.py    chat tools (queue, case, claim, answer, draft, approval card)
knowledge/                      policies, alias rows, unit words, sources
connectors/erpnext.yaml         the ERP connection (sample by default)
work-items.yaml  hitl.yaml      write modes and the approval card policy
settings.yaml hub.settings.yaml desk settings
hub.manifest.yaml doctor/       the desk's doctor checks
desk/                           the desk service (pipeline, cases, CLI)
erp/                            the ERPNext module and the distributor-v1 dataset
evals/ tests/                   evals and tests
```

Documents: [operator guide](docs/operator-guide.md), [architecture](docs/ARCHITECTURE.md), [rule authoring](docs/rule-authoring.md), [ERPNext setup](docs/erpnext-setup.md), [errors](docs/errors.md).

## Supported documents and limits

PDF (text or scanned), PNG and JPEG images, XLSX and CSV spreadsheets, plain text and email bodies. Up to 20 MiB and 100 pages per file, 200 lines per draft, 180 seconds to parse one document. Encrypted PDFs and macro-enabled Office files are refused. Orders and supplier invoices only: credit notes, returns, payments and bank detail changes go to a person.

## Licence

Apache-2.0. ERPNext is a separate GPL-3.0 application, and none of its code is in this repository. Built on [Hubzoid](https://github.com/hubzoid/hubzoid).
