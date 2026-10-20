"""The desk's own doctor checks, run by `hubzoid doctor` (hub.manifest.yaml)."""
import sys
from pathlib import Path

_HUB = Path(__file__).resolve().parents[1]
if str(_HUB) not in sys.path:
    sys.path.insert(0, str(_HUB))


def _one(result):
    return {k: result.get(k) for k in ("status", "summary", "recovery", "details") if result.get(k) is not None}


def rules(context):
    from desk.doctor import check_rules

    return _one(check_rules(Path(context["hub_dir"])))


def erp(context):
    from desk.doctor import check_erp

    return _one(check_erp(Path(context["hub_dir"]))[0])


def write_mode(context):
    from desk.doctor import check_write_mode

    return _one(check_write_mode(Path(context["hub_dir"])))


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
