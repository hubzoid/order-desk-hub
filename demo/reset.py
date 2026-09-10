"""Reset a demo hub to its starting state.

    python demo/reset.py HUB [--yes]

Refuses unless connectors/erpnext.yaml is the synthetic sample backend, and
unless no Hubzoid server is serving the hub. Removes the runtime data
(.hubzoid/, output/, raw_data/) and the two sample channel files. Hub files
(rules, settings, the station, the workflows) are kept, so a correction you
saved stays; restore knowledge/ from git to undo it too.
"""
from __future__ import annotations

import argparse
import shutil
import socket
import sys
from pathlib import Path

import yaml


def _serving(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hub")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--port", type=int, default=8711)
    args = ap.parse_args(argv)
    hub = Path(args.hub).resolve()
    cfg = yaml.safe_load((hub / "connectors" / "erpnext.yaml").read_text()) or {}
    if cfg.get("backend") != "sample":
        print("refused: the ERP connection is not the synthetic sample backend. A reset never touches a real ERP.")
        return 3
    if _serving(args.port):
        print(f"refused: something is serving on 127.0.0.1:{args.port}. Stop hubzoid run first.")
        return 4
    targets = [hub / ".hubzoid", hub / "output", hub / "raw_data", hub / "channels" / "sample-email.yaml",
               hub / "channels" / "sample-whatsapp.yaml"]
    present = [t for t in targets if t.exists()]
    if not args.yes:
        print("Would remove:\n  " + "\n  ".join(str(t) for t in present) + "\nRun again with --yes.")
        return 0
    for t in present:
        shutil.rmtree(t) if t.is_dir() else t.unlink()
    print("Reset. Next: hubzoid-desk sample install . then hubzoid run and hubzoid-desk sample load .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
