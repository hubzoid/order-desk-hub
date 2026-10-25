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
  <a href="#quick-start">Quick start</a> ·
  <a href="skills/setup/SKILL.md">Setup guide</a> ·
  <a href="docs/inbox-records.md">Records and reports</a> ·
  <a href="https://github.com/hubzoid/hubzoid">Hubzoid</a>
</p>
<p align="center">
  <a href="https://github.com/hubzoid/hubzoid"><img src="https://img.shields.io/badge/Built%20on-Hubzoid-B5471F" alt="Built on Hubzoid"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-0B0B0C" alt="Apache License 2.0"></a>
  <a href="https://github.com/hubzoid/hubzoid/discussions"><img src="https://img.shields.io/badge/Community-Discussions-0B0B0C?logo=github" alt="GitHub Discussions"></a>
</p>

**The Order & Invoice Desk is a ready-to-use Hubzoid hub that reads customer orders and supplier invoices from your Gmail, turns each one into a sourced record, and gives you reports, with no ERP required.** Anything the rules do not decide comes back as a question for a person. Connect ERPNext when you want master-data checks and reviewed drafts or submission.

<p align="center">
  <a href="https://youtu.be/GaONTBNsZdk"><img alt="Watch: from inbox to entry, with every line sourced" src="https://img.youtube.com/vi/GaONTBNsZdk/maxresdefault.jpg" width="640"></a>
</p>

## How it works

- **Intake** reads email text, PDFs, images and spreadsheets from the mailbox or label you choose. Senders are allowed by an explicit sender policy. Unknown senders are quarantined until a person releases the message.
- **Extractor** turns each document into a versioned record: party, references, dates, currency, lines, taxes and totals, with field-level evidence from the source.
- **Rules** decide what they can: your aliases, unit conversions and policies, each with its source. A conflicting total, an unsupported tax basis or a possible duplicate becomes an exception for a person to resolve.
- **Review** is a person's step. Correct a field or accept a validated record. Every correction is a new revision, and the original extraction is kept.
- **Reports** count only the latest accepted revision of each document. Unknown amounts, conflicting totals, duplicate candidates and unreviewed records stay visible for review and out of the totals.

Every document is a work item. A restart resumes it from its last saved stage, and a saved stage never runs twice. [Architecture](docs/ARCHITECTURE.md)

## Quick start

You need Python 3.11 or later, Git, and Claude Code or Codex signed in on this machine. No API key. The quick start uses the labelled sample, so you need no Gmail and no ERP.

Paste this into Claude Code or Codex:

```text
Clone https://github.com/hubzoid/order-desk-hub into a folder called
order-desk, cd in, and follow skills/setup/SKILL.md to run the labelled sample.
Do not connect Gmail, enable ERP writes or send email.
```

Or by hand:

```bash
git clone https://github.com/hubzoid/order-desk-hub order-desk
cd order-desk
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[parsing]'
hubzoid-desk parser prefetch
```

`parser prefetch` downloads the model assets for PDFs and images, once. The sample runs on Claude Code. For Codex, set `MODEL=codex-local` in `.env` for chat, and `engine: codex-local` with a Codex model in `agents/desk-extractor/AGENTS.md` for extraction. Then load the sample and start the desk:

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

Run `hubzoid-desk list .` again until no case is "received", usually 2 to 4 minutes. The sample is a fictional distributor, Synthetic Meridian Distribution, with orders and invoices as email text, a PDF, a spreadsheet, a chat message and an image.

In the web chat at **http://127.0.0.1:8711**, ask:

> What is in the desk queue?

You see ten cases ready and two that came back as questions: an item that matches two products, and an invoice that bills 50 when only 40 were received and not yet billed. Open a case to see each source line beside its proposal and the rule that decided it.

<!-- Screenshot: a case page with each source line beside its proposal and the rule that decided it. -->

The [setup guide](skills/setup/SKILL.md) holds the same steps for your coding assistant, and the steps to accept records and run the reports on the sample.

## Integrations

| Works with | What the desk does |
|---|---|
| Gmail | Reads new orders and invoices from one business label or mailbox you choose, plus a bounded history import. You connect it with your own Google OAuth client. [Setup guide](skills/setup/SKILL.md) |
| ERPNext (optional) | Matches items by exact code or alias, checks prices, units and receipts, and saves drafts or submits them in the mode you choose. [ERPNext setup](docs/erpnext-setup.md) |
| The web chat, and Claude Code, ChatGPT and Codex over MCP | Answers from the records: *"What is in the desk queue?"*, *"Show the invoice totals for October"*, *"Export the register"*. |

