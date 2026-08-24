from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from erp import config as config_lib  # noqa: E402
from erp.fake_server import FakeServer  # noqa: E402
from erp.service import DATASETS_ROOT, ERPService  # noqa: E402

DATASET = DATASETS_ROOT / "distributor-v1"


def make_service(tmp_path: Path, backend: str = "sample", *, origin: str | None = None, writes: bool = True,
                 **changes) -> ERPService:
    hub = tmp_path / "hub"
    (hub / "connectors").mkdir(parents=True, exist_ok=True)
    data = dict(config_lib.SAMPLE_DEFAULT, writes_enabled=writes, **changes)
    if backend == "fake_rest":
        data.update(backend="fake_rest", site_url=origin)
    (hub / "connectors" / "erpnext.yaml").write_text(config_lib.dump(data))
    return ERPService(hub, hub / ".hubzoid" / "data" / "erp", deployment_id="test-deployment",
                      secret_resolver=lambda ref: None)


@pytest.fixture
def fake_site(tmp_path):
    server = FakeServer(DATASET, state_dir=tmp_path / "fake-state").start()
    yield server
    server.stop()


@pytest.fixture(params=["sample", "fake_rest"])
def erp_backend(request, tmp_path):
    """The same contract on both offline backends."""
    if request.param == "sample":
        yield make_service(tmp_path), None
    else:
        server = FakeServer(DATASET, state_dir=tmp_path / "fake-state").start()
        try:
            yield make_service(tmp_path, "fake_rest", origin=server.origin), server
        finally:
            server.stop()
