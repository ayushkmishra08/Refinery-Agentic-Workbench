"""The named local tools and the tool-using agent loop."""
from __future__ import annotations

import json

import pytest

from workbench.llm.fake import FakeLLM
from workbench.sandbox import SandboxLimits, SandboxRunner
from workbench.sovereignty.hashchain import HashChainedLog
from workbench.tools import ToolContext, default_registry
from workbench.tools.agent_loop import ToolAgent, ToolDecision, plan_deterministically
from workbench.tools.calculator_tool import evaluate
from workbench.tools.base import ToolError


@pytest.fixture
def ctx(tmp_path, mock_knowledge):
    sandbox = SandboxRunner(tmp_path / "sbx", SandboxLimits(timeout_seconds=6, memory_mb=128), backend="subprocess")
    return ToolContext(session_key="tester__t", workspace=tmp_path / "ws", knowledge=mock_knowledge,
                       sandbox=sandbox, log=HashChainedLog(tmp_path / "tools.jsonl"))


@pytest.fixture
def reg():
    return default_registry()


def test_registry_holds_the_named_tools(reg):
    assert set(reg.names()) == {"read_file", "write_file", "list_files", "run_python", "spreadsheet_read",
                                "spreadsheet_write", "search_documents", "calculate"}
    for d in reg.describe():
        assert d["description"] and d["parameters"]["type"] == "object"


# ------------------------------------------------------------------ files
def test_file_tools_roundtrip_and_listing(reg, ctx):
    w = reg.get("write_file")({"path": "notes/a.txt", "content": "hello"}, ctx)
    assert w.ok and w.files == ["notes/a.txt"] and len(w.data["sha256"]) == 64
    r = reg.get("read_file")({"path": "notes/a.txt"}, ctx)
    assert r.ok and r.output == "hello"
    a = reg.get("write_file")({"path": "notes/a.txt", "content": " world", "append": True}, ctx)
    assert a.ok and reg.get("read_file")({"path": "notes/a.txt"}, ctx).output == "hello world"
    ls = reg.get("list_files")({}, ctx)
    assert ls.ok and ls.data["count"] == 1 and ls.data["files"][0]["path"] == "notes/a.txt"
    missing = reg.get("read_file")({"path": "nope.txt"}, ctx)
    assert not missing.ok and "no such file" in missing.error


def test_file_tools_are_confined_to_the_workspace(reg, ctx, tmp_path):
    for bad in ("../escape.txt", "a/../../escape.txt", str(tmp_path / "abs.txt"), "/etc/passwd", "C:\\Windows\\x.txt"):
        res = reg.get("write_file")({"path": bad, "content": "x"}, ctx)
        assert not res.ok, bad
        assert "not allowed" in res.error or "escapes" in res.error, bad
        res = reg.get("read_file")({"path": bad}, ctx)
        assert not res.ok, bad
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "abs.txt").exists()


def test_argument_validation(reg, ctx):
    res = reg.get("write_file")({"path": "x.txt"}, ctx)
    assert not res.ok and "missing required argument 'content'" in res.error
    res = reg.get("calculate")({"expression": 12}, ctx)          # coerced to a string
    assert res.ok and res.data["result"] == 12


# ------------------------------------------------------------------ calculator
def test_calculator_shows_every_step(reg, ctx):
    res = reg.get("calculate")({"expression": "(482-219)/482*100"}, ctx)
    assert res.ok
    assert res.data["result"] == pytest.approx(54.5643, abs=1e-4)
    assert res.steps[0] == "482 - 219 = 263"
    assert "263 / 482 = 0.5456" in res.steps
    assert res.steps[-1] == "result: (482-219)/482*100 = 54.5643"
    assert res.output.count("\n") == len(res.steps) - 1


