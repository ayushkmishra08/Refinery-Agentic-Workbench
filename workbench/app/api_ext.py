"""The extended API: models & routing, local tools & sandbox, intake, deliverables & review, vault, sovereignty.

Everything here is an ``APIRouter`` included by ``workbench.app.api``; the question path in
``api.py`` is untouched. Each router reads the same orchestrator and the same bearer token, so the
access rules are the ones the rest of the API enforces: a tool sees the knowledge exactly as a
question from that caller would, a draft is visible to its owner and to reviewers, the vault and
the model registry are administered by administrators only.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from workbench.app.deps import bearer, orch, require_role as _require_role

logger = logging.getLogger(__name__)


def _principal_or_401(token: str | None):
    o = orch()
    principal = o.principal(token)
    if o.cfg.security.enabled and not principal.authenticated:
        raise HTTPException(401, "Sign in first: POST /auth/login.")
    return principal


# =========================================================================== models & routing
models = APIRouter(prefix="/models", tags=["models"])


@models.get("")
def list_models() -> dict:
    """Every registered model, its capability profile, whether it is installed and fits the card, and who wins each task kind."""
    o = orch()
    router = o.router
    from workbench.models.registry import discover_installed, load_registry

    installed = discover_installed(o.cfg.llm.base_url)
    if router is None:
        reg = load_registry(o.cfg.models.local_registry)
        table = [{"name": m.name, "family": m.family, "size_gb": m.size_gb, "context": m.context, "modalities": m.modalities,
                  "installed": any(i["name"] == m.name or i["name"] == f"{m.name}:latest" for i in installed),
                  "fits_vram": m.fits(o.cfg.models.vram_mb), "min_vram_mb": m.min_vram_mb, "resident": False,
                  "capabilities": m.capabilities, "notes": m.notes, "source": m.source, "thinking": m.thinking} for m in reg.models]
        best = {}
    else:
        table = router.table()
        best = router.best_per_kind()
    return {"routing_enabled": bool(o.cfg.models.routing_enabled and router is not None),
            "budget": o.cfg.models.budget, "vram_mb": o.cfg.models.vram_mb, "profile": o.cfg.profile.name,
            "default_model": o.cfg.llm.model, "vision_model": o.cfg.llm.vision_model,
            "resident": getattr(router, "resident", None), "installed": installed, "models": table, "best_per_kind": best,
            "registry_files": {"builtin": "workbench/models/registry.yaml", "local": str(o.cfg.models.local_registry)}}


@models.get("/routing")
def routing_log(limit: int = 100, run_id: str | None = None) -> dict:
    """The routing log: which model was chosen for which call, with the candidates and the reason."""
    o = orch()
    from workbench.sovereignty.hashchain import HashChainedLog

    log = HashChainedLog(o.cfg.models.routing_log)
    rows = log.read()
    if run_id:
        rows = [r for r in rows if r.get("run_id") == run_id]
    return {"entries": rows[-limit:], "total": len(rows), "chain": log.verify().model_dump()}


class RouteBody(BaseModel):
    text: str
    budget: str | None = None


@models.post("/route")
def route_text(body: RouteBody) -> dict:
    """Decompose a request into sub-tasks and show which model would take each (no model is called)."""
    o = orch()
    if o.router is None:
        raise HTTPException(503, "Model routing is not active (no Ollama, or RWB_ROUTING=off).")
    plan = o.router.plan(body.text, budget=body.budget or o.cfg.models.budget)
    return plan.model_dump(mode="json")


class RegisterModelBody(BaseModel):
    name: str
    family: str = ""
    size_gb: float = 0.0
    context: int = 4096
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    min_vram_mb: int = 0
    thinking: bool = False
    capabilities: dict[str, float] = Field(default_factory=dict)
    notes: str = ""


@models.post("/register")
def register_model(body: RegisterModelBody, token: str | None = Depends(bearer)) -> dict:
    """Plug a model in: write its capability profile to the local registry and make it routable now. Administrators only."""
    import yaml

    _require_role(token, "admin")
    o = orch()
    path = Path(o.cfg.models.local_registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    rows = [m for m in (raw or {}).get("models", []) if m.get("name") != body.name]
    rows.append(body.model_dump())
    path.write_text(yaml.safe_dump({"models": rows}, sort_keys=False), encoding="utf-8")
    if o.router is not None:
        from workbench.models.registry import ModelProfile

        o.router.registry.register(ModelProfile(**body.model_dump(), source="local"))
        if hasattr(o.llm, "client"):
            o.router.installed = set(o.llm.client.installed_models())
    o.security_audit.write("model_registered", principal=o.principal(token).username, role=o.principal(token).role.value,
                           outcome=f"{body.name} registered with {len(body.capabilities)} capability scores")
    return {"registered": body.name, "local_registry": str(path),
            "installed": bool(o.router and o.router.is_installed(body.name))}


# --- signed model packages -------------------------------------------------------------------
class PackageBody(BaseModel):
    path: str
    dry_run: bool = True


@models.get("/packages")
def package_log(token: str | None = Depends(bearer)) -> dict:
    """Signed model packages: trusted signers, and the chained log of every verification and import."""
    _require_role(token, "manager")
    o = orch()
    from workbench.sovereignty.model_updates import trusted_signers, update_log

    trust = o.cfg.paths.security_dir / "trusted_signers"
    return {"trusted_signers": trusted_signers(trust), "trust_dir": str(trust),
            **update_log(o.cfg.paths.security_dir / "model_updates.jsonl")}


@models.post("/packages/verify")
def package_verify(body: PackageBody, token: str | None = Depends(bearer)) -> dict:
    _require_role(token, "manager")
    o = orch()
    from workbench.sovereignty.model_updates import verify_package

    report = verify_package(Path(body.path), o.cfg.paths.security_dir / "trusted_signers")
    o.security_audit.write("model_package_verified" if report.ok else "model_package_rejected",
                           principal=o.principal(token).username, role=o.principal(token).role.value,
                           outcome=report.detail, package=body.path)
    return report.model_dump(mode="json")


@models.post("/packages/import")
def package_import(body: PackageBody, token: str | None = Depends(bearer)) -> dict:
    """Verify a signed package and, unless dry_run, load it into Ollama from the local files — never a download."""
    _require_role(token, "admin")
    o = orch()
    from workbench.sovereignty.model_updates import import_package

    out = import_package(Path(body.path), o.cfg.paths.security_dir / "trusted_signers", ollama_url=o.cfg.llm.base_url,
                         dry_run=body.dry_run, log_path=o.cfg.paths.security_dir / "model_updates.jsonl")
    ver = out.get("verification", {})
    o.security_audit.write("model_package_verified" if ver.get("ok") else "model_package_rejected",
                           principal=o.principal(token).username, role=o.principal(token).role.value,
                           outcome=("imported" if out.get("imported") else ("dry run" if body.dry_run else "refused")), package=body.path)
    return out


# =========================================================================== sovereignty
sovereignty = APIRouter(prefix="/sovereignty", tags=["sovereignty"])


@sovereignty.get("")
def sovereignty_report(deep: bool = False) -> dict:
    """Air-gap proof: egress guard state, live network monitor, and the integrity of every chained log."""
    o = orch()
    if o.sovereignty is None:
        raise HTTPException(503, "sovereignty service unavailable")
    o.start_sovereignty()
    rep = o.sovereignty.report() if deep else _shallow_report(o)
    rep["tls"] = _tls_state(o)
    return rep


def _shallow_report(o) -> dict:
    """The report without walking every run-audit file (that is ``deep=true``)."""
    svc = o.sovereignty
    from workbench.sovereignty.hashchain import HashChainedLog

    chains = []
    for name, path in svc.chained_logs():
        if not path.exists():
            chains.append({"log": name, "path": str(path), "exists": False, "ok": True, "entries": 0, "detail": "not created yet"})
            continue
        v = HashChainedLog(path).verify()
        chains.append({"log": name, "path": str(path), "exists": True, **v.model_dump(exclude={"path"})})
    audits = svc.run_audit_logs()
    chains.append({"log": f"run audits ({len(audits)} conversations)", "path": str(o.cfg.paths.audit_dir), "exists": bool(audits),
                   "ok": True, "entries": 0, "head": "", "first_bad_seq": None, "detail": "not verified in this view; use deep=true"})
    mon = svc.monitor.status() if svc.monitor else {"running": False, "verdict": "monitor disabled"}
    guard = svc.guard.status() if svc.guard else {"installed": False}
    from workbench.sovereignty.service import _verdict

    return {"airgap_enforced": bool(o.cfg.sovereignty.airgap_enforced), "egress_guard": guard, "network_monitor": mon,
            "chains": chains, "chains_intact": all(c["ok"] for c in chains), "verdict": _verdict(guard, mon, chains),
            "generated": time.time(), "deep": False}


def _tls_state(o) -> dict:
    tls_dir = Path(o.cfg.vault.tls_dir)
    if not (tls_dir / "server.crt").exists():
        return {"configured": False, "dir": str(tls_dir), "hint": "python -m workbench serve --tls (or --mtls)"}
    try:
        from workbench.security.tls import describe, ensure_certificates

        from workbench.app import api as core

        return {"configured": True, **describe(ensure_certificates(tls_dir)), "active": bool(getattr(core, "TLS_ACTIVE", False)),
                "mtls": bool(getattr(core, "MTLS_ACTIVE", False))}
    except Exception as exc:
        return {"configured": False, "error": str(exc)}


@sovereignty.get("/connections")
def connections(limit: int = 200, event: str | None = None, external_only: bool = False) -> dict:
    o = orch()
    if o.sovereignty is None or o.sovereignty.monitor is None:
        raise HTTPException(503, "network monitor disabled")
    rows = o.sovereignty.monitor.recent(limit=5000, event=event)
    if external_only:
        rows = [r for r in rows if r.get("class") == "external"]
    return {"entries": rows[-limit:], "status": o.sovereignty.monitor.status()}


@sovereignty.post("/verify")
def verify_chains(token: str | None = Depends(bearer)) -> dict:
    """Walk every chained log in full (all run audits included) and report the first altered entry, if any."""
    _require_role(token, "manager")
    o = orch()
    rows = o.sovereignty.verify_all()
    return {"chains": rows, "intact": all(r["ok"] for r in rows), "checked": time.time()}


@sovereignty.get("/egress")
def egress(limit: int = 100) -> dict:
    o = orch()
    from workbench.sovereignty.hashchain import HashChainedLog

    log = HashChainedLog(Path(o.cfg.sovereignty.sovereignty_dir) / "egress.jsonl")
    return {"guard": o.sovereignty.guard.status() if o.sovereignty and o.sovereignty.guard else {"installed": False},
            "entries": log.read(limit=limit), "chain": log.verify().model_dump()}


# =========================================================================== vault
vault = APIRouter(prefix="/vault", tags=["vault"])


@vault.get("")
def vault_status(token: str | None = Depends(bearer)) -> dict:
    """Envelope-encryption state: sealed branches, role key versions, what is decrypted in memory right now."""
    o = orch()
    if o.kms is None:
        raise HTTPException(503, "vault unavailable")
    from workbench.security.vault import vault_status as _vs

    out = _vs(o.cfg, o.kms, o.vault_store)
    out["enabled"] = bool(o.cfg.vault.enabled)
    out["branches_in_memory"] = o.vault.status() if o.vault is not None else {"enabled": False, "branches": [], "in_memory": []}
    out["keyrings"] = o.keyrings.status() if o.keyrings is not None else {}
    principal = o.principal(token)
    out["you"] = {"principal": principal.username, "role": principal.role.value,
                  "can_unwrap": [b for b in o.vault_store.sealed_branches() if principal.role.value in o.kms.wrapped_roles(b)]}
    out["key_events"] = o.kms.key_events.read(limit=30)
    return out


class SealBody(BaseModel):
    shred: bool = Field(default=False, description="Overwrite and delete the plaintext index cache after sealing")


@vault.post("/seal")
def vault_seal(body: SealBody, token: str | None = Depends(bearer)) -> dict:
    """Seal every branch's index into the vault, wrapped for exactly the roles that may read it. Administrators only."""
    principal = _require_role(token, "admin")
    o = orch()
    from workbench.security.vault import seal_index_cache

    sealed = seal_index_cache(o.cfg, o.kms, o.vault_store, o.classifications, shred=body.shred)
    for info in sealed:
        o.security_audit.write("branch_sealed", principal=principal.username, role=principal.role.value,
                               outcome=f"{info.bytes_plain} bytes sealed for {', '.join(info.roles)}", branch=info.branch)
    return {"sealed": [i.model_dump(mode="json") for i in sealed], "shredded": body.shred}


