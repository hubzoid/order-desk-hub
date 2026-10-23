"""demo/reset.py trusts the ERP configuration in force, not only the file,
and removes runtime data where the SDK keeps it."""
from __future__ import annotations

import importlib.util
import socket

import pytest

from erp import config as config_lib
from tests.conftest import ROOT, make_service
from tests.desk_helpers import make_hub


def _reset():
    spec = importlib.util.spec_from_file_location("demo_reset", ROOT / "demo" / "reset.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_reset_clears_the_configured_data_root(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setenv("HUBZOID_DATA_DIR", str(data))
    (data / ".hubzoid" / "data" / "desk").mkdir(parents=True)
    (data / "output" / "desk").mkdir(parents=True)
    assert _reset()([str(hub), "--yes", "--port", str(_free_port())]) == 0
    assert not (data / ".hubzoid").exists() and not (data / "output").exists()
    assert (hub / "knowledge").is_dir()                       # hub files stay


def test_reset_refuses_when_a_real_erp_is_applied(tmp_path, monkeypatch):
    monkeypatch.delenv("HUBZOID_DATA_DIR", raising=False)
    svc = make_service(tmp_path, "fake_rest", origin="http://127.0.0.1:9")
    svc.applied()                                            # fake_rest is now the configuration in force
    hub = svc.hub_dir
    (hub / "connectors" / "erpnext.yaml").write_text(config_lib.dump(dict(config_lib.SAMPLE_DEFAULT)))
    assert _reset()([str(hub), "--yes", "--port", str(_free_port())]) == 3
    assert (hub / ".hubzoid").exists()
