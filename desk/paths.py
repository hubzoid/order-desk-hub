"""Where the desk keeps things, and how hub entry modules find this package."""
from __future__ import annotations

import sys
from pathlib import Path

HUB_ID_DEFAULT = "order-invoice-desk"


def ensure_importable(entry_file: str) -> Path:
    """Put the hub root (the folder holding `desk/` and `erp/`) on sys.path.
    The SDK imports workflow and tool files by path, so they call this first."""
    here = Path(entry_file).resolve()
    for parent in here.parents:
        if (parent / "desk" / "__init__.py").is_file() and (parent / "erp" / "__init__.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return parent
    raise RuntimeError(f"{entry_file} is not inside an Order & Invoice Desk hub")


def hub_key(hub_dir) -> str:
    """The SDK's hub key: the normalised folder name."""
    try:
        from hubzoid.assertions import hub_key as sdk_hub_key

        return sdk_hub_key(Path(hub_dir))
    except Exception:  # noqa: BLE001 - outside a Hubzoid install (unit tests)
        return Path(hub_dir).resolve().name.lower()


def check_data_root(hub_dir) -> None:
    from hubzoid.datadir import state
    from .store import StoreError

    hub = Path(hub_dir)
    legacy = hub / ".hubzoid" / "data"
    target = state(hub) / "data"
    if target.resolve() != legacy.resolve() and any((legacy / name).exists() for name in ("desk", "erp")):
        raise StoreError("data_root_migration_required", "Legacy desk/ERP state exists under .hubzoid/data. Stop workers, back up, then explicitly move existing desk and ERP state to the configured data root, preserving deployment-id, records and operation receipts. See docs/qualification.md.", status=409)


def data_dir(hub_dir) -> Path:
    from hubzoid.datadir import state

    check_data_root(hub_dir)
    path = state(Path(hub_dir)) / "data" / "desk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir(hub_dir, *parts) -> Path:
    from hubzoid.datadir import root

    path = root(Path(hub_dir)).joinpath("output", "desk", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path
