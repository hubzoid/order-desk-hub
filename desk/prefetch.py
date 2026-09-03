"""Fetch Docling's model assets once and warm the OCR engine, so parsing can
run offline afterwards. `hubzoid-desk parser prefetch` runs this."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def main() -> int:
    import os
    from importlib.metadata import version

    os.environ["HF_HUB_OFFLINE"] = "0"

    from docling.utils.model_downloader import download_models

    path = download_models(progress=False)
    from PIL import Image, ImageDraw, ImageFont

    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "warm.png"
        image = Image.new("RGB", (480, 80), "white")
        ImageDraw.Draw(image).text((10, 20), "SYNTHETIC 12 x SYN-ITEM-001", fill="black",
                                   font=ImageFont.load_default(size=28))
        image.save(img)
        from desk.docling_worker import main as worker

        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            worker([str(img), "--ocr"])
    print(json.dumps({"ok": True, "parser": f"docling {version('docling')}", "models": str(path)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
