"""Record the parsed source elements of every Order & Invoice Desk fixture.

    python scripts/record_elements.py            # writes evals/fixtures/elements/<event>.json

Runs the real parsers (Docling for PDFs and images, openpyxl for sheets), one
document at a time. The scripted tests and evals then replay these elements
without Docling. Heavy: take the shared heavy lock first on a shared machine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desk import documents as docs  # noqa: E402

DATASET = ROOT / "erp" / "datasets" / "distributor-v1"
OUT = ROOT / "evals" / "fixtures" / "elements"


def envelopes():
    for name in ("order-invoice-desk.jsonl", "order-invoice-desk-extras.jsonl", "order-invoice-desk-adversarial.jsonl"):
        for line in (DATASET / "messages" / name).read_text().splitlines():
            yield json.loads(line)


def main(argv=None) -> int:
    only = set((argv if argv is not None else sys.argv[1:]))
    OUT.mkdir(parents=True, exist_ok=True)
    for env in envelopes():
        if only and env["provider_event_id"] not in only:
            continue
        parsed = [docs.message_elements(env["text"], label="message")]
        problems = []
        for att in env["attachments"]:
            data = (DATASET / "attachments" / att["filename"]).read_bytes()
            try:
                parsed.append(docs.parse_file(data, att["filename"], att["media_type"]))
            except docs.ParseError as exc:
                problems.append({"document": att["filename"], "code": exc.code, "message": exc.message})
        record = {"event": env["provider_event_id"], "elements": docs.number(parsed), "problems": problems,
                  "parsers": [p.parser for p in parsed]}
        (OUT / f"{env['provider_event_id']}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
        print(env["provider_event_id"], len(record["elements"]), "elements", [p.parser for p in parsed],
              problems[0]["code"] if problems else "", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