@vault.post("/rotate/{role}")
def vault_rotate(role: str, token: str | None = Depends(bearer)) -> dict:
    """Rotate one role's wrapping key: every branch key it held is re-wrapped, old copies become useless, no data is re-encrypted."""
    principal = _require_role(token, "admin")
    o = orch()
    try:
        ver = o.kms.rotate_role_key(role)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if o.keyrings is not None:
        o.keyrings.wipe_all()             # a keyring holding a key unwrapped under the old version must not survive
    o.security_audit.write("key_rotated", principal=principal.username, role=principal.role.value,
                           outcome=f"{role} wrapping key now v{ver.version}; sessions must re-open their branches", target_role=role)
    return {"role": role, **ver.model_dump(mode="json")}


class RevokeBody(BaseModel):
    branch: str
    role: str


@vault.post("/revoke")
def vault_revoke(body: RevokeBody, token: str | None = Depends(bearer)) -> dict:
    """Remove a role's wrapped copy of one branch key. That role can no longer open the branch, instantly."""
    principal = _require_role(token, "admin")
    o = orch()
    try:
        o.kms.revoke_role(body.branch, body.role)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if o.keyrings is not None:
        o.keyrings.wipe_all()
    if o.vault is not None:
        o.vault.close_orphans(o.auth.active_roles())
    o.security_audit.write("key_revoked", principal=principal.username, role=principal.role.value,
                           outcome=f"{body.role} can no longer unwrap {body.branch}", branch=body.branch, target_role=body.role)
    return {"branch": body.branch, "role": body.role, "roles_with_key": o.kms.wrapped_roles(body.branch)}


