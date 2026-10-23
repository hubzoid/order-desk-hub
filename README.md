<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/hubzoid/hubzoid/72ac7bf1610c8a335b9b3ac3b536de8fdb6770f1/assets/mark-dark.svg">
    <img alt="Hubzoid" src="https://raw.githubusercontent.com/hubzoid/hubzoid/72ac7bf1610c8a335b9b3ac3b536de8fdb6770f1/assets/mark-light.svg" width="200">
  </picture>
</p>

<h1 align="center">Order &amp; Invoice Desk</h1>
<h3 align="center">Orders and invoices from your inbox, organized and sourced.</h3>
<p align="center">Every line tied to its source. A question instead of a guess. Reports without an ERP.</p>
<p align="center">
  <a href="https://youtu.be/GaONTBNsZdk">Watch the video</a> ·
  <a href="skills/setup/SKILL.md">Setup guide</a> ·
  <a href="docs/inbox-records.md">Records and reports</a> ·
  <a href="docs/erpnext-setup.md">ERPNext</a> ·
  <a href="https://github.com/hubzoid/hubzoid">Hubzoid</a>
</p>
<p align="center">
  <a href="https://github.com/hubzoid/hubzoid"><img src="https://img.shields.io/badge/Built%20on-Hubzoid-B5471F" alt="Built on Hubzoid"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-0B0B0C" alt="Apache License 2.0"></a>
</p>

**The Order & Invoice Desk is a ready-to-use Hubzoid hub that reads customer
orders and supplier invoices from your Gmail, turns each one into a sourced
record, and gives you reports, with no ERP required.** Anything the rules do not
decide comes back as a question for a person. Connect ERPNext when you want
master-data checks and reviewed drafts or submission.

<p align="center">
  <a href="https://youtu.be/GaONTBNsZdk"><img alt="Watch: from inbox to entry, with every line sourced" src="https://img.youtube.com/vi/GaONTBNsZdk/maxresdefault.jpg" width="640"></a>
</p>

## How it works for you

| Step | What the desk does |
|---|---|
| Orders and invoices arrive in Gmail | Reads email text, PDFs, images and spreadsheets from the mailbox or label you choose, including a bounded history import. |
| Each document is read | Becomes a versioned record: party, references, dates, currency, lines, taxes and totals, with field-level evidence from the source. |
| Something is unclear | A conflicting total, an unsupported tax basis or a possible duplicate becomes an exception for a person to resolve. |
| You review | Correct a field or accept a validated record. Every correction is a new revision, and the original extraction is kept. |
| You need the numbers | Four reports: a CSV register, order summary, invoice totals and exception aging. |

## Get your first useful result

You need Python 3.11 or later and Claude Code or Codex signed in on this
machine. Installing the desk installs Hubzoid 2.0.1 or later. Gmail uses an OAuth client you own.

```bash
git clone https://github.com/hubzoid/order-desk-hub order-desk
cd order-desk
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[parsing]'
hubzoid-desk parser prefetch
```

Then set it up with your coding assistant, from this folder:

```text
Read skills/setup/SKILL.md. Set up an inbox-only Order & Invoice Desk using my
installed runtime. Help me select a bounded business-mail query or existing
label and dates, inspect the proposed files and permissions, and connect my
customer-owned Gmail OAuth client. Keep secrets out of chat. Show useful records
and all four reports, resolve one exception, then propose a sourced reusable
correction and test it on a separate example. Do not enable ERP writes or send
email.
```

The [setup guide](skills/setup/SKILL.md) holds the exact commands: the setup
preview, the Gmail connection, the history import, access grants and the
correction loop. Email text, CSV and XLSX work without the `parsing` extra. PDFs
and images use it.

<details>
<summary>Try the labelled sample first</summary>

A fictional distributor, Synthetic Meridian Distribution, with orders and
invoices as email text, a PDF, a spreadsheet, a chat message and an image, and a
sample ERPNext backend:

```bash
hubzoid-desk sample install .
hubzoid-desk doctor .
HUBZOID_SCHEDULES=1 hubzoid run . --port 8711 --bridge-port 8712
```

In a second terminal, with the same environment activated:

```bash
hubzoid-desk sample load .
hubzoid-desk list .
```

The sample runs on Claude Code. For Codex, set `MODEL=codex-local` in `.env` for
chat, and `engine: codex-local` with a Codex model in
`agents/desk-extractor/AGENTS.md` for extraction.

Open the web app, ask *"What is in the desk queue?"*, and open a case. Each
source line sits beside its proposal and the rule that decided it. Two cases
come back as questions: an item that matches two products, and an invoice that
bills 50 when only 40 were received and not yet billed.

</details>

