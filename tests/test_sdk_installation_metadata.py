"""SDK pin diagnostics use PEP 610 metadata, never an unrelated parent Git repo."""
from __future__ import annotations

import json
import subprocess
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from desk.doctor import check_sdk

PIN = "f99a22406a65c1306a48485b8182f77be1ac71fb"


@pytest.fixture
def locked_hub(tmp_path):
    hub = tmp_path / "desk"
    hub.mkdir()
    (hub / "hub.lock.json").write_text(json.dumps({"sdk": {"commit": PIN}}))
    return hub


def installed(monkeypatch, value):
    raw = json.dumps(value) if isinstance(value, (dict, list)) else value
    monkeypatch.setattr(metadata, "distribution", lambda name: SimpleNamespace(read_text=lambda path: raw))


def no_git(*args, **kwargs):
    raise AssertionError("Git must not run for missing or non-editable installation metadata")


def editable_source(tmp_path, monkeypatch):
    import hubzoid

    root = tmp_path / "sdk"
    package = root / "hubzoid"
    package.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="hubzoid"\n')
    init = package / "__init__.py"
    init.write_text("")
    monkeypatch.setattr(hubzoid, "__file__", str(init))
    installed(monkeypatch, {"url": root.as_uri(), "dir_info": {"editable": True}})
    return root


def test_noneditable_vcs_install_verifies_exact_resolved_pin_without_git(locked_hub, monkeypatch):
    installed(monkeypatch, {"url": "https://github.com/hubzoid/hubzoid.git",
                            "vcs_info": {"vcs": "git", "commit_id": PIN, "requested_revision": PIN}})
    monkeypatch.setattr(subprocess, "run", no_git)
    result = check_sdk(locked_hub)
    assert result["status"] == "ok"
    assert result["details"] == {"expected": PIN, "actual": PIN, "source": "pep610_vcs"}


def test_mismatched_vcs_metadata_is_a_readable_failure(locked_hub, monkeypatch):
    installed(monkeypatch, {"vcs_info": {"vcs": "git", "commit_id": "a" * 40}})
    monkeypatch.setattr(subprocess, "run", no_git)
    result = check_sdk(locked_hub)
    assert result["status"] == "fail" and "differs" in result["summary"]
    assert result["details"]["expected"] == PIN and result["details"]["actual"] == "a" * 40


@pytest.mark.parametrize("value", [None, "{broken", {}, [], {"vcs_info": {}}, {"dir_info": {"editable": False}}])
def test_missing_or_invalid_metadata_warns_without_guessing_a_git_parent(locked_hub, monkeypatch, value):
    installed(monkeypatch, value)
    monkeypatch.setattr(subprocess, "run", no_git)
    result = check_sdk(locked_hub)
    assert result["status"] == "fail" and "cannot be verified" in result["summary"]
    assert result["details"]["actual"] is None and result["recovery"]


def test_unreadable_distribution_metadata_warns(locked_hub, monkeypatch):
    def unavailable(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", unavailable)
    monkeypatch.setattr(subprocess, "run", no_git)
    assert check_sdk(locked_hub)["status"] == "fail"


def test_editable_install_checks_its_own_source_git_root(locked_hub, tmp_path, monkeypatch):
    root = editable_source(tmp_path, monkeypatch)
    calls = []

    def git(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(root) if command[-1] == "--show-toplevel" else PIN)

    monkeypatch.setattr(subprocess, "run", git)
    result = check_sdk(locked_hub)
    assert result["status"] == "ok" and result["details"]["source"] == "editable_git"
    assert len(calls) == 2 and all(kwargs["cwd"] == root for _, kwargs in calls)


def test_editable_package_inside_unrelated_parent_git_is_not_verified(locked_hub, tmp_path, monkeypatch):
    root = editable_source(tmp_path, monkeypatch)
    (root / ".git").rmdir()
    (root.parent / ".git").mkdir()
    monkeypatch.setattr(subprocess, "run", no_git)
    result = check_sdk(locked_hub)
    assert result["status"] == "fail" and "unrelated parent" in result["summary"]


def test_editable_metadata_does_not_override_actual_imported_sdk_worktree(locked_hub, tmp_path, monkeypatch):
    root = editable_source(tmp_path, monkeypatch)
    installed(monkeypatch, {"url": (root.parent / "original-sdk").as_uri(), "dir_info": {"editable": True}})
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
                        SimpleNamespace(returncode=0, stdout=str(root) if command[-1] == "--show-toplevel" else PIN))
    assert check_sdk(locked_hub)["status"] == "ok"


def test_git_missing_for_editable_source_is_a_readable_warning(locked_hub, tmp_path, monkeypatch):
    editable_source(tmp_path, monkeypatch)

    def absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", absent)
    result = check_sdk(locked_hub)
    assert result["status"] == "fail" and "Git is unavailable" in result["summary"]


def test_git_reports_unrelated_root_even_with_editable_marker(locked_hub, tmp_path, monkeypatch):
    root = editable_source(tmp_path, monkeypatch)
    calls = []

    def wrong_root(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=str(root.parent))

    monkeypatch.setattr(subprocess, "run", wrong_root)
    result = check_sdk(locked_hub)
    assert result["status"] == "fail" and "not the reported Git root" in result["summary"]
    assert len(calls) == 1


def test_invalid_editable_url_warns_without_git(locked_hub, monkeypatch):
    installed(monkeypatch, {"url": "file://[invalid", "dir_info": {"editable": True}})
    monkeypatch.setattr(subprocess, "run", no_git)
    assert check_sdk(locked_hub)["status"] == "fail"