@vault.get("/tls")
def tls_state() -> dict:
    return _tls_state(orch())


# =========================================================================== tools & sandbox
tools = APIRouter(prefix="/tools", tags=["tools"])


@tools.get("")
def list_tools() -> dict:
    """The named local tools an agent may call, with their argument schemas."""
    o = orch()
    if o.tools is None:
        raise HTTPException(503, "tool registry unavailable")
    return {"tools": o.tools.describe(), "sandbox": _sandbox_info(o)}


def _sandbox_info(o) -> dict:
    sb = o.sandbox
    if sb is None:
        return {"enabled": False}
    limits = getattr(sb, "limits", None)
    return {"enabled": True, "backend": getattr(sb, "backend", "?"), "python": getattr(sb, "python", ""),
            "limits": limits.model_dump() if hasattr(limits, "model_dump") else {},
            "manifest_hash": getattr(sb, "manifest_hash", ""), "vendor_path": getattr(sb, "vendor_path", None),
            "manifest_error": getattr(sb, "manifest_error", None),
            "root": str(getattr(sb, "root", "")), "workspace_root": str(o.cfg.sandbox.workspace_dir)}


class ToolRunBody(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    session_id: str = "web"


@tools.post("/run")
def run_tool(body: ToolRunBody, token: str | None = Depends(bearer)) -> dict:
    """Run one named tool in the caller's session workspace, over the knowledge the caller may read."""
    o = orch()
    principal = _principal_or_401(token)
    if o.tools is None:
        raise HTTPException(503, "tool registry unavailable")
    tool = o.tools.get(body.tool)
    if tool is None:
        raise HTTPException(404, f"no tool called {body.tool!r}")
    principal, session_key, knowledge = o.guarded_knowledge_for(token, body.session_id)
    ctx = o.tool_context(session_key, principal, knowledge)
    result = tool(body.args, ctx)                  # validates, runs, times, and writes the chained tool log
    out = result.model_dump(mode="json")
    out["principal"] = principal.username
    out["workspace"] = str(ctx.workspace)
    return out


class AgentBody(BaseModel):
    goal: str
    session_id: str = "web"
    max_iterations: int = 8


@tools.post("/agent")
def run_agent(body: AgentBody, token: str | None = Depends(bearer)) -> dict:
    """Agentic execution: plan, call real local tools, iterate; every call is logged in the chained tool log."""
    o = orch()
    principal = _principal_or_401(token)
    if o.tools is None:
        raise HTTPException(503, "tool registry unavailable")
    from workbench.tools.agent_loop import ToolAgent

    principal, session_key, knowledge = o.guarded_knowledge_for(token, body.session_id)
    ctx = o.tool_context(session_key, principal, knowledge)
    agent = ToolAgent(o.tools, ctx, o.llm, max_iterations=body.max_iterations)
    report = agent.run(body.goal)
    out = report.model_dump(mode="json")
    out["principal"] = principal.username
    out["workspace"] = str(ctx.workspace)
    return out


@tools.get("/workspace")
def workspace_files(session_id: str = "web", token: str | None = Depends(bearer)) -> dict:
    o = orch()
    principal = _principal_or_401(token)
    session_key = o.sessions.key_for(principal.username, session_id)
    ctx = o.tool_context(session_key, principal, None)
    files = []
    for p in sorted(ctx.workspace.rglob("*")):
        if p.is_file():
            files.append({"path": str(p.relative_to(ctx.workspace)).replace("\\", "/"), "bytes": p.stat().st_size, "modified": p.stat().st_mtime})
    return {"workspace": str(ctx.workspace), "files": files}


@tools.get("/workspace/file")
def workspace_download(path: str, session_id: str = "web", token: str | None = Depends(bearer)):
    o = orch()
    principal = _principal_or_401(token)
    session_key = o.sessions.key_for(principal.username, session_id)
    ctx = o.tool_context(session_key, principal, None)
    target = (ctx.workspace / path).resolve()
    if ctx.workspace.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "no such workspace file")
    return FileResponse(str(target), filename=target.name)


