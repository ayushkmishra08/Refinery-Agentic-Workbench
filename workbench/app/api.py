"""Local HTTP API for the web front end (FastAPI).

POST /ask                      run a request synchronously -> FinalResponse
POST /runs                     start a run in the background -> {run_id}
GET  /runs/{id}                run state (phase, plan progress, final when done)
GET  /runs/{id}/events         Server-Sent Events stream of progress (phase/agent/plan/final)
POST /runs/{id}/btw            ask the status agent about a running/finished run -> blocks
POST /btw                      same, for the latest run of a session
POST /upload                   upload a PDF/image; parsed with the knowledge-layer parser and added to the session
GET  /sessions/{id}            session memory (turns, uploads)
GET  /reviews                  pending human-in-the-loop items;  POST /reviews/{response_id} to decide
GET  /agents  GET /health  GET /schema   introspection for the frontend
All responses are JSON built from the pydantic models in workbench/core (schemas in docs/schema/).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter

from workbench.agents.registry import describe_agents
from workbench.core.blocks import Block
from workbench.core.request import Attachment, UserRequest
from workbench.core.result import FinalResponse
from workbench.orchestration.orchestrator import Orchestrator

logger = logging.getLogger(__name__)
_orch: Orchestrator | None = None
_event_queues: dict[str, list[queue.Queue]] = {}
_lock = threading.Lock()


def orch() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator(warm_start="full" if os.getenv("RWB_WARM_START", "1") == "1" else False)
    return _orch


@asynccontextmanager
async def lifespan(app: FastAPI):
    orch()
    yield
    if _orch is not None:
        _orch.shutdown()


app = FastAPI(title="Refinery Engineering AI Workbench", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AskBody(BaseModel):
    text: str
    session_id: str = "web"
    user_role: str = "engineer"
    options: dict = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)


class BtwBody(BaseModel):
    text: str = "what is going on?"
    session_id: str | None = None


class ReviewBody(BaseModel):
    decision: str
    reviewer: str = "reviewer"
    note: str = ""


def _fanout(run_id: str):
    def sink(ev) -> None:
        with _lock:
            qs = list(_event_queues.get(run_id, []))
        for q in qs:
            q.put(ev.model_dump(mode="json"))
    return sink


@app.get("/health")
def health() -> dict:
    o = orch()
    return {"status": "ok", "backend": o.backend_name, "llm": getattr(o.llm, "model", o.llm.name), "llm_available": o.llm.available(), "profile": o.cfg.profile.name, "effort": o.cfg.effort.name,
            "documents": [d.document_id for d in o.knowledge.documents()], "resources": o.resources.status(), "active_runs": len(o.runs.active())}


@app.get("/agents")
def agents() -> list[dict]:
    return describe_agents()


@app.get("/schema")
def schema() -> dict:
    return {"final_response": FinalResponse.model_json_schema(), "block": TypeAdapter(Block).json_schema(), "user_request": UserRequest.model_json_schema()}


@app.post("/ask", response_model=FinalResponse)
def ask(body: AskBody) -> FinalResponse:
    req = UserRequest(text=body.text, session_id=body.session_id, user_role=body.user_role, options=body.options, attachments=body.attachments)
    return orch().ask(req)


@app.post("/runs")
def start_run(body: AskBody) -> dict:
    req = UserRequest(text=body.text, session_id=body.session_id, user_role=body.user_role, options=body.options, attachments=body.attachments)
    o = orch()
    rs_holder: dict = {}

    def sink(ev):
        rid = rs_holder.get("id")
        if rid:
            _fanout(rid)(ev)

    rs = o.start(req, on_event=sink)
    rs_holder["id"] = rs.run_id
    with _lock:
        _event_queues.setdefault(rs.run_id, [])
    return {"run_id": rs.run_id, "session_id": rs.session_id}


@app.get("/runs")
def list_runs() -> list[dict]:
    return [r.model_dump(mode="json", exclude={"final", "recent_events"}) for r in orch().runs.all()]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    rs = orch().runs.get(run_id)
    if rs is None:
        raise HTTPException(404, "unknown run")
    rs.resources = orch().resources.status()
    return rs.model_dump(mode="json")


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    o = orch()
    rs = o.runs.get(run_id)
    if rs is None:
        raise HTTPException(404, "unknown run")
    q: queue.Queue = queue.Queue()
    with _lock:
        _event_queues.setdefault(run_id, []).append(q)
    # replay what already happened
    for e in rs.recent_events:
        q.put({"event": e["event"], "agent": e.get("agent"), "message": e.get("message"), "replay": True, "ts": rs.started + e["t"]})

    async def gen():
        try:
            while True:
                try:
                    item = q.get_nowait()
                except queue.Empty:
                    if rs.finished:
                        final = rs.final.model_dump(mode="json") if rs.final else {"status": "failed", "error": rs.error}
                        yield f"event: final\ndata: {json.dumps(final)}\n\n"
                        break
                    await asyncio.sleep(0.15)
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {item.get('event', 'progress')}\ndata: {json.dumps(item, default=str)}\n\n"
        finally:
            with _lock:
                if q in _event_queues.get(run_id, []):
                    _event_queues[run_id].remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/runs/{run_id}/btw")
def btw_run(run_id: str, body: BtwBody) -> dict:
    blocks = orch().btw(body.text, run_id=run_id)
    return {"run_id": run_id, "blocks": [b.model_dump(mode="json") for b in blocks]}


@app.post("/btw")
def btw(body: BtwBody) -> dict:
    o = orch()
    rs = o.runs.latest(body.session_id)
    blocks = o.btw(body.text, run_id=rs.run_id if rs else None, session_id=body.session_id)
    return {"run_id": rs.run_id if rs else None, "blocks": [b.model_dump(mode="json") for b in blocks]}


@app.get("/sessions/{session_id}")
def session(session_id: str) -> dict:
    return orch().sessions.load(session_id).model_dump(mode="json")


@app.post("/upload")
async def upload(file: UploadFile = File(...), session_id: str = Form("web"), note: str = Form("")) -> dict:
    o = orch()
    dest_dir = o.cfg.paths.uploads_dir / session_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file.filename or "upload.bin").name
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    from workbench.services.ingest import ingest_upload

    result = ingest_upload(o, session_id, dest, note=note)
    return result


@app.get("/reviews")
def reviews() -> list[dict]:
    return orch().hitl.pending()


@app.post("/reviews/{response_id}")
def decide(response_id: str, body: ReviewBody) -> dict:
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved or rejected")
    return orch().hitl.decide(response_id, body.decision, body.reviewer, body.note)


@app.get("/audit/{session_id}")
def audit(session_id: str, audit_id: str | None = None) -> list[dict]:
    return orch().audit.read(session_id, audit_id)