def test_calculator_substitutes_variables_and_converts_units(reg, ctx):
    res = reg.get("calculate")({"expression": "(actual - normal) / normal * 100",
                                "variables": {"actual": 520, "normal": 482}, "precision": 2}, ctx)
    assert res.ok
    assert res.steps[0] == "substitute: (520 - 482) / 482 * 100"
    assert res.data["result"] == pytest.approx(7.88, abs=0.01)
    conv = reg.get("calculate")({"expression": "24.45", "from_unit": "kg/cm2a", "to_unit": "bar"}, ctx)
    assert conv.ok and conv.data["unit"] == "bar" and conv.data["converted"] == pytest.approx(23.98, abs=0.01)
    assert conv.steps[-1].startswith("convert: 24.45 kg/cm2a = ")
    bad = reg.get("calculate")({"expression": "1", "from_unit": "kg/cm2", "to_unit": "m3/h"}, ctx)
    assert not bad.ok and "cannot convert" in bad.error


def test_calculator_refuses_anything_but_arithmetic(reg, ctx):
    for expr in ("__import__('os')", "open('x')", "a.b", "x[0]", "1 if 1 else 2", "lambda: 1", "'abc'", "2 < 3"):
        res = reg.get("calculate")({"expression": expr}, ctx)
        assert not res.ok, expr
    res = reg.get("calculate")({"expression": "flow * 2"}, ctx)
    assert not res.ok and "unknown name 'flow'" in res.error
    res = reg.get("calculate")({"expression": "1/0"}, ctx)
    assert not res.ok and "division by zero" in res.error
    with pytest.raises(ToolError):
        evaluate("2 ** 100000")
    val, steps = evaluate("sqrt(16) + max(1, 2)")
    assert val == 6 and steps[0] == "sqrt(16) = 4"


# ------------------------------------------------------------------ spreadsheets
def test_spreadsheet_write_then_read_keeps_formulas(reg, ctx):
    w = reg.get("spreadsheet_write")({"path": "calc.xlsx", "sheet": "Pump", "header": ["parameter", "value", "unit"],
                                      "rows": [["normal flow", 482, "m3/h"], ["actual flow", 520, "m3/h"],
                                               ["deviation %", "=(B3-B2)/B2*100", "%"]]}, ctx)
    assert w.ok and w.files == ["calc.xlsx"]
    assert w.data["formulas"] == [{"cell": "B4", "value": "=(B3-B2)/B2*100"}]
    e = reg.get("spreadsheet_write")({"path": "calc.xlsx", "sheet": "Pump", "cells": [{"cell": "B3", "value": 530}]}, ctx)
    assert e.ok and e.data["changed"] == [{"cell": "B3", "value": 530}]
    r = reg.get("spreadsheet_read")({"path": "calc.xlsx", "sheet": "Pump"}, ctx)
    assert r.ok
    assert r.data["rows"][0] == ["parameter", "value", "unit"]
    assert r.data["rows"][2][1] == 530
    assert r.data["formulas"][0]["cell"] == "B4" and r.data["formulas"][0]["formula"] == "=(B3-B2)/B2*100"
    assert r.data["sheets"] == ["Pump"]
    bad = reg.get("spreadsheet_read")({"path": "calc.xlsx", "sheet": "Nope"}, ctx)
    assert not bad.ok and "no sheet" in bad.error
    ap = reg.get("spreadsheet_write")({"path": "calc.xlsx", "sheet": "Pump", "rows": [["design flow", 520, "m3/h"]], "append": True}, ctx)
    assert ap.ok and ap.data["changed"][0]["cell"] == "A5"


# ------------------------------------------------------------------ document search
def test_search_documents_returns_evidence(reg, ctx):
    res = reg.get("search_documents")({"query": "crude charge pump", "k": 5}, ctx)
    assert res.ok and res.data["hits"] >= 1
    assert all({"document_id", "page", "chunk_id", "text"} <= set(e) for e in res.evidence)
    claims = reg.get("search_documents")({"query": "flow", "kind": "claims"}, ctx)
    assert claims.ok and claims.data["hits"] >= 1 and claims.evidence[0]["kind"] == "claim"
    procs = reg.get("search_documents")({"query": "start", "kind": "procedures"}, ctx)
    assert procs.ok and procs.evidence and procs.evidence[0]["kind"] == "procedure"
    bad = reg.get("search_documents")({"query": "x", "kind": "emails"}, ctx)
    assert not bad.ok
    ctx.knowledge = None
    none = reg.get("search_documents")({"query": "x"}, ctx)
    assert not none.ok and "no knowledge service" in none.error


