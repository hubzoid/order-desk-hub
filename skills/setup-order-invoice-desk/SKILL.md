---
name: setup-order-invoice-desk
description: Set up, check, demonstrate and personalize the Order & Invoice Desk hub (orders and supplier invoices to ERPNext drafts). For a builder's coding assistant (Claude Code or Codex), not for the desk's chat users.
---

# Setting up the Order & Invoice Desk

You are helping a builder set up this hub. Follow the steps in order, show what each one printed, and stop where a person must decide. Never ask for, read, print or type a secret value. Never connect a real ERP, mailbox or WhatsApp number, and never enable ERP writes or approve-mode submission, unless the builder asks for that exact step.

## 1. Sample first

```bash
pip install -e '.[parsing]'
hubzoid-desk parser prefetch            # Docling's model assets, a few minutes, once
hubzoid-desk sample install .           # labelled synthetic data, sender bindings, two sample channels
grep -q HUBZOID_SCHEDULES .env || echo 'HUBZOID_SCHEDULES=1' >> .env
hubzoid-desk doctor .                   # every check must be ok, except model access if not signed in
```

If doctor reports `desk.model` missing, the builder signs in to Claude Code (`claude login`). The sample, rules and ERP checks work without it, and cases wait at extraction.

## 2. The twelve documents

```bash
hubzoid run . --port 8711 --bridge-port 8712 --no-open &   # leave it running
hubzoid-desk sample load .
hubzoid-desk list .                     # repeat until no case is "received" (about 2 to 4 minutes)
```

Expect ten cases in `needs_owner` and two in `needs_clarification`: an ambiguous bolt (AH-3316) and a bill for more than was received (GCS-3003). Show `hubzoid-desk show . CASE-...` for one order and one invoice, and point the builder at the case page it writes (`output/desk/cases/`). These counts describe the synthetic fixture, not a measured accuracy.

## 3. Make it yours

Show `knowledge/order-policy.md`, `knowledge/invoice-policy.md`, `knowledge/sku-aliases.csv` and `knowledge/unit-words.csv`. For a correction such as "in our orders a carton of SYN-ITEM-001 is twelve units":

1. Quote the builder's words in a new note under `knowledge/sources/`.
2. Add exactly one row to `knowledge/sku-aliases.csv` citing that note. Show the diff.
3. `hubzoid-desk rules check .`
4. `hubzoid-desk rerun . CASE --reason "..."` on the document that asked. Show that only the affected line changed and that the earlier proposal revision is kept.

Never add a rule the builder's words or files do not state. Show unsupported assumptions as questions.

## 4. ERP drafts on the sample

`work-items.yaml` ships with `erp.submit: draft` on the sample backend. A person claims a case and saves its draft, in chat or with:

```bash
hubzoid-desk claim . CASE --expected-revision N
hubzoid-desk draft . CASE --expected-revision N --digest sha256:...
```

To demonstrate submission, the builder changes `erp.submit` to `approve` (show the diff first), then asks to submit with `hubzoid-desk submit-request`. Stop there: the builder approves in the chat card, the Console's Work Items screen, or `hubzoid operations approve`. You never approve.

## 5. A real ERPNext site

Follow `docs/erpnext-setup.md`. Keep `writes_enabled: false` until doctor shows the custom field and no draft-save hooks. Credentials go in with `hubzoid secrets set ... --stdin` by the person who holds them, never through you.

## When something fails

Every error prints a code and the next command. `docs/errors.md` lists them all. For a stopped work item, fix the cause, then `hubzoid work-items restart . ITEM --reason "..."`. An `ambiguous` case is reconciled with `hubzoid-desk reconcile . CASE` before anything else.
