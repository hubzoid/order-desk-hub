# Call script: the Order & Invoice Desk in ten minutes

For a founder or partner showing the desk to a distributor's owner, an
operations lead or an ERP implementer. Everything on screen is the labelled
synthetic company Synthetic Meridian Distribution on the sample ERP backend.
Nothing reaches a real ERP.

## Before the call (15 minutes, once)

1. Reset and start the hub. Do not load the messages yet.

   ```bash
   python demo/reset.py . --yes
   hubzoid-desk sample install .
   hubzoid-desk doctor .
   hubzoid run . --port 8711 --bridge-port 8712 --no-open
   ```

2. Check `connectors/erpnext.yaml` says `backend: sample` and `work-items.yaml`
   has `erp.submit: draft`. Doctor must say every desk check is ok.
3. Open the web app at `http://127.0.0.1:8711` in one tab and the Console's Work
   items screen in a second tab. Keep Claude Code (or Codex) open in the hub's
   folder for the correction moment.
4. Restore the rule files from git, so the correction moment starts clean:
   `git checkout -- knowledge/`.

## On the call

**0:00 The problem (one minute).** Ask how orders and supplier invoices reach
them today and who types them into the ERP. Say what the desk does in one line:
it reads the messy inbox and leaves drafts in ERPNext, with every value tied to
its source, and asks when it is not sure.

**1:00 Load the inbox (two to three minutes).** Run `hubzoid-desk sample load .`
in a terminal they can see. Twelve messages arrive through Hubzoid intake: email
orders, a PDF, a spreadsheet, a WhatsApp message, a photo of a handwritten list
and supplier invoices. While they parse, open one source document and point at
the mess: a customer part number instead of our SKU, "ctn", a total with a typo.

**4:00 The queue (one minute).** In chat, ask "What is in the desk queue?" Ten
cases are ready for an owner and two have a question. Read the two questions out:

- "Synthetic Hex Bolt M8" matches two items. Which one?
- The invoice bills 50 floor cleaners, but only 40 were received and not yet
  billed.

Say: these are the cases a careless tool would have guessed. The desk asks.

**5:00 One order, line by line (two minutes).** Ask to see the HF-7010 order
(its case number is in the queue). Point at one line: the source text, the rule that matched it (an exact code or an
alias row), the unit conversion and the price list rate. Money is exact to the
cent. Then claim the case and say "Save the ERP draft." Show the draft in the
case page: docstatus 0, nothing posted, the customer's ERPNext would show it as a
draft for a person to finish.

**7:00 The correction moment (two minutes).** Run
`hubzoid-desk sample load . --set extras`. The new order (HF-7012) asks: "How
many Nos are in one carton?" The chat assistant will not invent the answer, and
it will not turn a case answer into a rule. A rule is a reviewed edit of the hub's
files, so switch to Claude Code in the hub's folder and say:

> In our orders, carton means twelve units for SKU SYN-ITEM-001 only. Save that
> rule and rerun the same document.

Claude Code follows the setup skill: it quotes your words in a note under
`knowledge/sources/`, adds one row to `knowledge/sku-aliases.csv`, shows the diff,
checks the rules and reruns the case. The order now proposes 24 Nos at 9.80, and
the earlier proposal is kept. Say: the rule is a file you can read, review and
undo. It applies to the next order too, after a restart.

**9:00 Who decides what gets posted (one minute).** Show `work-items.yaml`:
`off`, `draft` or `approve`. In approve mode the person who asked approves the
exact document, its lines, totals and ERP version. A model, a document or a
workflow can never approve. Close with the two paths: try it yourself on the
sample, or have us run it for you on your ERPNext.

## Questions people ask

- **Which ERPs?** ERPNext today. Others are plans, not support.
- **What does it need from ERPNext?** One integration user that can read the
  listed records and create two draft types, one custom field installed by an
  administrator, and a separate user for submission if they turn on approve
  mode. See `docs/erpnext-setup.md`.
- **Which model reads the documents?** The desk-extractor station. On this demo
  it is the presenter's own Claude Code login. A customer chooses theirs.
- **What if the answer from ERPNext is lost?** The draft carries a unique marker,
  so the desk checks whether it exists before trying again. It never resends on
  a guess.

## Rehearsal record

| Date | Presenter | Total time | Notes |
|---|---|---|---|
| | | | Rehearse once before the first call and fill this in. |
