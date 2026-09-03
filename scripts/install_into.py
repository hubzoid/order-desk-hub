"""Install this hub into an existing hub folder (for example a copy of a test hub).

    python scripts/install_into.py TARGET [--from-copy SOURCE]

Copies the desk's hub files into TARGET. With --from-copy, TARGET is first
created as a copy of SOURCE without its runtime state (.hubzoid, logs,
output and the like). The source hub's own sample tools, workflows and evals
move to `TARGET/_previous/`, which Hubzoid does not load, so the desk's chat
agent sees only the desk's tools. `.env` and `identity/` are kept.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_FILES = ["AGENTS.md", "hub.manifest.yaml", "settings.yaml", "hub.settings.yaml", "work-items.yaml", "hitl.yaml",
             "README.md", "NOTICE", "LICENSE", "hub.lock.json"]
HUB_DIRS = ["agents", "desk", "erp", "knowledge", "workflows/order_invoice", "doctor", "connectors", "evals", "quality",
            "skills", "docs", "demo"]
TOOLS = ["tools_local/order_invoice.py"]
RUNTIME = {".hubzoid", ".openwebui-data", ".inbound", "logs", "output", "__pycache__", "raw_data"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def copy_hub(source: Path, target: Path) -> None:
    if target.exists():
        raise SystemExit(f"{target} exists. Choose a new run folder.")
    target.mkdir(parents=True)
    for entry in source.iterdir():
        if entry.name in RUNTIME:
            continue
        dest = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, ignore=IGNORE, symlinks=False)
        else:
            shutil.copy2(entry, dest)


def install(target: Path) -> list[str]:
    done = []
    previous = target / "_previous"
    for sub in ("tools_local", "workflows", "evals", "restricted", "schedule"):
        src = target / sub
        if src.is_dir():
            previous.mkdir(exist_ok=True)
            if (previous / sub).exists():
                shutil.rmtree(previous / sub)
            shutil.move(str(src), str(previous / sub))
            done.append(f"moved {sub}/ to _previous/")
    if (target / "AGENTS.md").exists():
        previous.mkdir(exist_ok=True)
        shutil.move(str(target / "AGENTS.md"), str(previous / "AGENTS.md"))
    for name in HUB_FILES:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, target / name)
            done.append(name)
    for name in HUB_DIRS:
        src = ROOT / name
        if src.is_dir():
            dest = target / name
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, ignore=IGNORE)
            done.append(name + "/")
    for name in TOOLS:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target / name)
        done.append(name)
    (target / "channels").mkdir(exist_ok=True)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--from-copy")
    args = ap.parse_args(argv)
    target = Path(args.target).resolve()
    if args.from_copy:
        copy_hub(Path(args.from_copy).resolve(), target)
    for line in install(target):
        print(line)
    print(f"installed into {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
