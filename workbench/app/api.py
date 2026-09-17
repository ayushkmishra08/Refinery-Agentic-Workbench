"""Local HTTP API for the web front end (FastAPI).

POST /auth/login               username + password -> {token, role, readable_documents}
POST /auth/logout              revoke the bearer token
GET  /auth/whoami              the caller's role, tags and readable documents
POST /ask                      run a request synchronously -> FinalResponse (401 when not cleared)
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

Authentication is a bearer token: POST /auth/login, then send it back either as an
``Authorization: Bearer <token>`` header or as ``auth_token`` in the request body. Without a
token a caller is a guest, and a guest is cleared for nothing classified — which the CDU
operating manual is.
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

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter

from workbench.agents.registry import describe_agents
from workbench.core.blocks import Block
from workbench.core.request import Attachment, UserRequest
from workbench.core.result import FinalResponse
from workbench.orchestration.orchestrator import Orchestrator
from workbench.security import totp

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
    auth_token: str | None = Field(default=None, description="Token from POST /auth/login; an Authorization: Bearer header is used when this is absent")
    access_key: str | None = Field(default=None, description="An approved one-time key (RGK.…) for this exact question")
    options: dict = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)


class LoginBody(BaseModel):
    username: str = "lead"
    password: str
    label: str = "web"
    code: str | None = Field(default=None, description="Authenticator code, when the role requires a second factor")


class MfaBody(BaseModel):
    code: str


def bearer(authorization: str | None = Header(default=None)) -> str | None:
    """The token from an ``Authorization: Bearer`` header, when one was sent."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


class BtwBody(BaseModel):
    text: str = "what is going on?"
    session_id: str | None = None


class ReviewBody(BaseModel):
    decision: str
    reviewer: str = "reviewer"
    note: str = ""


class DecisionBody(BaseModel):
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
            "documents": [d.document_id for d in o.knowledge.documents()], "resources": o.resources.status(), "active_runs": len(o.runs.active()),
            "access_control": o.cfg.security.enabled, "answer_style": o.cfg.presentation.style,
            "compose_answers": o.cfg.llm.use_llm_for_answer}


@app.get("/agents")
def agents() -> list[dict]:
    return describe_agents()


@app.get("/schema")
def schema() -> dict:
    return {"final_response": FinalResponse.model_json_schema(), "block": TypeAdapter(Block).json_schema(), "user_request": UserRequest.model_json_schema()}


