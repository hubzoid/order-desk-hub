# The one-prompt demo

## For Claude Code or Codex

Open this hub's folder in Claude Code or Codex and paste:

> Follow skills/setup/SKILL.md, section "Try the labelled sample", in this folder. Run the twelve sample documents, show me the queue, one order and one invoice with every line's source, and the two questions. Accept one order record and one invoice record and show me both reports with their totals. Then save the ERP draft for the HF-7010 order on the sample backend. Stop before any real ERP connection, mailbox or submission, and tell me the time each step took.

Expected: ten cases ready, two questions, two accepted records counted in the reports, one sample ERP draft (docstatus 0), nothing sent anywhere. From loading the twelve messages to the last case takes a few minutes (about two and a half with Docling and the `claude-local` extractor on a MacBook Air). Setup time depends on installing Docling and fetching its model assets once.

For your own mail without an ERP, use the prompt in the README instead.

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

Stop `hubzoid run` first, then `python demo/reset.py . --yes`. It refuses unless the ERP configuration in force is the sample backend, then removes the hub's runtime data (`.hubzoid/` and `output/`, in the data root when `HUBZOID_DATA_DIR` is set, and `raw_data/`) and the sample channel files, so the next run starts clean and gives the same result.