@tools.get("/calls")
def tool_calls(limit: int = 100, token: str | None = Depends(bearer)) -> dict:
    """The chained log of every tool call made through the API or an agent loop."""
    o = orch()
    principal = _principal_or_401(token)
    from workbench.sovereignty.hashchain import HashChainedLog

    log = HashChainedLog(Path(o.cfg.sandbox.workspace_dir) / "tool_calls.jsonl")
    rows = log.read()
    if principal.role.value != "admin":
        rows = [r for r in rows if r.get("principal") == principal.username or r.get("session", "").startswith(f"{principal.username}__")]
    return {"entries": rows[-limit:], "chain": log.verify().model_dump()}


sandbox = APIRouter(prefix="/sandbox", tags=["sandbox"])


class SandboxBody(BaseModel):
    code: str
    tests: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    task_id: str = ""


@sandbox.post("/run")
def sandbox_run(body: SandboxBody, token: str | None = Depends(bearer)) -> dict:
    """Run code in a fresh, network-less, resource-bounded sandbox; verified = static analysis + the task's own tests."""
    o = orch()
    principal = _principal_or_401(token)
    if o.sandbox is None:
        raise HTTPException(503, "sandbox disabled")
    res = o.sandbox.run(body.code, inputs=body.inputs or None, tests=body.tests, task_id=body.task_id or f"api:{principal.username}")
    o.security_audit.write("sandbox_run", principal=principal.username, role=principal.role.value,
                           outcome=f"exit {res.exit_code}; verified={res.verified}; limit={res.limit_hit or 'none'}", run_id=res.run_id)
    return res.model_dump(mode="json")


