"""Parse one PDF or image with Docling, in its own process.

    python -m desk.docling_worker FILE --max-pages N [--ocr]

Prints one JSON object: {"elements": [...], "pages": N, "parser": "docling <version>"}.
Run by `desk.documents` with a deadline, one document at a time, so a slow or
huge file is killed with everything it loaded and memory is returned after
every document. Remote services and downloads are off: model assets come
from the local cache (`hubzoid-desk parser prefetch` fills it).
"""
from __future__ import annotations

import json
import os
import sys


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--ocr", action="store_true")
    args = ap.parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from importlib.metadata import version

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = bool(args.ocr)
    opts.do_table_structure = True
    opts.enable_remote_services = False
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
                                                  InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)})
    result = converter.convert(args.path, max_num_pages=args.max_pages, raises_on_error=True)
    doc = result.document
    elements = []
    for item, _level in doc.iterate_items():
        prov = item.prov[0] if getattr(item, "prov", None) else None
        page = prov.page_no if prov else None
        if type(item).__name__ == "TableItem":
            for r, row in enumerate(item.data.grid):
                cells = [str(c.text or "").strip() for c in row]
                if any(cells):
                    elements.append({"kind": "row", "page": page, "row": r, "cells": cells, "text": " | ".join(cells)})
        else:
            text = str(getattr(item, "text", "") or "").strip()
            if text:
                elements.append({"kind": "text", "page": page, "text": text})
    pages = len(getattr(doc, "pages", {}) or {}) or None
    print(json.dumps({"elements": elements, "pages": pages, "parser": f"docling {version('docling')}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