def _demo_code(cred) -> dict:
    """A code the demo can actually type, and when it becomes valid.

    The obvious answer — "the code the app is showing now" — is wrong often enough to spoil a
    demonstration: the step the account just enrolled with is already burned, and single use is
    the point of the feature. So walk forward to the first step that has not been spent and say
    how long until it works.
    """
    now = time.time()
    spent = set(cred.totp_used_counters)
    current = int(now // totp.STEP_SECONDS)
    for ahead in range(0, 3):
        counter = current + ahead
        if counter not in spent:
            at = counter * totp.STEP_SECONDS
            return {"demo_code": totp.code_now(cred.totp_secret, at),
                    "demo_code_valid_in_seconds": max(0, int(at - now)),
                    "demo_code_expires_in_seconds": max(0, int(at + totp.STEP_SECONDS - now))}
    return {"demo_code": None, "demo_code_valid_in_seconds": totp.seconds_remaining(now)}


@app.post("/auth/login")
def login(body: LoginBody) -> dict:
    """Exchange a password — and a code, where the role needs one — for a session token.

    401 refuses, 423 means locked, and **428** means "the password was right, now send the code":
    the client re-posts the same body with ``code`` filled in. Nothing is issued at 428, so a
    client that stops there holds nothing.
    """
    from workbench.security.auth import AuthError, MfaRequired
    from workbench.security.totp import TotpError

    o = orch()
    try:
        principal = o.login(body.username, body.password, label=body.label, code=body.code)
    except MfaRequired as exc:
        challenge: dict = {"mfa_required": True, "username": exc.username, "role": exc.role,
                           "digits": totp.DIGITS, "period": totp.STEP_SECONDS,
                           "seconds_remaining": totp.seconds_remaining(),
                           "detail": "Enter the 6-digit code from your authenticator app."}
        if o.cfg.security.mfa_demo_codes:
            cred = o.auth._users.get(exc.username)
            if cred and cred.totp_secret:
                challenge.update(_demo_code(cred))
                challenge["demo_notice"] = ("Demo mode is on (RWB_OTP_DEMO): the expected code is shown "
                                            "because no phone is enrolled here. Never enable this in a deployment.")
        raise HTTPException(428, detail=challenge) from exc
    except TotpError as exc:
        raise HTTPException(401, str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(423 if exc.locked_until else 401, str(exc)) from exc
    _, decision = o.access_for(principal.token)
    return {"token": principal.token, "username": principal.username, "role": principal.role.value,
            "level": principal.level, "readable_tags": principal.tags, "expires": principal.expires,
            "must_change_password": principal.must_change_password,
            "mfa_enrolled": principal.mfa_enrolled, "mfa_satisfied": principal.mfa_satisfied,
            "mfa_enrolment_pending": (principal.role.value in o.cfg.security.mfa_required_roles
                                      and not principal.mfa_enrolled),
            "readable_documents": decision.allowed, "withheld_documents": decision.denied}


@app.get("/auth/mfa")
def mfa_status(token: str | None = Depends(bearer)) -> dict:
    """Whether this deployment asks the caller's role for a second factor, and whether they have one."""
    return orch().mfa_status(token)


@app.post("/auth/mfa/enrol")
def mfa_enrol(token: str | None = Depends(bearer)) -> dict:
    """Mint an authenticator secret and return the QR payload. Inactive until confirmed."""
    try:
        return orch().begin_mfa_enrolment(token)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.post("/auth/mfa/confirm")
def mfa_confirm(body: MfaBody, token: str | None = Depends(bearer)) -> dict:
    """Prove the app is producing codes, which is what activates the secret."""
    from workbench.security.auth import AuthError
    from workbench.security.totp import TotpError

    try:
        return orch().confirm_mfa_enrolment(token, body.code)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except (TotpError, AuthError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/auth/mfa")
def mfa_disable(username: str | None = None, token: str | None = Depends(bearer)) -> dict:
    """Remove an authenticator — one's own, or anyone's when an administrator asks."""
    try:
        return orch().disable_mfa(token, username)
    except PermissionError as exc:
        raise HTTPException(403 if "administrator" in str(exc) else 401, str(exc)) from exc


@app.post("/auth/logout")
def logout(token: str | None = Depends(bearer)) -> dict:
    return {"revoked": orch().logout(token)}


@app.get("/auth/whoami")
def whoami(token: str | None = Depends(bearer)) -> dict:
    o = orch()
    principal, decision = o.access_for(token)
    # The second-factor state rides along so a caller that re-reads its clearance — after enrolling
    # an authenticator, say — does not keep showing a banner about an authenticator it just added.
    mfa = o.mfa_status(token)
    return {"username": principal.username, "role": principal.role.value, "authenticated": principal.authenticated,
            "level": principal.level, "readable_tags": principal.tags, "access_control": o.cfg.security.enabled,
            "readable_documents": decision.allowed, "withheld_documents": decision.denied,
            "documents": o.restricted_documents(token),
            "mfa_enrolled": mfa["enrolled"], "mfa_enrolment_pending": mfa["enrolment_pending"],
            "mfa_required_for_role": mfa["required_for_role"]}


@app.post("/ask", response_model=FinalResponse)
def ask(body: AskBody, token: str | None = Depends(bearer)) -> FinalResponse:
    req = UserRequest(text=body.text, session_id=body.session_id, user_role=body.user_role,
                      auth_token=body.auth_token or token, access_key=body.access_key,
                      options=body.options, attachments=body.attachments)
    resp = orch().ask(req)
    if resp.status == "unauthorized":
        raise HTTPException(401, resp.answer_markdown)
    return resp


@app.post("/runs")
def start_run(body: AskBody, token: str | None = Depends(bearer)) -> dict:
    req = UserRequest(text=body.text, session_id=body.session_id, user_role=body.user_role,
                      auth_token=body.auth_token or token, access_key=body.access_key,
                      options=body.options, attachments=body.attachments)
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


@app.get("/sessions")
def sessions(token: str | None = Depends(bearer)) -> list[dict]:
    """The caller's own conversations, newest first — title, turn count, when, attachments."""
    return orch().list_sessions(token)


@app.get("/sessions/{session_id}")
def session(session_id: str, token: str | None = Depends(bearer)) -> dict:
    """One caller's conversation. Sessions are namespaced by principal, so a guessed id
    returns the caller's own (empty) conversation, never someone else's."""
    o = orch()
    state = o.session_for(session_id, token)
    # touching a conversation brings its attachments back if the process that indexed them is gone
    o._ensure_uploads(state.session_id, state)
    out = state.model_dump(mode="json")
    out["attached_documents"] = o.upload_document_ids(state.session_id)
    return out


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, token: str | None = Depends(bearer)) -> dict:
    """Forget one of the caller's conversations."""
    try:
        return {"deleted": orch().delete_session(token, session_id)}
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


@app.post("/upload")
async def upload(file: UploadFile = File(...), session_id: str = Form("web"), note: str = Form(""),
                 token: str | None = Depends(bearer)) -> dict:
    """Add a PDF or an image to **this conversation**.

    The file is parsed and indexed for the session that uploaded it and nowhere else: it is not
    added to the shared corpus, not classified, and not written into the knowledge layer. It is
    readable by the person who uploaded it whatever their role — it is their own file — and it is
    gone when the session's uploads are dropped or the server restarts.
    """
    o = orch()
    principal = o.principal(token)
    if o.cfg.security.enabled and not principal.authenticated:
        raise HTTPException(401, "Sign in before uploading: POST /auth/login.")
    # Namespaced exactly as the ask path namespaces it, or the upload lands in a conversation
    # nobody is having.
    session_key = o.sessions.key_for(principal.username, session_id)
    dest_dir = o.cfg.paths.uploads_dir / principal.username / session_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file.filename or "upload.bin").name
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    from workbench.services.ingest import ingest_upload

    return ingest_upload(o, session_key, dest, note=note,
                         owner=principal.username, owner_role=principal.role.value)


class PromoteBody(BaseModel):
    session_id: str = "web"
    tag: str | None = Field(default=None, description="INTERNAL, CONFIDENTIAL or SECRET; defaults to CONFIDENTIAL")


@app.post("/knowledge/documents/{document_id:path}/promote")
def promote_upload(document_id: str, body: PromoteBody, token: str | None = Depends(bearer)) -> dict:
    """Move a document uploaded into a conversation into the shared knowledge layer.

    Manager or above: the person doing this decides what everyone else will be able to read.
    """
    o = orch()
    try:
        return o.promote_upload(token, body.session_id, document_id, body.tag)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/upload")
def drop_uploads(session_id: str = "web", token: str | None = Depends(bearer)) -> dict:
    """Forget everything uploaded into this conversation."""
    o = orch()
    principal = o.principal(token)
    dropped = o.drop_session_uploads(o.sessions.key_for(principal.username, session_id))
    return {"dropped": dropped}


def _require_role(token: str | None, minimum: str) -> "Principal":
    """Refuse a caller below ``minimum``. Used by the endpoints that expose other people's work."""
    from workbench.security.roles import Role, can_read, level_of, title

    o = orch()
    principal = o.principal(token)
    if not o.cfg.security.enabled:
        return principal
    if not principal.authenticated:
        raise HTTPException(401, "Sign in first: POST /auth/login.")
    if level_of(principal.role) < level_of(minimum):
        raise HTTPException(403, f"This needs {title(minimum)} or above; you are {title(principal.role)}.")
    return principal


@app.get("/reviews")
def reviews(token: str | None = Depends(bearer)) -> list[dict]:
    """Pending human-in-the-loop items. These carry answer content, so they need a manager."""
    _require_role(token, "manager")
    return orch().hitl.pending()


@app.post("/reviews/{response_id}")
def decide(response_id: str, body: ReviewBody, token: str | None = Depends(bearer)) -> dict:
    principal = _require_role(token, "manager")
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved or rejected")
    return orch().hitl.decide(response_id, body.decision, body.reviewer or principal.username, body.note)


@app.get("/audit/{session_id}")
def audit(session_id: str, audit_id: str | None = None, token: str | None = Depends(bearer)) -> list[dict]:
    """The run trace of one's own conversation. The trace quotes retrieved material, so a
    caller only ever sees their own; an administrator may read anyone's."""
    from workbench.security.roles import Role

    o = orch()
    principal = o.principal(token)
    if o.cfg.security.enabled and not principal.authenticated:
        raise HTTPException(401, "Sign in first: POST /auth/login.")
    if o.cfg.security.enabled and principal.role is not Role.ADMIN:
        return o.audit.read(session_id, audit_id) if session_id.startswith(f"{principal.username}__") else []
    return o.audit.read(session_id, audit_id)


@app.get("/stats")
def stats(token: str | None = Depends(bearer)) -> dict:
    """Counts for the caller's workspace: documents, knowledge, activity, escalation queue.

    Every number is computed over what this role may read, so the panel cannot be used to size
    the tier above. Nothing about the host machine is reported.
    """
    return orch().workspace_stats(token)


@app.get("/knowledge/tree")
def knowledge_tree(token: str | None = Depends(bearer)) -> dict:
    """The knowledge layer branch by branch, with the caller's reach marked on each one.

    A branch the caller may read is opened: chapters and counts. A branch they may not is named,
    classified and routed to whoever can release it — and nothing else about it is returned.
    """
    return orch().knowledge_tree(token)


class DocumentRolesBody(BaseModel):
    roles: list[str] | None = Field(
        default=None,
        description="The roles allowed to read this document. null hands it back to the tag ladder.",
    )


@app.post("/security/documents/{document_id:path}/roles")
def set_document_roles(document_id: str, body: DocumentRolesBody, token: str | None = Depends(bearer)) -> dict:
    """Pin who reads one document. Administrators only."""
    try:
        return orch().set_document_roles(token, document_id, body.roles)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from exc


@app.get("/security")
def security(token: str | None = Depends(bearer)) -> dict:
    """The role schema, the document tags, and the caller's place in both."""
    return orch().security_overview(token)


@app.post("/access-requests")
def raise_access_request(body: AskBody, token: str | None = Depends(bearer)) -> dict:
    """Ask a higher role to release the material one question needs."""
    from workbench.security.escalation import EscalationError

    try:
        return orch().request_access(body.auth_token or token, body.text, session_id=body.session_id)
    except EscalationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/access-requests")
def my_access_requests(token: str | None = Depends(bearer)) -> list[dict]:
    return orch().my_requests(token)


@app.get("/approvals")
def approvals(show_all: bool = False, token: str | None = Depends(bearer)) -> list[dict]:
    """The approval queue for the caller's role, with the material each request would release."""
    from workbench.security.escalation import EscalationError

    try:
        return orch().pending_approvals(token, include_all=show_all)
    except EscalationError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/approvals/{request_id}/approve")
def approve(request_id: str, body: DecisionBody | None = None, token: str | None = Depends(bearer)) -> dict:
    """Approve a request. The response carries the one-time key, and it is shown only here."""
    from workbench.security.escalation import EscalationError

    try:
        return orch().approve_request(token, request_id, note=(body.note if body else ""))
    except EscalationError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.post("/approvals/{request_id}/deny")
def deny(request_id: str, body: DecisionBody | None = None, token: str | None = Depends(bearer)) -> dict:
    from workbench.security.escalation import EscalationError

    try:
        return orch().deny_request(token, request_id, note=(body.note if body else ""))
    except EscalationError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.get("/security-log")
def security_log(event: str | None = None, principal: str | None = None, limit: int = 100,
                 token: str | None = Depends(bearer)) -> list[dict]:
    """The security audit trail. Managers see it; administrators see everything in it."""
    from workbench.security.roles import Role

    who = _require_role(token, "manager")
    o = orch()
    if o.cfg.security.enabled and who.role is not Role.ADMIN:
        principal = who.username            # a manager reads their own trail, not everyone's
    return o.security_audit.read(event=event, principal=principal, limit=limit)