@sandbox.get("/runs")
def sandbox_runs(limit: int = 50, token: str | None = Depends(bearer)) -> dict:
    o = orch()
    _principal_or_401(token)
    from workbench.sovereignty.hashchain import HashChainedLog

    log = HashChainedLog(Path(o.cfg.sandbox.root_dir) / "runs.jsonl")
    return {"entries": log.read(limit=limit), "chain": log.verify().model_dump(), "sandbox": _sandbox_info(o)}


@sandbox.get("/manifest")
def sandbox_manifest() -> dict:
    """What the sandbox may import: the pinned stdlib allow-list, the banned modules, and the checksummed vendored files."""
    o = orch()
    try:
        from workbench.sandbox.manifest import ALLOWED_STDLIB, BANNED_IMPORTS, build_manifest

        vendor_dir = getattr(o.sandbox, "vendor_dir", None)
        m = build_manifest(vendor_dir)
        return {"hash": m.hash, "python": m.python, "stdlib_allowed": sorted(ALLOWED_STDLIB), "banned_imports": sorted(BANNED_IMPORTS),
                "vendored": [v.model_dump() for v in m.vendor], "vendor_dir": str(vendor_dir) if vendor_dir else None,
                "verified": getattr(o.sandbox, "manifest_error", None) is None, "error": getattr(o.sandbox, "manifest_error", None)}
    except Exception as exc:
        return {"error": str(exc)}