Four reports come from the same records: a CSV register, an order summary, invoice totals and exception aging. [Records and reports](docs/inbox-records.md)

Ready for your own inbox? Ask your coding assistant to follow the [setup guide](skills/setup/SKILL.md). It previews every file change and the mail scope before applying anything, and you store the Google client secret yourself, never in chat. Email text, CSV and XLSX work without the `parsing` extra. PDFs and images use it.

## Make it yours

The files you will edit, and what each one changes:

```text
order-desk/
├── AGENTS.md                        the desk assistant in chat
├── agents/desk-extractor/AGENTS.md  how documents are read into records. Set engine: codex-local for Codex
├── knowledge/
│   ├── record-rules.md              your rules, aliases and unit conversions, each with its source
│   ├── sku-aliases.csv              the names your customers and suppliers use for your items, with unit factors
│   ├── unit-words.csv               the unit words in your documents
│   ├── order-policy.md              how orders are checked
│   ├── invoice-policy.md            how invoices are checked
│   └── sources/                     the source behind each rule
├── hub.settings.yaml                sources, intake, limits
├── connectors/erpnext.yaml          the optional ERPNext connection
└── work-items.yaml                  ERP write mode: off, draft or approve
```

Start from your examples. Your coding assistant reads representative documents and your company rules, then proposes aliases, conversions and typed rules, each with its source. Test before you activate: `hubzoid-desk preview . FILE --json` on separate holdout documents, and `hubzoid-desk rules check .` to validate the file.

To save a rule from chat, tell the desk *"In our orders, a carton of SYN-ITEM-001 is twelve units."* It prepares the exact change as a pending Hub change. The owner reviews and activates it, and new documents follow it. A correction made on one case stays with that case until it is saved as a shared rule. [Rule authoring](docs/rule-authoring.md)

## Safety

- **The desk sends no email and never posts to an ERP on its own.** A model, a document or a workflow can never approve. Writes to ERPNext wait for the mode you choose, and `approve` mode waits for the person who asked to approve the exact draft.
- **Each mailbox has its own access scope.** A team member sees its records only with that mailbox's grant. Senders never become members.
- **Documents are data, never instructions.** Document text, sender names and descriptions are read as data. Instructions inside them are flagged and ignored.

Money uses Decimal, with exact reconciliation at stated precision. Currencies and original units stay separate. Every ERP write is recorded before it is sent, and an uncertain outcome is reconciled from ERPNext, never resent on a guess. [ERPNext setup](docs/erpnext-setup.md)

## Host it for your team

The desk runs on a server the same way any Hubzoid hub does: one image built from this repository, one durable folder at `/data`, sign-in on, and one public address. Three things are specific to the desk:

- **`HUBZOID_SCHEDULES=1`**, so intake and case work run in the background.
- **`HUBZOID_PUBLIC_URL`**, your hub's HTTPS address, which the Google OAuth callback uses.
- **Your data root.** Records, operations and artifacts live in `HUBZOID_DATA_DIR`. Back it up with the source revision you run.

Grant each person the mailbox scopes they need, and nothing more. [Operator guide](docs/operator-guide.md) covers running the desk day to day. [Limits and recovery](docs/limits-and-recovery.md) covers limits, upgrades and recovery. [Hubzoid deployment](https://github.com/hubzoid/hubzoid/blob/main/docs/deploy/README.md) lists the hosting options and which are verified.

## Go further

| Next step | Guide |
|---|---|
| Set up Gmail, import history and grant access | [Setup](skills/setup/SKILL.md) |
| Review records and read the reports | [Records and reports](docs/inbox-records.md) |
| Write and test company rules | [Rule authoring](docs/rule-authoring.md) |
| Connect ERPNext | [ERPNext setup](docs/erpnext-setup.md) |
| Run the desk day to day | [Operator guide](docs/operator-guide.md) |
| Limits, upgrades and recovery | [Limits and recovery](docs/limits-and-recovery.md) |
| Look up an error | [Errors](docs/errors.md) |
| How it fits together | [Architecture](docs/ARCHITECTURE.md) |

Questions go to [GitHub Discussions](https://github.com/hubzoid/hubzoid/discussions). Bugs in the desk go to [this repository's issues](https://github.com/hubzoid/order-desk-hub/issues).

The Order & Invoice Desk is **Apache-2.0**. ERPNext is a separate application, and none of its code is in this repository. Built on [Hubzoid](https://github.com/hubzoid/hubzoid). Need it running for your team? [Build with us](https://hubzoid.com/build-with-us).
