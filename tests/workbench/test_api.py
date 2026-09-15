"""FastAPI surface with the orchestrator swapped for the mock-backend instance."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from workbench.app import api


@pytest.fixture
def client(orch):
    api._orch = orch
    with TestClient(api.app) as c:
        yield c
    api._orch = orch        # lifespan shutdown released resources; keep the instance for other tests


def test_health_agents_schema(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["backend"] == "mock" and h["documents"] == ["CDU operating manual"] and h["llm_available"] is False
    agents = client.get("/agents").json()
    assert {a["key"] for a in agents} >= {"task_classifier", "planner", "governance", "lookup"}
    schema = client.get("/schema").json()
    assert "final_response" in schema and "block" in schema and "user_request" in schema


def test_ask_returns_final_response_json(client):
    r = client.post("/ask", json={"text": "What is the normal flow rate of the crude charge pump?", "session_id": "api-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "answered" and data["task_type"] == "lookup"
    assert any(b["type"] == "kpi" for b in data["blocks"]) and data["blocks"][-1]["type"] == "audit"
    assert client.get("/sessions/api-1").json()["turns"][-1]["task_type"] == "lookup"
    assert client.get(f"/audit/api-1?audit_id={data['audit_trail_id']}").json()[-1]["kind"] == "final"


def test_background_run_poll_and_btw(client):
    r = client.post("/runs", json={"text": "Which instruments monitor the crude charge pump?", "session_id": "api-2"})
    run_id = r.json()["run_id"]
    deadline = time.time() + 30
    state = None
    while time.time() < deadline:
        state = client.get(f"/runs/{run_id}").json()
        if state["finished"]:
            break
        time.sleep(0.05)
    assert state and state["finished"] and state["final"]["status"] == "answered"
    assert any(s["agent"] == "graph" and s["status"] == "done" for s in state["step_status"])
    btw = client.post(f"/runs/{run_id}/btw", json={"text": "what is going on?"}).json()
    assert btw["run_id"] == run_id and btw["blocks"][0]["type"] == "text"
    latest = client.post("/btw", json={"text": "status", "session_id": "api-2"}).json()
    assert latest["run_id"] == run_id
    assert any(x["run_id"] == run_id for x in client.get("/runs").json())
    assert client.get("/runs/does-not-exist").status_code == 404


def test_reviews_endpoints(client):
    client.post("/ask", json={"text": "Can I bypass the crude charge pump low flow trip temporarily?", "session_id": "api-3"})
    pend = client.get("/reviews").json()
    assert pend and pend[-1]["session_id"] == "api-3"
    rid = pend[-1]["response_id"]
    assert client.post(f"/reviews/{rid}", json={"decision": "nope"}).status_code == 400
    ok = client.post(f"/reviews/{rid}", json={"decision": "rejected", "reviewer": "tester"}).json()
    assert ok["status"] == "rejected"


def test_upload_rejects_unsupported_type(client, tmp_path):
    r = client.post("/upload", files={"file": ("notes.txt", b"hello", "text/plain")}, data={"session_id": "api-4"})
    assert r.status_code == 200 and r.json()["status"] == "unsupported"