# =========================================================================== intake (OCR + vision)
intake = APIRouter(prefix="/intake", tags=["intake"])


@intake.post("")
async def intake_file(file: UploadFile = File(...), session_id: str = Form("web"), purpose: str = Form("general"),
                      run_vision: bool = Form(True), max_pages: int = Form(6), create_draft: bool = Form(True),
                      token: str | None = Depends(bearer)) -> dict:
    """On-device OCR + vision over a scanned PDF, photo, P&ID or handwritten note. Low-confidence lines are flagged for review."""
    o = orch()
    principal = _principal_or_401(token)
    from workbench.intake.pipeline import intake_file as _intake

    dest_dir = o.cfg.paths.uploads_dir / principal.username / session_id / "intake"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file.filename or "upload.bin").name
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    result = _intake(dest, llm_or_resources=(o.resources if o.llm.available() else None), threshold=o.cfg.review.ocr_confidence_threshold,
                     run_vision=run_vision, max_pages=max_pages, purpose=purpose)
    out = result.model_dump(mode="json")
    if create_draft and o.drafts is not None:
        from workbench.review.drafts import figures_from_intake

        figures = figures_from_intake(result, threshold=o.cfg.review.ocr_confidence_threshold)
        draft = o.drafts.create("intake", f"Intake: {dest.name}", path=str(dest), figures=figures, session_id=session_id,
                                owner=principal.username, owner_role=principal.role.value, classification=None)
        out["draft"] = draft.public()
    o.security_audit.write("intake", principal=principal.username, role=principal.role.value,
                           outcome=f"{result.kind}: {len(result.flagged)} flagged line(s)", file=dest.name)
    return out


# =========================================================================== deliverables & review
deliverables = APIRouter(prefix="/deliverables", tags=["deliverables"])


class ExportBody(BaseModel):
    response_id: str
    format: str = Field(default="docx", description="docx | pptx | xlsx | md")
    title: str | None = None


@deliverables.post("")
def export_deliverable(body: ExportBody, token: str | None = Depends(bearer)) -> dict:
    """Turn a released answer into a Word / PowerPoint / Excel file and register it as a draft pending sign-off."""
    o = orch()
    principal = _principal_or_401(token)
    try:
        resp = o.responses.load_for(body.response_id, principal)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    from workbench.deliverables.export import export_response

    fmt = body.format.lower()
    if fmt not in ("docx", "pptx", "xlsx", "md"):
        raise HTTPException(400, "format must be docx, pptx, xlsx or md")
    draft = None
    figures = None
    if o.drafts is not None:
        draft = o.drafts.create(fmt, body.title or _title_for(resp), response=resp, session_id=resp.session_id,
                                owner=principal.username, owner_role=principal.role.value,
                                classification=resp.security.classification,
                                ocr_threshold=o.cfg.review.ocr_confidence_threshold, flag_llm_derived=o.cfg.review.flag_llm_derived)
        figures = draft.figures
    out_dir = Path(o.cfg.review.deliverables_dir) / principal.username
    result = export_response(resp, fmt, out_dir, title=body.title or _title_for(resp), figures=figures)
    if draft is not None:
        draft = o.drafts.attach_file(draft.draft_id, Path(result.path))
    o.security_audit.write("deliverable_exported", principal=principal.username, role=principal.role.value,
                           outcome=f"{fmt} from {body.response_id}", draft_id=(draft.draft_id if draft else None),
                           classification=resp.security.classification)
    payload = result.model_dump(mode="json")
    payload["draft"] = draft.public() if draft else None
    payload["download"] = f"/deliverables/{draft.draft_id}/download" if draft else None
    return payload


def _title_for(resp) -> str:
    first = (resp.answer_markdown or "").strip().split("\n")[0][:80]
    return f"{resp.task_type.value.replace('_', ' ').title()}: {first}" if first else resp.task_type.value


@deliverables.get("/responses")
def recent_responses(limit: int = 30, token: str | None = Depends(bearer)) -> list[dict]:
    """Released answers that can be exported: the caller's own, or everyone's for an administrator."""
    o = orch()
    principal = _principal_or_401(token)
    owner = None if principal.role.value == "admin" else principal.username
    return o.responses.recent(owner=owner, limit=limit)


