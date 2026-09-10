# The one-prompt demo

## For Claude Code or Codex

Open this hub's folder in Claude Code or Codex and paste:

> Set up the Order & Invoice Desk in this folder on the synthetic sample, following skills/setup-order-invoice-desk/SKILL.md. Run the twelve sample documents, show me the queue, one order and one invoice with every line's source, and the two questions. Then save the ERP draft for the HF-7010 order on the sample backend. Stop before any real ERP connection or submission, and tell me the time each step took.

Expected: ten proposals, two questions, one sample ERP draft (docstatus 0), nothing sent anywhere. The recorded run on 20 Oct 2026 took 2 minutes 19 seconds from loading the twelve messages to the last case (Docling and the `claude-local` extractor on a MacBook Air). Setup time depends on installing Docling and fetching its model assets once.

## The same steps by hand

```bash
pip install -e '.[parsing]' && hubzoid-desk parser prefetch
hubzoid-desk sample install . && echo 'HUBZOID_SCHEDULES=1' >> .env && hubzoid-desk doctor .
hubzoid run . --port 8711 --bridge-port 8712 --no-open &
hubzoid-desk sample load . && hubzoid-desk list .
```

## The correction moment

> In our orders, carton means twelve units for SKU SYN-ITEM-001 only. Save that rule and rerun the same document.

Load the document that needs it first: `hubzoid-desk sample load . --set extras` (HF-7012 asks how many units are in a carton).

## Reset

`python demo/reset.py .` stops nothing by itself: stop `hubzoid run` first. It refuses unless the ERP connection is the sample backend, then removes the hub's runtime data (`.hubzoid/`, `output/`, `raw_data/`) and the sample channel files, so the next run starts clean and gives the same result.
