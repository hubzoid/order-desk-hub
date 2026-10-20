from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier

from erp import config
from erp.service import ERPService


def test_concurrent_service_initialization_uses_one_persistent_deployment_id(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    state = tmp_path / "state"
    barrier = Barrier(4)
    original = os.link
    def simultaneous_link(source, target, *args, **kwargs):
        if Path(target).name == "deployment-id":
            barrier.wait(timeout=10)
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(os, "link", simultaneous_link)
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(lambda _: ERPService(hub, state).deployment_id, range(4)))
    assert len(set(ids)) == 1
    assert (state / "deployment-id").read_text() == ids[0]
    assert not list(state.glob(".deployment-id-*"))


def test_concurrent_first_config_read_returns_winning_revision(tmp_path, monkeypatch):
    hub, state = tmp_path / "hub", tmp_path / "state"
    (hub / "connectors").mkdir(parents=True)
    (hub / config.FILE).write_text(config.dump(config.SAMPLE_DEFAULT))
    barrier, original = Barrier(4), config.apply
    def simultaneous_apply(*args, **kwargs):
        if kwargs.get("expected_revision") == "absent":
            barrier.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(config, "apply", simultaneous_apply)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: config.current(hub, state), range(4)))
    assert {result.revision for result in results} == {1}
    assert len({result.digest for result in results}) == 1
