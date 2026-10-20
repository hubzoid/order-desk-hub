"""The desk's own doctor checks, run by `hubzoid doctor` (hub.manifest.yaml)."""
import sys
from pathlib import Path

_HUB = Path(__file__).resolve().parents[1]
if str(_HUB) not in sys.path:
    sys.path.insert(0, str(_HUB))


def _one(result):
    return {k: result.get(k) for k in ("status", "summary", "recovery", "details") if result.get(k) is not None}


def _safe(fn, context):
    from desk.store import StoreError
    try:
        return _one(fn(Path(context["hub_dir"])))
    except StoreError as exc:
        return {"status": "fail", "summary": exc.message, "details": {"code": exc.code}}


def rules(context):
    from desk.doctor import check_rules

    return _safe(check_rules, context)


def erp(context):
    from desk.doctor import check_erp

    return _safe(lambda hub: check_erp(hub)[0], context)


def write_mode(context):
    from desk.doctor import check_write_mode

    return _safe(check_write_mode, context)


def approval_card(context):
    from desk.doctor import check_safety

    return _one(check_safety(Path(context["hub_dir"])))


def intake(context):
    from desk.doctor import check_channels

    return _one(check_channels(Path(context["hub_dir"])))


def parser(context):
    from desk.doctor import check_parser

    return _one(check_parser())


def model(context):
    from desk.doctor import check_model

    return _one(check_model(Path(context["hub_dir"])))


def sdk(context):
    from desk.doctor import check_sdk

    return _one(check_sdk(Path(context["hub_dir"])))


def storage(context):
    from desk.doctor import check_storage

    return _one(check_storage(Path(context["hub_dir"])))
