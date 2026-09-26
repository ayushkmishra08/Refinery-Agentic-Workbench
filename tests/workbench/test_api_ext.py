"""The extended API surface end to end, with access control on and the three role accounts.

Covers: models & routing, sovereignty & chain verification, vault status / seal / rotate / revoke,
tools & the agent loop & the sandbox, deliverables -> draft -> flag -> resolve -> sign-off, intake.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from workbench.app import api


@pytest.fixture
def client(secure_orch, tokens):
    api._orch = secure_orch
    with TestClient(api.app) as c:
        c.tokens = tokens
        yield c
    api._orch = None


def _h(client, role):
    return {"Authorization": f"Bearer {client.tokens[role]}"}


# --------------------------------------------------------------------------- health / models
def test_health_reports_new_subsystems(client):
    h = client.get("/health").json()
    assert h["status"] == "ok"
    assert "airgap_enforced" in h and "vault" in h and h["tools"] >= 8 and h["sandbox"] is True


def test_models_registry_and_route(client):
    m = client.get("/models").json()
    names = {row["name"] for row in m["models"]}
    assert {"qwen3:4b", "qwen3.5:2b"} <= names
    assert "routing_enabled" in m and "best_per_kind" in m
    r = client.post("/models/route", json={"text": "read the scanned P&ID and calculate the margin"})
    assert r.status_code in (200, 503)               # 503 when no LLM is available in the test profile
    log = client.get("/models/routing").json()
    assert log["chain"]["ok"]


def test_register_model_is_admin_only(client, tmp_path, secure_orch):
    secure_orch.cfg.models.local_registry = tmp_path / "registry.local.yaml"
    body = {"name": "test-model:1b", "capabilities": {"code": 0.95}, "min_vram_mb": 500}
    assert client.post("/models/register", json=body, headers=_h(client, "user")).status_code == 403
    r = client.post("/models/register", json=body, headers=_h(client, "admin"))
    assert r.status_code == 200 and r.json()["registered"] == "test-model:1b"
    assert (tmp_path / "registry.local.yaml").exists()


# --------------------------------------------------------------------------- sovereignty
def test_sovereignty_report_and_verify(client):
    rep = client.get("/sovereignty").json()
    assert rep["airgap_enforced"] is True
    assert rep["egress_guard"]["installed"] is True
    assert rep["network_monitor"]["running"] is True
    assert any(c["log"] == "security audit" for c in rep["chains"])
    assert "verdict" in rep
    assert client.post("/sovereignty/verify", headers=_h(client, "user")).status_code == 403
    v = client.post("/sovereignty/verify", headers=_h(client, "manager")).json()
    assert v["intact"] is True
    conns = client.get("/sovereignty/connections?limit=5").json()
    assert "entries" in conns and conns["status"]["chain"]["ok"]
    eg = client.get("/sovereignty/egress").json()
    assert eg["guard"]["installed"] is True


# --------------------------------------------------------------------------- vault
def test_vault_status_seal_rotate_revoke(client, secure_orch, tmp_path):
    # a fake index cache to seal: two documents that the classifications know
    cache = secure_orch.cfg.paths.cache_dir
    for doc in ("CDU operating manual", "API 610 pump standard"):
        (cache / doc).mkdir(parents=True, exist_ok=True)
        (cache / doc / "index.json").write_text('{"fingerprint": "x", "index": {"doc": "%s"}}' % doc, encoding="utf-8")
    assert client.post("/vault/seal", json={"shred": False}, headers=_h(client, "user")).status_code == 403
    sealed = client.post("/vault/seal", json={"shred": False}, headers=_h(client, "admin")).json()["sealed"]
    by_branch = {s["branch"]: s for s in sealed}
    assert by_branch["CDU operating manual"]["roles"] == ["admin"]
    assert set(by_branch["API 610 pump standard"]["roles"]) == {"user", "manager", "admin"}
    st = client.get("/vault", headers=_h(client, "user")).json()
    assert "CDU operating manual" not in st["you"]["can_unwrap"] and "API 610 pump standard" in st["you"]["can_unwrap"]
    st_admin = client.get("/vault", headers=_h(client, "admin")).json()
    assert "CDU operating manual" in st_admin["you"]["can_unwrap"]
    rot = client.post("/vault/rotate/manager", headers=_h(client, "admin")).json()
    assert rot["version"] >= 2
    rev = client.post("/vault/revoke", json={"branch": "API 610 pump standard", "role": "user"}, headers=_h(client, "admin")).json()
    assert "user" not in rev["roles_with_key"]
    assert secure_orch.kms.key_events.verify().ok
    assert client.get("/vault/tls").json()["configured"] in (True, False)


# --------------------------------------------------------------------------- tools / sandbox
def test_tools_listed_and_calculate_runs(client):
    t = client.get("/tools").json()
    names = {x["name"] for x in t["tools"]}
    assert {"read_file", "write_file", "list_files", "run_python", "spreadsheet_read", "spreadsheet_write",
            "search_documents", "calculate", "ocr_image", "describe_image", "make_docx", "make_xlsx", "make_pptx"} <= names
    assert t["sandbox"]["enabled"] is True
    assert client.post("/tools/run", json={"tool": "calculate", "args": {"expression": "(520-482)/482*100"}}).status_code == 401
    r = client.post("/tools/run", json={"tool": "calculate", "args": {"expression": "(520-482)/482*100"}, "session_id": "t1"},
                    headers=_h(client, "user")).json()
    assert r["ok"] and "7.88" in r["output"] and len(r["steps"]) >= 3
    calls = client.get("/tools/calls", headers=_h(client, "user")).json()
    assert calls["chain"]["ok"] and calls["entries"][-1]["tool"] == "calculate"


def test_search_documents_is_guarded(client):
    # a user may not read the CDU manual (SECRET); the desalter is CONFIDENTIAL (manager)
    body = {"tool": "search_documents", "args": {"query": "crude charge pump normal flow", "k": 8}, "session_id": "t2"}
    as_user = client.post("/tools/run", json=body, headers=_h(client, "user")).json()
    as_admin = client.post("/tools/run", json=body, headers=_h(client, "admin")).json()
    user_docs = {e.get("document_id") for e in as_user.get("evidence", [])}
    admin_docs = {e.get("document_id") for e in as_admin.get("evidence", [])}
    assert "CDU operating manual" not in user_docs
    assert "CDU operating manual" in admin_docs


def test_agent_loop_writes_file_and_workspace_lists_it(client):
    r = client.post("/tools/agent", json={"goal": "calculate 2*(3+4)", "session_id": "t3"}, headers=_h(client, "user")).json()
    assert r["ok"] and r["iterations"][0]["tool"] == "calculate" and "14" in r["final_output"]
    w = client.post("/tools/run", json={"tool": "write_file", "args": {"path": "notes/margin.txt", "content": "7.88 %"}, "session_id": "t3"},
                    headers=_h(client, "user")).json()
    assert w["ok"]
    files = client.get("/tools/workspace?session_id=t3", headers=_h(client, "user")).json()["files"]
    assert any(f["path"] == "notes/margin.txt" for f in files)
    dl = client.get("/tools/workspace/file?session_id=t3&path=notes/margin.txt", headers=_h(client, "user"))
    assert dl.status_code == 200 and dl.text == "7.88 %"
    assert client.get("/tools/workspace/file?session_id=t3&path=../../x", headers=_h(client, "user")).status_code == 404


def test_sandbox_run_and_log(client):
    r = client.post("/sandbox/run", json={"code": "print(sum(range(10)))", "tests": "def test_ok():\n    assert 1 + 1 == 2\n"},
                    headers=_h(client, "manager")).json()
    assert r["ok"] and "45" in r["stdout"] and r["verified"] is True and r["workdir_destroyed"] is True
    blocked = client.post("/sandbox/run", json={"code": "import socket\nsocket.create_connection(('8.8.8.8', 53), timeout=2)\n"},
                          headers=_h(client, "manager")).json()
    assert blocked["egress_attempts"] and not blocked["ok"]
    runs = client.get("/sandbox/runs", headers=_h(client, "manager")).json()
    assert runs["chain"]["ok"] and len(runs["entries"]) >= 2
    man = client.get("/sandbox/manifest").json()
    assert "stdlib_allowed" in man and "subprocess" in man["banned_imports"]


# --------------------------------------------------------------------------- deliverables & review
def test_export_and_signoff_workflow(client, secure_orch):
    ask = client.post("/ask", json={"text": "The crude charge pump is operating at 520 m3/h. Is this acceptable?", "session_id": "d1"},
                      headers=_h(client, "admin")).json()
    rid = ask["response_id"]
    assert client.get("/deliverables/responses", headers=_h(client, "admin")).json()[0]["response_id"] == rid
    # a user cannot export an admin's answer
    assert client.post("/deliverables", json={"response_id": rid, "format": "xlsx"}, headers=_h(client, "user")).status_code == 403
    for fmt in ("docx", "xlsx", "pptx"):
        out = client.post("/deliverables", json={"response_id": rid, "format": fmt}, headers=_h(client, "admin")).json()
        assert out["fmt"] == fmt and out["draft"]["status"] == "pending_signoff" and out["bytes"] > 1000
        dl = client.get(out["download"], headers=_h(client, "admin"))
        assert dl.status_code == 200 and len(dl.content) == out["bytes"]
    drafts = client.get("/drafts", headers=_h(client, "admin")).json()
    assert drafts["summary"]["pending_signoff"] >= 3 and drafts["chain"]["ok"]
    draft_id = drafts["drafts"][0]["draft_id"]
    # inject a low-confidence figure, as an OCR misread would
    from workbench.review.drafts import DraftFigure, FigureSource

    d = secure_orch.drafts.get(draft_id)
    d.figures.append(DraftFigure(label="Discharge pressure (OCR)", value="24.15", unit="kg/cm2",
                                 source=FigureSource(document_id="scan.pdf", page=2, text="24.15", source_kind="ocr"),
                                 confidence=0.41, flagged=True, flag_reason="low OCR confidence 0.41"))
    secure_orch.drafts._save(d)
    fid = d.figures[-1].figure_id
    blocked = client.post(f"/drafts/{draft_id}/signoff", json={"note": "looks fine"}, headers=_h(client, "manager"))
    assert blocked.status_code == 409 and blocked.json()["detail"]["open_flags"][0]["figure_id"] == fid
    ok = client.post(f"/drafts/{draft_id}/figures/{fid}/resolve", json={"action": "corrected", "corrected_value": "24.45", "note": "checked p.2"},
                     headers=_h(client, "manager")).json()
    assert ok["open_flags"] == 0
    assert client.post(f"/drafts/{draft_id}/signoff", json={}, headers=_h(client, "user")).status_code == 403
    signed = client.post(f"/drafts/{draft_id}/signoff", json={"note": "verified"}, headers=_h(client, "manager")).json()
    assert signed["status"] == "signed_off" and signed["signed_off_by"] == "manager"
    events = secure_orch.security_audit.read()
    assert any(e["event"] == "signoff_blocked" for e in events) and any(e["event"] == "draft_signed_off" for e in events)


def test_intake_endpoint_flags_low_confidence(client):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((20, 60), "PRESSURE 24.45 kg/cm2", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = client.post("/intake", files={"file": ("gauge.png", buf.getvalue(), "image/png")},
                    data={"session_id": "i1", "run_vision": "false"}, headers=_h(client, "user"))
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "image" and "flagged" in body and "draft" in body
