"""Register export through the real tool and SDK chat artifact contracts."""
from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import sys
from urllib.parse import parse_qs, urlsplit

from agents.tool_context import ToolContext
from hubzoid import _request_ctx, _signing, memory

from tests.desk_helpers import ROOT
from tests.test_inbox_workflow import inbox, normalize


def load_tools(monkeypatch, desk):
    spec = importlib.util.spec_from_file_location("order_report_tools_test", ROOT / "tools_local/order_invoice.py")
    tools = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, tools)
    spec.loader.exec_module(tools)
    monkeypatch.setattr(tools, "_HUB", desk.hub_dir)
    monkeypatch.setattr(tools, "_desk", lambda: desk)
    monkeypatch.setattr(tools, "_actor", lambda: "operator")
    monkeypatch.setenv("HUBZOID_HUB_DIR", str(desk.hub_dir))
    monkeypatch.delenv("HUBZOID_ARTIFACT_SECRET", raising=False)
    return tools


def invoke_report(tools):
    arguments = json.dumps({
        "kind": "register", "start": "", "end": "", "timezone": "UTC", "party": "", "csv_export": True,
    })
    context = ToolContext(context=None, tool_name=tools.desk_report.name,
                          tool_call_id="report-contract-test", tool_arguments=arguments)
    return json.loads(asyncio.run(tools.desk_report.on_invoke_tool(context, arguments)))


def test_register_tool_exports_safe_csv_and_signed_link_in_only_current_chat(tmp_path, monkeypatch):
    monkeypatch.delenv("HUBZOID_DATA_DIR", raising=False)
    desk = inbox(tmp_path)
    case = normalize(desk)
    desk.review_record("operator", case["case_id"], expected_revision=1, changes={"party": "=Alpha"}, accept=True)
    tools = load_tools(monkeypatch, desk)
    with _request_ctx.chat_scope("report-chat"):
        out = invoke_report(tools)
        surfaced = _request_ctx.drain_artifacts()
    filename = "order-invoice-register.csv"
    path = memory.chat_artifact_dir(desk.hub_dir, "report-chat") / filename
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    assert rows[0]["party"] == "'=Alpha"
    assert rows[0]["stated_total"] == "22.00"
    assert out["artifact"] == filename and "csv" not in out
    assert len(surfaced) == 1 and surfaced[0]["name"] == filename
    link = urlsplit(surfaced[0]["url"])
    assert link.path == "/artifacts/report-chat/" + filename
    query = parse_qs(link.query)
    assert _signing.verify_artifact_token("report-chat", filename, query["t"][0],
                                         (query.get("e") or [None])[0], hub_dir=desk.hub_dir)
    assert not _signing.verify_artifact_token("other-chat", filename, query["t"][0],
                                             (query.get("e") or [None])[0], hub_dir=desk.hub_dir)
    assert not (memory.chat_artifact_dir(desk.hub_dir, "other-chat") / filename).exists()


def test_register_tool_rechecks_source_scope_and_authentication(tmp_path, monkeypatch):
    monkeypatch.delenv("HUBZOID_DATA_DIR", raising=False)
    desk = inbox(tmp_path)
    normalize(desk)
    tools = load_tools(monkeypatch, desk)
    desk._grants = lambda actor, permission: permission == "desk_view"
    with _request_ctx.chat_scope("restricted-chat"):
        out = invoke_report(tools)
    assert out["rows"] == []
    path = memory.chat_artifact_dir(desk.hub_dir, "restricted-chat") / out["artifact"]
    assert list(csv.DictReader(io.StringIO(path.read_text()))) == []
    monkeypatch.setattr(tools, "_actor", lambda: None)
    with _request_ctx.chat_scope("anonymous-chat"):
        out = invoke_report(tools)
        assert _request_ctx.drain_artifacts() == []
    assert out["error"]["code"] == "unauthenticated"
    assert not (memory.chat_artifact_dir(desk.hub_dir, "anonymous-chat") / "order-invoice-register.csv").exists()
