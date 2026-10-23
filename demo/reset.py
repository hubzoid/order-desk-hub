"""Reset a demo hub to its starting state.

    python demo/reset.py HUB [--yes]

Refuses unless the ERP configuration in force (the applied one, and the
connectors/erpnext.yaml file) is the synthetic sample backend, and unless no
Hubzoid server is serving the hub. Removes the runtime data where it really
lives (the hub's data root: .hubzoid/ and output/, under HUBZOID_DATA_DIR
when that is set), raw_data/ and the two sample channel files. Hub files
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


def _backends(hub: Path) -> set[str]:
    """The backend in the file and the one applied (in force), when there is one."""
    from hubzoid.datadir import state

    from erp import config as config_lib

    found = {(yaml.safe_load((hub / "connectors" / "erpnext.yaml").read_text()) or {}).get("backend")}
    state_dir = state(hub) / "data" / "erp"
    if state_dir.is_dir():
        applied = config_lib.current(hub, state_dir)
        if applied is not None:
            found.add(applied.config.backend)
    return found


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
    sys.path.insert(0, str(hub))
    from hubzoid.datadir import root, state

    if _backends(hub) != {"sample"}:
        print("refused: the ERP connection is not the synthetic sample backend. A reset never touches a real ERP.")
        return 3
    if _serving(args.port):
        print(f"refused: something is serving on 127.0.0.1:{args.port}. Stop hubzoid run first.")
        return 4
    data = root(hub)
    if data in (Path(data.anchor), Path.home()) or hub.is_relative_to(data) and data != hub:
        print(f"refused: the data root {data} holds more than this hub's data.")
        return 5
    targets = [state(hub), data / "output", hub / "raw_data", hub / "channels" / "sample-email.yaml",
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