# ------------------------------------------------------------------ sandboxed code
def test_run_python_uses_the_sandbox_and_keeps_artifacts(reg, ctx):
    res = reg.get("run_python")({"code": "print(6*7)\nopen('out.txt','w').write('done')",
                                 "tests": "def test_out():\n    assert open('out.txt').read() == 'done'\n"}, ctx)
    assert res.ok, res.output
    assert "42" in res.output and res.data["verified"] is True
    assert res.files and res.files[0].endswith("out.txt")
    assert (ctx.workspace / res.files[0]).read_text() == "done"
    blocked = reg.get("run_python")({"code": "import subprocess"}, ctx)
    assert not blocked.ok and "not permitted" in blocked.output
    ctx.sandbox = None
    none = reg.get("run_python")({"code": "print(1)"}, ctx)
    assert not none.ok and "no sandbox" in none.error


# ------------------------------------------------------------------ the loop
def test_deterministic_plans():
    assert plan_deterministically("calculate 2*(3+4)") == [("calculate", {"expression": "2*(3+4)"})]
    assert plan_deterministically("Search for desalter voltage in the documents")[0][0] == "search_documents"
    assert plan_deterministically("run this python: print(1)") == [("run_python", {"code": "print(1)"})]
    assert plan_deterministically("write 'hi' to notes.txt") == [("write_file", {"path": "notes.txt", "content": "hi"})]
    assert plan_deterministically("read the spreadsheet a.xlsx") == [("spreadsheet_read", {"path": "a.xlsx"})]
    assert plan_deterministically("list files") == [("list_files", {})]
    assert plan_deterministically("tell me a joke") == []


def test_tool_agent_deterministic_fallback(reg, ctx):
    agent = ToolAgent(reg, ctx, llm=None)
    report = agent.run("calculate 2*(3+4)")
    assert report.ok and report.mode == "deterministic"
    assert report.steps == ["3 + 4 = 7", "2 * 7 = 14", "result: 2*(3+4) = 14"]
    assert report.iterations[0].tool == "calculate" and report.iterations[0].ok
    check = ctx.log.verify()
    assert check.ok and check.entries == 1
    row = ctx.log.read()[0]
    assert row["tool"] == "calculate" and row["session"] == "tester__t" and len(row["args_sha256"]) == 64
    unknown = agent.run("tell me a joke")
    assert not unknown.ok and "does not match" in unknown.reason


def test_tool_agent_with_scripted_model(reg, ctx):
    script = iter([
        ToolDecision(thought="need the number", tool="calculate", args={"expression": "482*1.1"}),
        ToolDecision(thought="done", done=True, final="10% above normal is 530.2 m3/h"),
    ])
    llm = FakeLLM(responses={"ToolDecision": lambda system, user: next(script)})
    report = ToolAgent(reg, ctx, llm=llm, max_iterations=4).run("What is 10% above the normal flow of 482?")
    assert report.ok and report.mode == "model"
    assert report.llm_calls == 2
    assert [i.tool for i in report.iterations] == ["calculate"]
    assert report.final_output == "10% above normal is 530.2 m3/h"
    assert "482 * 1.1 = 530.2" in report.steps
    assert "calculate" in llm.calls[0]["user"] and "previous tool results" in llm.calls[1]["user"]
    json.dumps(report.model_dump(mode="json"))


def test_tool_agent_survives_bad_tool_and_budget(reg, ctx):
    llm = FakeLLM(responses={"ToolDecision": lambda s, u: ToolDecision(tool="teleport", args={})})
    report = ToolAgent(reg, ctx, llm=llm, max_iterations=2).run("go somewhere")
    assert not report.ok and "budget" in report.reason
    assert len(report.iterations) == 2 and all(not i.ok and "unknown tool" in (i.error or "") for i in report.iterations)