## Four reports

```bash
hubzoid-desk report . register --csv > orders-and-invoices.csv
hubzoid-desk report . orders --start 2026-10-01 --end 2026-11-01 --json
hubzoid-desk report . invoices --start 2026-10-01 --end 2026-11-01 --json
hubzoid-desk report . exceptions --json
```

In chat, or through the same tools over MCP, ask *"Show the invoice totals for
October"* or *"Export the register"*. Only the latest accepted revision of each
document counts toward totals. Unknown amounts, conflicting totals, duplicate
candidates and unreviewed records stay visible for review and out of the
totals. Currencies and original units stay separate. Money uses Decimal, with
exact reconciliation at stated precision. [How records and reports work →](docs/inbox-records.md)

## Make it yours

Your rules, aliases and unit conversions live in `knowledge/record-rules.md`,
with their sources in `knowledge/sources/`.

1. **Start from your examples.** Your coding assistant reads selected
   representative documents and your company rules, then proposes aliases,
   conversions and typed rules, each with its source.
2. **Test before you activate.** With the proposed rules in your working copy,
   run `hubzoid-desk preview . FILE --json` on separate holdout documents, and
   `hubzoid-desk rules check .` to validate the file.
3. **Save a correction.** In chat, `desk_propose_record_rules` prepares the exact
   change. On a writable hub it is stored as a pending Hub change; on read-only
   hosting it returns the diff for your builder checkout. The owner reviews and
   activates it, and new documents follow it.

A correction made on one case stays with that case until it is saved as a
shared rule.

## Add ERPNext

Set `DESK_ERP_ENABLED: true` in `hub.settings.yaml`, configure
`connectors/erpnext.yaml`, and follow [ERPNext setup](docs/erpnext-setup.md).
The desk then matches items by exact code or alias, checks prices, units and
receipts, and can write to ERPNext in the mode you choose in `work-items.yaml`:

| Mode | What the desk may do |
|---|---|
| `off` | Prepare proposals. Nothing is written to the ERP. |
| `draft` | Save inert drafts (docstatus 0) for a person to finish in ERPNext. |
| `approve` | Also submit, after the person who asked approves the exact draft. |

Every line keeps its source. Grants, rule revision, draft content and the ERP
version are rechecked at dispatch. The desk refuses an invoice that bills more
than was received and not yet billed, and a bill number or customer PO already
in ERPNext.
Every ERP write is recorded before it is sent, and an uncertain outcome is
reconciled from ERPNext, never resent on a guess.

## Safety

- Document text, sender names and descriptions are data. Instructions inside
  them are flagged and ignored.
- Senders are allowed by an explicit sender policy. Unknown senders are
  quarantined until a person releases the message.
- In inbox mode, each connected mailbox has its own access scope: a team member
  sees its records only with that mailbox's grant. With ERPNext on, cases are
  shared with everyone who has the `desk_view` permission.
- The desk sends no email and never posts to an ERP on its own. A model, a
  document or a workflow can never approve.

## The folder

```
AGENTS.md                       the desk assistant in chat
agents/desk-extractor/          the extraction station and its JSON schema
workflows/order_invoice/        the intake and case work-item workflows
tools_local/order_invoice.py    chat and MCP tools: queue, case, review, reports, rules
knowledge/                      record rules, ERP policies, aliases, unit words, sources
connectors/erpnext.yaml         the optional ERPNext connection
work-items.yaml  hitl.yaml      ERP write modes and the approval card policy
desk/                           records, reports, cases and the CLI
erp/                            the ERPNext adapter and the distributor-v1 sample dataset
skills/setup/                   the setup recipe for your coding assistant
evals/ tests/                   behaviour checks and the test suite
```

Records, operations and artifacts live in the Hubzoid data root
(`HUBZOID_DATA_DIR`). Back it up with the source revision you run.

## Go further

| Next step | Guide |
|---|---|
| Set up Gmail, import history and grant access | [Setup](skills/setup/SKILL.md) |
| Review records and read the reports | [Records and reports](docs/inbox-records.md) |
| Write and test company rules | [Rule authoring](docs/rule-authoring.md) |
| Connect ERPNext | [ERPNext setup](docs/erpnext-setup.md) |
| Run the desk day to day | [Operator guide](docs/operator-guide.md) |
| Look up an error | [Errors](docs/errors.md) |
| How it fits together | [Architecture](docs/ARCHITECTURE.md) |

The Order & Invoice Desk is **Apache-2.0**. ERPNext is a separate application,
and none of its code is in this repository. Built on [Hubzoid](https://github.com/hubzoid/hubzoid).
Need it running for your team? [Build with us](https://hubzoid.com/enterprise).