@deliverables.get("/{draft_id}/download")
def download_deliverable(draft_id: str, token: str | None = Depends(bearer)):
    o = orch()
    principal = _principal_or_401(token)
    draft = _draft_for(o, draft_id, principal)
    if not draft.path or not Path(draft.path).exists():
        raise HTTPException(404, "this draft has no file")
    return FileResponse(draft.path, filename=Path(draft.path).name)


drafts = APIRouter(prefix="/drafts", tags=["review"])


def _draft_for(o, draft_id: str, principal):
    if o.drafts is None:
        raise HTTPException(503, "draft registry unavailable")
    try:
        draft = o.drafts.get(draft_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'")) from exc
    if draft is None:
        raise HTTPException(404, f"no draft {draft_id!r}")
    if draft.owner != principal.username and principal.role.value not in ("manager", "admin"):
        raise HTTPException(403, "that draft belongs to someone else")
    return draft


@drafts.get("")
def list_drafts(status: str | None = None, token: str | None = Depends(bearer)) -> dict:
    """Every draft the caller may see, with its pending-sign-off state and open flags. Nothing here is ever auto-approved."""
    o = orch()
    principal = _principal_or_401(token)
    if o.drafts is None:
        raise HTTPException(503, "draft registry unavailable")
    owner = None if principal.role.value in ("manager", "admin") else principal.username
    rows = o.drafts.list(owner=owner, status=status)
    return {"drafts": [d.public() for d in rows], "summary": o.drafts.summary(),
            "reviewer": principal.role.value in ("manager", "admin"), "chain": o.drafts.verify_chain().model_dump()}


@drafts.get("/{draft_id}")
def get_draft(draft_id: str, token: str | None = Depends(bearer)) -> dict:
    o = orch()
    principal = _principal_or_401(token)
    return _draft_for(o, draft_id, principal).public()


class ResolveBody(BaseModel):
    action: str = Field(description="accepted | corrected | removed")
    corrected_value: str | None = None
    note: str = ""


@drafts.post("/{draft_id}/figures/{figure_id}/resolve")
def resolve_figure(draft_id: str, figure_id: str, body: ResolveBody, token: str | None = Depends(bearer)) -> dict:
    """Resolve one flagged figure against its source. Sign-off stays blocked until every flag is resolved."""
    o = orch()
    principal = _principal_or_401(token)
    _draft_for(o, draft_id, principal)
    try:
        draft = o.drafts.resolve_figure(draft_id, figure_id, by=principal.username, action=body.action,
                                        corrected_value=body.corrected_value, note=body.note)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return draft.public()


class SignoffBody(BaseModel):
    note: str = ""


@drafts.post("/{draft_id}/signoff")
def signoff(draft_id: str, body: SignoffBody, token: str | None = Depends(bearer)) -> dict:
    """Sign a draft off. Refused (409) while any flagged figure is unresolved; manager or above."""
    o = orch()
    principal = _principal_or_401(token)
    _draft_for(o, draft_id, principal)
    from workbench.review.drafts import SignoffBlocked

    try:
        draft = o.drafts.sign_off(draft_id, by=principal.username, role=principal.role.value, note=body.note)
    except SignoffBlocked as exc:
        o.security_audit.write("signoff_blocked", principal=principal.username, role=principal.role.value,
                               outcome=f"{len(exc.open_flags)} open flag(s)", draft_id=draft_id)
        raise HTTPException(409, {"detail": "sign-off blocked: resolve every flagged figure first",
                                  "open_flags": [f.model_dump(mode="json") for f in exc.open_flags]}) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    o.security_audit.write("draft_signed_off", principal=principal.username, role=principal.role.value,
                           outcome=body.note or "signed off", draft_id=draft_id)
    return draft.public()


@drafts.post("/{draft_id}/reject")
def reject(draft_id: str, body: SignoffBody, token: str | None = Depends(bearer)) -> dict:
    o = orch()
    principal = _principal_or_401(token)
    _draft_for(o, draft_id, principal)
    try:
        draft = o.drafts.reject(draft_id, by=principal.username, role=principal.role.value, note=body.note)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    o.security_audit.write("draft_rejected", principal=principal.username, role=principal.role.value,
                           outcome=body.note or "rejected", draft_id=draft_id)
    return draft.public()


ROUTERS = [models, sovereignty, vault, tools, sandbox, intake, deliverables, drafts]
