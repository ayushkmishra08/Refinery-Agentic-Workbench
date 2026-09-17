"""Orchestrator: UserRequest -> FinalResponse through Phases 0-5, with tracked runs.

Phase 0/1  classify -> resolve context (entities, values, symptom, session) -> safety gate
Phase 2    build the Engineering Context Package along the cheapest retrieval route
Phase 3    plan (template + extensions + validation, LLM refinement for planning requests)
Phase 4    execute the DAG; replan on missing evidence (bounded)
Phase 5    verification -> governance (policy, confidence, HITL, citations) -> FinalResponse
Runs are registered so /runs/{id}/events can stream progress and /runs/{id}/btw can answer
status questions while the run is still going. Resources are released when idle.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
import traceback

from workbench.agents.base import AgentServices
from workbench.agents.composer import AnswerComposerAgent
from workbench.agents.context_resolver import ContextResolverAgent
from workbench.agents.governance import GovernanceAgent
from workbench.agents.planner import PlannerAgent
from workbench.agents.task_classifier import TaskClassifierAgent
from workbench.agents.verification import VerificationAgent
from workbench.config import WorkbenchConfig, load_config
from workbench.core.blocks import AuditPhase
from workbench.core.context import ContextPackage
from workbench.core.events import EventBus, ProgressEvent
from workbench.core.evidence import Confidence
from workbench.core.plan import Plan, PlanStep, StepStatus
from workbench.core.request import SafetyStatus, StructuredRequest, TaskType, UserRequest
from workbench.core.result import AgentResult, FinalResponse
from workbench.llm.client import build_llm
from workbench.memory.followup import resolve_followup
from workbench.memory.session import SessionStore, Turn
from workbench.orchestration import narration
from workbench.orchestration.executor import Executor, ReplanRequested
from workbench.orchestration.hitl import HITLRegistry
from workbench.orchestration.runs import RunRegistry, RunState
from workbench.orchestration.status_agent import status_reply
from workbench.services.audit_store import AuditStore
from workbench.services.context_builder import ContextBuilder
from workbench.services.knowledge import build_knowledge_service
from workbench.services.resources import ResourceManager
from workbench.security import leakcheck
from workbench.security.audit import SecurityAudit
from workbench.security.auth import AuthService, Principal
from workbench.security.classification import ClassificationRegistry
from workbench.security.escalation import EscalationError, EscalationStore, ScopeManifest
from workbench.security.guard import GuardedKnowledgeService, scope_probe
from workbench.security.policy import AccessPolicy
from workbench.security.roles import ROLE_LEVEL, TAG_LEVEL, Role, Tag, schema as role_schema, title as role_title


def _a(word: str) -> str:
    """"an Administrator", "a Manager" — it appears in every refusal, so it should read right."""
    return ("an " if word[:1].upper() in "AEIOU" else "a ") + word

logger = logging.getLogger(__name__)


def _counts(res: AgentResult, duration_ms: int | None = None) -> dict:
    """The payload every ``agent_finished`` event carries; the CLI display and the SSE client read these keys."""
    return {"ok": res.ok, "duration_ms": res.duration_ms if duration_ms is None else duration_ms, "llm_calls": res.llm_calls,
            "blocks": len(res.blocks), "evidence": len(res.evidence), "missing": res.missing}


def _model_used(llm, res: AgentResult) -> str | None:
    """The model name to attribute to a stage, or None when it ran without the LLM."""
    return (getattr(llm, "model", None) or getattr(llm, "name", None)) if res.llm_calls else None


class Orchestrator:
    def __init__(self, cfg: WorkbenchConfig | None = None, knowledge=None, llm=None, warm_start: bool | str = True) -> None:
        """warm_start: False = nothing preloaded; True = embedder import in the background (~6 s); "full" = embedder + reranker (server mode, ~35 s)."""
        self.cfg = cfg or load_config()
        self.knowledge = knowledge or build_knowledge_service(self.cfg)
        self.llm = llm or build_llm(self.cfg)
        self.sessions = SessionStore(self.cfg.paths.sessions_dir, self.cfg.session_ttl_turns)
        self.audit = AuditStore(self.cfg.paths.audit_dir)
        self.hitl = HITLRegistry(self.cfg.paths.audit_dir)
        self.runs = RunRegistry()
        primary = getattr(self.knowledge, "primary", self.knowledge)
        idle = 45 if self.cfg.profile.name in ("gpu_4gb", "cpu") else 180
        self.resources = ResourceManager(self.llm, primary, idle_unload_seconds=idle)
        self.context_builder = ContextBuilder(self.knowledge, self.cfg)
        self.backend_name = self.cfg.resolve_backend()
        self.auth = AuthService(self.cfg.paths.security_dir, token_ttl_seconds=self.cfg.security.token_ttl_seconds)
        self.classifications = ClassificationRegistry(self.cfg.paths.security_dir / "classifications.json")
        self.policy = AccessPolicy(self.classifications, enabled=self.cfg.security.enabled)
        self.escalations = EscalationStore(self.cfg.paths.security_dir,
                                           grant_ttl=self.cfg.security.grant_ttl_seconds,
                                           request_ttl=self.cfg.security.request_ttl_seconds)
        self.security_audit = SecurityAudit(self.cfg.paths.security_dir)
        # Documents uploaded during a conversation, keyed by the session they belong to
        # (``<username>__<session_id>``). They are deliberately *not* added to the shared
        # knowledge service: an upload is one person's working file, it has not been classified by
        # anyone, and it must not become visible to another session, another role, or the next
        # conversation. See `session_uploads` and `_knowledge_for`.
        self.session_uploads: dict[str, object] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._warm_level = warm_start
        if warm_start:
            threading.Thread(target=self._warm, daemon=True).start()

    # ------------------------------------------------------------------ warm-up (server mode)
    def _warm(self) -> None:
        """Load CPU models in the background so the first semantic query does not pay for them."""
        try:
            primary = getattr(self.knowledge, "primary", self.knowledge)
            emb = getattr(primary, "_embedder", None)
            if emb is not None and self.cfg.retrieval.use_vectors:
                emb.encode("warm up")
            rr = getattr(primary, "_reranker", None)
            if rr is not None and self.cfg.retrieval.use_reranker and self._warm_level == "full":
                rr.scores("warm up", ["warm up passage"])
            logger.info("warm start complete")
        except Exception as exc:
            logger.warning("warm start failed: %s", exc)

    # ------------------------------------------------------------------ public API
    def ask(self, request: UserRequest, on_event=None) -> FinalResponse:
        rs = self.runs.create(request.session_id, request.text)
        return self._run(rs, request, on_event)

    def start(self, request: UserRequest, on_event=None) -> RunState:
        rs = self.runs.create(request.session_id, request.text)
        t = threading.Thread(target=self._run, args=(rs, request, on_event), daemon=True)
        self._threads[rs.run_id] = t
        t.start()
        return rs

    def btw(self, question: str, run_id: str | None = None, session_id: str | None = None):
        rs = self.runs.get(run_id) if run_id else self.runs.latest(session_id)
        if rs is None:
            from workbench.core.blocks import CalloutBlock

            return [CalloutBlock(id="none", level="info", markdown="No run is active or recorded for this session.")]
        rs.resources = self.resources.status()
        return status_reply(rs, question)

    def shutdown(self) -> None:
        self.resources.release_all()

    # ------------------------------------------------------------------ access control
    def login(self, username: str, password: str, *, label: str = "", code: str | None = None) -> Principal:
        """Verify a password, then the authenticator code the role requires. Raises on refusal.

        ``MfaRequired`` means the password was right and a code is still owed; it carries no
        token, and the half-finished attempt is recorded so an administrator can see it.
        """
        from workbench.security.auth import AuthError, MfaRequired
        from workbench.security.totp import TotpError

        try:
            principal = self.auth.authenticate_with_mfa(
                username, password, code=code, label=label,
                required_roles=self.cfg.security.mfa_required_roles)
        except MfaRequired as exc:
            self.security_audit.write("mfa_challenge", principal=exc.username, role=exc.role,
                                      outcome="password accepted; authenticator code required", label=label)
            raise
        except TotpError as exc:
            self.security_audit.write("mfa_failed", principal=(username or "?"), outcome=str(exc), label=label)
            raise
        except AuthError as exc:
            self.security_audit.write("lockout" if exc.locked_until else "login_failed",
                                      principal=(username or "?"), outcome=str(exc), label=label)
            raise
        self.security_audit.write("login", principal=principal.username, role=principal.role.value,
                                  outcome="signed in", label=label, must_change=principal.must_change_password,
                                  second_factor=principal.mfa_satisfied)
        return principal

    # ------------------------------------------------------------------ second factor
    def mfa_status(self, token: str | None) -> dict:
        """What the caller still owes, and whether this deployment asks for it at all."""
        principal = self.principal(token)
        cred = self.auth._users.get(principal.username) if principal.authenticated else None
        required = principal.role.value in self.cfg.security.mfa_required_roles
        return {"username": principal.username, "role": principal.role.value,
                "required_for_role": required, "enrolled": bool(cred and cred.mfa_enrolled),
                "enrolment_pending": required and not (cred and cred.mfa_enrolled),
                "required_roles": list(self.cfg.security.mfa_required_roles),
                "demo_codes": bool(self.cfg.security.mfa_demo_codes)}

    def begin_mfa_enrolment(self, token: str | None) -> dict:
        principal = self.require_principal(token)
        out = self.auth.begin_enrolment(principal.username)
        self.security_audit.write("mfa_enrolment_started", principal=principal.username,
                                  role=principal.role.value, outcome="secret issued, not yet active")
        return out

    def confirm_mfa_enrolment(self, token: str | None, code: str) -> dict:
        principal = self.require_principal(token)
        self.auth.confirm_enrolment(principal.username, code)
        self.security_audit.write("mfa_enrolled", principal=principal.username, role=principal.role.value,
                                  outcome="authenticator active on this account")
        return {"username": principal.username, "enrolled": True}

    def disable_mfa(self, token: str | None, username: str | None = None) -> dict:
        """Remove an authenticator. One's own, or anyone's when an administrator asks."""
        principal = self.require_principal(token)
        target = (username or principal.username).lower()
        if target != principal.username and principal.role is not Role.ADMIN:
            raise PermissionError("Only an administrator can remove another account's authenticator.")
        self.auth.disable_mfa(target)
        self.security_audit.write("mfa_disabled", principal=principal.username, role=principal.role.value,
                                  outcome=f"authenticator removed from {target}", target=target)
        return {"username": target, "enrolled": False}

    def require_principal(self, token: str | None) -> Principal:
        principal = self.principal(token)
        if not principal.authenticated:
            raise PermissionError("Sign in first.")
        return principal

    def logout(self, token: str | None) -> bool:
        principal = self.principal(token)
        revoked = self.auth.revoke(token)
        if revoked:
            self.security_audit.write("logout", principal=principal.username, role=principal.role.value,
                                      outcome="token revoked")
        return revoked

    def principal(self, token: str | None) -> Principal:
        if not self.cfg.security.enabled:
            return Principal(username="unrestricted", role=Role.ADMIN, display_name="Access control disabled",
                             authenticated=True)
        return self.auth.principal_for(token)

    #: "this catalogue", "the attached pdf", "the uploaded datasheet" — a demonstrative followed by
    #: a word for a document, with room for an adjective or two in between.
    _ABOUT_THE_ATTACHMENT = re.compile(
        r"\b(this|that|these|the)\s+(\w+\s+){0,2}"
        r"(document|documents|manual|catalogue|catalog|file|pdf|attachment|upload|report|datasheet|brochure|spec|specification|book)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _asks_about_the_attachment(cls, text: str, uploaded: list[str]) -> bool:
        """Is this question about the file the person just attached, rather than the corpus?

        "What does this catalogue cover?" is not a question about the refinery. Answering it from
        the corpus — which is what happens when every document is in scope — produces a confident
        answer about the wrong document, and the person has no way to tell. When someone points at
        *this* document and there is an attachment in front of them, that is the document they mean.
        """
        if not uploaded or not text:
            return False
        if cls._ABOUT_THE_ATTACHMENT.search(text):
            return True
        lowered = text.lower()
        return any(word in lowered for word in ("attached", "uploaded", "the attachment"))

    def _survey_instead_of_clarifying(self, req, uploaded: list[str], events):
        """Someone pointing at their own attachment gets it surveyed, not a question back.

        "Tell me about this document" names no equipment, so the resolver finds none and the run
        heads for a clarification — *which pump did you mean?* — which reads as though the
        workstation has not noticed the file at all. It is also unanswerable: the person is asking
        what is in there precisely because they do not know what to name yet.

        The classifier catches the common phrasings, but no pattern list covers how people
        actually write, so this is the backstop: when the question points at an attachment and
        nothing resolved, survey the document. It only fires when there *is* an attachment, only
        when the question refers to it, and only when there is no entity to answer about — so an
        ordinary question that happens to mention a file is untouched.
        """
        if not uploaded or req.entities:
            return req
        if req.task_type not in (TaskType.AMBIGUOUS, TaskType.LOOKUP, TaskType.EXPLANATION):
            return req
        if not self._asks_about_the_attachment(req.original.text, uploaded):
            return req
        events.emit("agent_finished", phase="0/1 Understanding", agent="context_resolver", step_id="resolve-attachment",
                    message=f"question is about {', '.join(uploaded)}; surveying it rather than asking which equipment",
                    decision=f"task type {req.task_type.value} -> inventory",
                    thinking=("The question points at a document the person attached and names no equipment. "
                              "Asking them which pump they meant would be answering a question they have not "
                              "asked: they want to know what is in the file. Survey it instead."))
        return req.model_copy(update={"task_type": TaskType.INVENTORY, "ambiguities": []})

    def upload_backend(self, session_key: str):
        """The backend holding this session's uploaded documents, or None."""
        return self.session_uploads.get(session_key)

    def upload_document_ids(self, session_key: str) -> list[str]:
        backend = self.session_uploads.get(session_key)
        if backend is None:
            return []
        try:
            return [d.document_id for d in backend.documents()]
        except Exception:
            return []

    def _knowledge_for(self, session_key: str):
        """The corpus this session can see: the shared one, plus its own uploads if it has any."""
        backend = self.session_uploads.get(session_key)
        if backend is None:
            return self.knowledge
        from workbench.services.backends.composite import CompositeKnowledgeService

        return CompositeKnowledgeService(self.knowledge, [backend])

    def promote_upload(self, token: str | None, session_id: str, document_id: str, tag: str | None = None) -> dict:
        """Move a document uploaded into a conversation into the shared knowledge layer.

        This is the deliberate act that an upload is deliberately *not*. Until it happens the file
        is one person's working copy: answered from inside their own conversation, invisible to
        everyone else, and gone when they drop it. Promotion is the opposite on every count — the
        document is parsed into the knowledge layer's own directories, joins the shared corpus, and
        is classified so that the ordinary access rules start applying to it.

        It needs a manager, because the person doing it is deciding what everyone else will be
        able to read, and because an unclassified document defaults to the most restricted tag
        rather than the least.
        """
        principal = self.require_principal(token)
        if ROLE_LEVEL[principal.role] < ROLE_LEVEL[Role.MANAGER]:
            raise PermissionError("Adding a document to the knowledge layer needs a Manager or above.")

        session_key = self.sessions.key_for(principal.username, session_id)
        session = self.sessions.load(session_key, owner=principal.username, owner_role=principal.role.value)
        row = next((d for d in session.uploaded_documents if d.get("document_id") == document_id), None)
        if row is None or not row.get("path"):
            raise KeyError(f"{document_id!r} was not uploaded into this conversation.")
        source = Path(row["path"])
        if not source.exists():
            raise KeyError(f"The uploaded file for {document_id!r} is no longer on disk.")

        from workbench.security.classification import UPLOAD_TAG
        from workbench.services.ingest import SessionDocumentsBackend, build_index_from_pdf

        idx = build_index_from_pdf(self.cfg, source, persist=True)
        self.knowledge.add(SessionDocumentsBackend([idx], self.cfg))
        chosen = Tag(tag.strip().upper()) if tag else UPLOAD_TAG
        dc = self.classifications.assign(
            document_id, chosen,
            reason=f"uploaded by {principal.username} and promoted into the knowledge layer",
            by=f"{principal.role.value}:{principal.username}",
            title=idx.info.title or document_id,
        )
        self.drop_session_uploads(session_key)      # it is shared now; the private copy would shadow it
        self.security_audit.write("upload_promoted", principal=principal.username, role=principal.role.value,
                                  document=document_id, outcome=f"classified {dc.tag.value}, readable by {dc.min_role} and above")
        logger.info("upload %s promoted to the knowledge layer by %s as %s", document_id, principal.username, dc.tag.value)
        return {"document_id": document_id, "tag": dc.tag.value, "roles": [r.value for r in dc.readable_by],
                "min_role": dc.min_role, "stats": idx.build_stats}

    def drop_session_uploads(self, session_key: str) -> int:
        """Forget a session's uploads. Returns how many documents were dropped."""
        backend = self.session_uploads.pop(session_key, None)
        if backend is None:
            return 0
        try:
            return len(backend.documents())
        except Exception:
            return 1

    def access_for(self, token: str | None, *, grant=None):
        """(principal, decision) for a token, against the documents currently loaded."""
        principal = self.principal(token)
        return principal, self.policy.decide(principal, self.knowledge.documents(), grant=grant)

    def document_catalogue(self, token: str | None = None) -> list[dict]:
        """Every loaded document with its tag and who may read it.

        Names, tags and reader lists are deliberately public to a signed-in caller: an access
        request is impossible against something you have not been told exists. The **page count**
        is not — it sizes the tier above — so it is reported only for documents this caller can
        already open. Passing no token keeps the old behaviour for internal callers.
        """
        readable: set[str] | None = None
        if token is not None:
            _, decision = self.access_for(token)
            readable = set(decision.allowed)
        out = []
        for d in self.knowledge.documents():
            dc = self.classifications.classify(d)
            row = {"document_id": d.document_id, "title": d.title or d.document_id,
                   "tag": dc.tag.value, "min_role": dc.min_role, "reason": dc.reason,
                   "assigned_by": dc.assigned_by,
                   "roles": [r.value for r in dc.readable_by], "compartmented": dc.compartmented}
            if readable is None or d.document_id in readable:
                row["pages"] = d.total_pages
            out.append(row)
        return sorted(out, key=lambda r: (r["tag"], r["document_id"]))

    # kept for callers that predate the tag model
    restricted_documents = document_catalogue

    # ------------------------------------------------------------------ the knowledge layer
    def knowledge_tree(self, token: str | None = None) -> dict:
        """The knowledge layer as branches, with the caller's reach marked on each one.

        One branch per document: that is the unit access is granted in, so it is the unit the
        screen should show. A branch the caller may read is opened — its chapters and its counts
        are listed, because they are already theirs to read. A branch they may not is named and
        nothing more: its title, its classification, and who to ask. No chapter list, no counts,
        no page total. Knowing that a document exists is what makes an access request possible;
        knowing how big it is is a measurement of the tier above, and that stays shut.
        """
        principal, decision = self.access_for(token)
        primary = getattr(self.knowledge, "primary", self.knowledge)
        allowed = set(decision.allowed)
        branches = []
        for doc in self.knowledge.documents():
            dc = self.classifications.classify(doc)
            readable = doc.document_id in allowed
            branch = {
                "document_id": doc.document_id,
                "title": doc.title or doc.document_id,
                "tag": dc.tag.value,
                "reason": dc.reason,
                "roles": [r.value for r in dc.readable_by],
                "compartmented": dc.compartmented,
                "readable": readable,
                "assigned_by": dc.assigned_by,
            }
            if readable:
                branch["pages"] = doc.total_pages
                branch["counts"] = self._branch_counts(primary, doc.document_id)
                branch["chapters"] = self._branch_chapters(doc.document_id)
            else:
                branch["ask"] = self._lowest_reader(dc, principal)
            branches.append(branch)
        branches.sort(key=lambda b: (not b["readable"], b["document_id"]))
        return {
            "role": principal.role.value,
            "principal": principal.username,
            "access_control": self.cfg.security.enabled,
            "readable": sum(1 for b in branches if b["readable"]),
            "locked": sum(1 for b in branches if not b["readable"]),
            "branches": branches,
        }

    @staticmethod
    def _branch_counts(primary, document_id: str) -> dict:
        try:
            counts = primary.stats_for([document_id])
        except Exception:                       # a backend without per-document stats
            return {}
        return {k: v for k, v in counts.items() if k != "documents"}

    def _branch_chapters(self, document_id: str) -> list[dict]:
        try:
            rows = self.knowledge.chapters(document_id) or []
        except Exception:
            return []
        out = []
        for c in rows:
            title = str(c.get("title") or c.get("name") or "").strip()
            if not title:
                continue
            out.append({"number": c.get("number") or c.get("chapter") or None, "title": title[:120]})
        return out[:60]

    @staticmethod
    def _lowest_reader(dc, principal) -> str | None:
        """Who a locked branch should be asked for — the lowest role that can actually open it."""
        readers = [r for r in dc.readable_by if r is not principal.role]
        return readers[0].value if readers else None

    def set_document_roles(self, token: str | None, document_id: str, roles: list[str] | None) -> dict:
        """Pin (or clear) the reader allowlist on one branch. Administrators only.

        Clearing it hands the branch back to the tag ladder rather than opening it, so a mistake
        here narrows access or leaves it where it was; it cannot throw a door open.
        """
        principal = self.require_principal(token)
        if principal.role is not Role.ADMIN:
            raise PermissionError("Only an administrator can change who reads a document.")
        known = {d.document_id for d in self.knowledge.documents()}
        if document_id not in known:
            raise KeyError(f"No document called {document_id!r} is loaded.")
        dc = self.classifications.set_roles(document_id, roles, by=f"administrator:{principal.username}")
        self.security_audit.write("document_roles_set", principal=principal.username, role=principal.role.value,
                                  document=document_id,
                                  outcome=("allowlist " + ", ".join(dc.roles)) if dc.roles is not None else "back to the tag ladder")
        return {"document_id": document_id, "tag": dc.tag.value, "roles": [r.value for r in dc.readable_by],
                "compartmented": dc.compartmented, "min_role": dc.min_role}

    def security_overview(self, token: str | None = None) -> dict:
        """The whole security posture in one object: schema, documents, and the caller's place in it."""
        principal, decision = self.access_for(token)
        return {
            "enabled": self.cfg.security.enabled,
            "schema": role_schema(),
            "documents": self.document_catalogue(token),
            "you": {"principal": principal.username, "role": principal.role.value,
                    "authenticated": principal.authenticated, "level": principal.level,
                    "readable_tags": principal.tags,
                    "readable_documents": decision.allowed, "withheld_documents": decision.denied},
            "escalation": {"enabled": self.cfg.security.escalation_enabled,
                           "your_approver": decision.escalation_target(),
                           "key_lifetime_minutes": self.cfg.security.grant_ttl_seconds // 60,
                           "uses_per_key": self.cfg.security.grant_max_uses},
        }

    def workspace_stats(self, token: str | None = None) -> dict:
        """What the signed-in caller's workspace actually contains.

        Everything here is counted **after** the access filter, so two roles looking at the same
        dashboard see different totals — which is the point: a user should not learn the size of
        the CDU manual from a statistics panel. Nothing about the host machine is reported; this
        describes the corpus and the queue, not the hardware.
        """
        principal, decision = self.access_for(token)
        catalogue = {d["document_id"]: d for d in self.document_catalogue()}
        readable = [catalogue[d] for d in decision.allowed if d in catalogue]
        primary = getattr(self.knowledge, "primary", self.knowledge)
        scope = decision.allowed if self.cfg.security.enabled else None
        knowledge = primary.stats_for(scope) if hasattr(primary, "stats_for") else {}
        by_tag: dict[str, int] = {}
        for d in readable:
            by_tag[d["tag"]] = by_tag.get(d["tag"], 0) + 1
        runs = self.runs.all()
        mine = [r for r in runs if r.session_id.startswith(f"{principal.username}__")] if principal.authenticated else []
        requests = self.my_requests(token) if principal.authenticated else []
        try:
            queue = self.pending_approvals(token)
        except Exception:                                    # not an approver, or escalation is off
            queue = []
        return {
            "principal": principal.username, "role": principal.role.value,
            "documents": {"readable": len(readable), "withheld": len(decision.denied),
                          "total": len(catalogue), "by_tag": by_tag,
                          "pages": sum(int(d.get("pages") or 0) for d in readable)},
            "knowledge": {k: knowledge.get(k, 0) for k in ("entities", "claims", "relations", "procedures", "chunks")},
            "activity": {"runs_total": len(runs), "runs_active": len(self.runs.active()), "runs_mine": len(mine)},
            "escalation": {"my_open_requests": sum(1 for r in requests if r.get("status") == "pending"),
                           "my_requests": len(requests), "awaiting_my_approval": len(queue)},
            "answers": {"effort": self.cfg.effort.name, "llm": getattr(self.llm, "model", None) or self.llm.name,
                        "llm_available": self.llm.available(), "composed": self.cfg.llm.use_llm_for_answer},
        }

    # ------------------------------------------------------------------ escalation
    def scope_for_question(self, principal: Principal, question: str, withheld: list[str]) -> ScopeManifest:
        """Which restricted records bear on a question — as opaque ids, never content.

        Runs the resolver over the question first so the probe has entity uids to work with, then
        reduces everything it finds to record ids. The caller may show the manifest to the
        requester; it says how much material exists, never what it says.
        """
        if not withheld:
            return ScopeManifest()
        services = AgentServices(knowledge=self.knowledge, llm=self.llm, cfg=self.cfg, principal=principal)
        res = AgentResult(agent="context_resolver", step_id="scope")
        try:
            req = ContextResolverAgent(services).resolve(UserRequest(text=question), TaskClassifierAgent(services).classify(
                UserRequest(text=question), AgentResult(agent="task_classifier", step_id="scope-classify"), None), None, res)
            uids = req.entity_uids()
        except Exception as exc:
            logger.warning("scope resolution failed: %s", exc)
            uids = []
        # Least privilege in the escalation itself. Two passes, in this order:
        #
        #   1. the equipment the question named, tier by tier from the lowest. A question the
        #      desalter manual answers goes to a manager, not to an administrator — routing
        #      everything to the highest authority is how approval turns into a rubber stamp.
        #   2. only if the question named no equipment at all, a free-text sweep over everything
        #      withheld. Broad, so it is the fallback rather than the rule.
        by_tag: dict[Tag, list[str]] = {}
        for doc in withheld:
            by_tag.setdefault(self.classifications.tag_of(doc), []).append(doc)
        for tag in sorted(by_tag, key=lambda t: TAG_LEVEL[t]):
            found, tags = scope_probe(self.knowledge, withheld_document_ids=by_tag[tag], question=question,
                                      entity_uids=uids, registry=self.classifications, entities_only=True)
            manifest = ScopeManifest.build(found, tags, self._readers_for(found))
            if not manifest.is_empty:
                return manifest
        found, tags = scope_probe(self.knowledge, withheld_document_ids=withheld, question=question,
                                  entity_uids=uids, registry=self.classifications)
        return ScopeManifest.build(found, tags, self._readers_for(found))

    def _readers_for(self, records_by_document: dict) -> dict[str, list[str]]:
        """Who may read each document in a scope — the routing input for the request it becomes."""
        return {doc: [r.value for r in self.classifications.readers_of(doc)] for doc in records_by_document}

    def request_access(self, token: str | None, question: str, *, session_id: str = "") -> dict:
        """Raise an access request for a question the caller's role cannot answer."""
        principal, decision = self.access_for(token)
        if not principal.authenticated:
            raise EscalationError("Sign in before requesting access.")
        if not decision.withheld:
            raise EscalationError("Nothing is being withheld from you; that question can be answered as you are.")
        scope = self.scope_for_question(principal, question, decision.denied)
        request = self.escalations.raise_request(requester=principal.username, requester_role=principal.role,
                                                 question=question, scope=scope, session_id=session_id)
        self.security_audit.write("request_raised", principal=principal.username, role=principal.role.value,
                                  outcome=f"raised to {request.approver_role.value}", request_id=request.request_id,
                                  documents=scope.document_ids, records=len(scope.record_ids), question=question)
        return {"request": request.model_dump(mode="json"), "approver_role": request.approver_role.value,
                "summary": scope.summary()}

    def pending_approvals(self, token: str | None, *, include_all: bool = False) -> list[dict]:
        """The approval queue for the caller's role, with the content they are deciding on."""
        principal = self.principal(token)
        if not principal.authenticated:
            raise EscalationError("Sign in to see the approval queue.")
        rows = []
        for r in self.escalations.pending_for(principal.role, include_all=include_all):
            row = r.model_dump(mode="json")
            row["preview"] = self.preview_scope(principal, r)
            rows.append(row)
        return rows

    def preview_scope(self, principal: Principal, request) -> list[dict]:
        """What the approver is being asked to release, in full.

        The approver is cleared for this material — that is the whole point of asking them — so
        they read the actual passages before deciding. An approval given blind is not a control.
        """
        if not self.policy.may_read(principal, max(request.scope.tags, key=lambda t: t.value) if request.scope.tags else Tag.SECRET):
            return [{"note": "You are not cleared to preview this material, and cannot decide this request."}]
        view = GuardedKnowledgeService(self.knowledge, None, enabled=False)
        out = []
        for rid in request.scope.record_ids[:12]:
            kind, _, ident = rid.partition(":")
            try:
                if kind == "claim":
                    row = next((c for c in view.search_claims(limit=10_000) if c.claim_id == ident), None)
                    if row:
                        out.append({"id": rid, "kind": kind, "document": row.document_id, "page": row.page,
                                    "text": f"{row.subject}: {row.predicate} = {row.value} {row.unit or ''}".strip()})
                elif kind == "chunk":
                    row = view.get_chunk(ident)
                    if row:
                        out.append({"id": rid, "kind": kind, "document": row.document_id, "page": row.page_start,
                                    "text": (row.text or "")[:400]})
                elif kind == "procedure":
                    row = view.get_procedure(ident)
                    if row:
                        out.append({"id": rid, "kind": kind, "document": row.document_id, "page": row.page_start,
                                    "text": f"{row.title} ({row.procedure_type}, {len(row.steps)} steps)"})
                else:
                    out.append({"id": rid, "kind": kind, "document": "", "page": None, "text": "(relationship)"})
            except Exception as exc:
                logger.debug("preview of %s failed: %s", rid, exc)
        return out

    def approve_request(self, token: str | None, request_id: str, *, note: str = "") -> dict:
        principal = self.principal(token)
        if not principal.authenticated:
            raise EscalationError("Sign in to decide an access request.")
        request, grant, key = self.escalations.approve(
            request_id, approver=principal.username, approver_role=principal.role, note=note,
            ttl_seconds=self.cfg.security.grant_ttl_seconds)
        grant.max_uses = self.cfg.security.grant_max_uses
        self.security_audit.write("request_approved", principal=principal.username, role=principal.role.value,
                                  outcome=f"grant {grant.grant_id} issued to {request.requester}",
                                  request_id=request.request_id, grant_id=grant.grant_id,
                                  documents=grant.document_ids, records=len(grant.record_ids),
                                  expires_in_minutes=grant.minutes_left())
        return {"request": request.model_dump(mode="json"), "grant": grant.model_dump(mode="json", exclude={"key_digest", "signature"}),
                "key": key, "expires_in_minutes": grant.minutes_left(), "requester": request.requester}

    def deny_request(self, token: str | None, request_id: str, *, note: str = "") -> dict:
        principal = self.principal(token)
        if not principal.authenticated:
            raise EscalationError("Sign in to decide an access request.")
        request = self.escalations.deny(request_id, approver=principal.username, approver_role=principal.role, note=note)
        self.security_audit.write("request_denied", principal=principal.username, role=principal.role.value,
                                  outcome=note or "refused", request_id=request.request_id)
        return request.model_dump(mode="json")

    def my_requests(self, token: str | None) -> list[dict]:
        principal = self.principal(token)
        return [r.model_dump(mode="json") for r in self.escalations.requests_of(principal.username)]

    # ------------------------------------------------------------------ sessions
    def session_for(self, session_id: str, token: str | None = None):
        """One principal's conversation. Sessions are namespaced, so the caller must be named.

        This is the only supported way to read a session: asking for a bare id would let anyone
        who guesses one read the previous answers inside it.
        """
        principal = self.principal(token)
        return self.sessions.load(self.sessions.key_for(principal.username, session_id),
                                  owner=principal.username, owner_role=principal.role.value)

    # ------------------------------------------------------------------ the pipeline
    def _run(self, rs: RunState, request: UserRequest, on_event=None) -> FinalResponse:
        events = EventBus()
        events.subscribe(rs.note_event)
        if on_event:
            events.subscribe(on_event)
        audit_id = self.audit.new_id()
        started = time.time()
        phases: list[AuditPhase] = []
        self.resources.begin_request()

        # ---------------- Phase -1: who is asking ---------------------------------------------
        # Identity is resolved before anything reads a document, before the session is opened,
        # and before the question is even classified. Nothing downstream can widen it.
        principal = self.principal(request.auth_token)
        rs.principal = principal.describe()
        grant, key_error = self._redeem_key(request, principal, events)
        principal, access = self.access_for(request.auth_token, grant=grant)
        session_key = self.sessions.key_for(principal.username, request.session_id)
        session = self.sessions.load(session_key, owner=principal.username, owner_role=principal.role.value)
        uploaded = self.upload_document_ids(session_key)
        self.audit.write(request.session_id, audit_id, "request", {"text": request.text, "run_id": rs.run_id, "principal": principal.username, "role": principal.role.value, "attachments": [a.model_dump() for a in request.attachments]})
        self.audit.write(request.session_id, audit_id, "access", access.audit_payload())
        self.security_audit.write(
            "access_denied" if access.nothing_allowed else ("access_partial" if access.denied else "access_allowed"),
            principal=principal.username, role=principal.role.value,
            outcome=access.message() or "all loaded documents readable",
            allowed=access.allowed, withheld=access.denied, grant_id=access.grant_id, question=request.text[:200])
        # A caller cleared for nothing in the corpus can still ask about a file they uploaded
        # themselves, so the refusal only stands when there is genuinely nothing to read.
        if access.nothing_allowed and not uploaded and self.cfg.security.enabled:
            events.emit("access_denied", phase="0 Access", message=access.message(),
                        data={"role": principal.role.value, "required_roles": access.required_roles()})
            resp = self._unauthorized(request, access, principal, audit_id, started, phases)
            rs.final, rs.final_status, rs.finished = resp, resp.status, time.time()
            self.resources.end_request()
            return resp
        if access.denied:
            events.emit("warning", phase="0 Access", message=access.message())
        # A document this person uploaded into this conversation is readable by them whatever their
        # role: it is their own file, it never entered the knowledge layer, and nobody classified
        # it. It is reachable only through this session's backend, so no other session or role can
        # see it however the question is phrased. Adding its id to the allowed set — rather than
        # bypassing the guard — keeps one code path: the same filter, and the same release check
        # over the finished answer.
        readable = list(access.allowed) + uploaded
        if uploaded and self._asks_about_the_attachment(request.text, uploaded):
            # Narrow the whole run to the attachment. The guard is the only place document scope
            # is decided, so restricting it here scopes retrieval, the graph walk and the release
            # check together, with no agent needing to know about it.
            readable = list(uploaded)
            events.emit("phase_started", phase="0 Access",
                        message=f"This question is about the attached document; the answer is scoped to {', '.join(uploaded)}")
        elif uploaded:
            events.emit("phase_started", phase="0 Access",
                        message=f"{len(uploaded)} uploaded document(s) in this conversation: {', '.join(uploaded)}")
        knowledge = GuardedKnowledgeService(self._knowledge_for(session_key),
                                            readable, enabled=self.cfg.security.enabled,
                                            granted_record_ids=access.granted_records,
                                            granted_document_ids=access.granted_documents)
        context_builder = ContextBuilder(knowledge, self.cfg) if self.cfg.security.enabled else self.context_builder
        services = AgentServices(knowledge=knowledge, llm=self.llm, cfg=self.cfg, events=events, resources=self.resources,
                                 session=session, principal=principal)

        def phase(name: str):
            events.emit("phase_started", phase=name, message=name)
            return time.time()

        try:
            # ---------------- Phase 0/1 -------------------------------------------------------
            t0 = phase("0/1 Understanding")
            # A follow-up is rewritten before anything downstream sees it: the classifier and the
            # resolver read one sentence and have no memory of the conversation it belongs to.
            fu_res = AgentResult(agent="context_resolver", step_id="followup")
            followup = resolve_followup(request.text, session, llm=self.llm,
                                        use_llm=self.cfg.llm.use_llm_for_followup and self.cfg.effort.llm_followup,
                                        result=fu_res)
            if followup.is_followup and followup.rewritten:
                request = request.model_copy(update={"text": followup.rewritten, "asked_text": request.text})
                events.emit("agent_finished", phase="0/1 Understanding", agent="context_resolver", step_id="followup",
                            message=f"follow-up ({followup.kind}) read in context",
                            thinking=f"'{request.asked_text}' cannot be answered on its own: {followup.note}.\n"
                                     f"Read as: {followup.rewritten}",
                            decision=f"{followup.kind}; carried forward: {', '.join(followup.baseline_labels) or 'nothing'}",
                            model=(getattr(self.llm, 'model', None) if fu_res.llm_calls else None), data=_counts(fu_res))
                rs.followup = followup.kind
            services.prior_results["followup"] = fu_res
            clf_res = AgentResult(agent="task_classifier", step_id="classify")
            events.emit("agent_started", phase="0/1 Understanding", agent="task_classifier", step_id="classify",
                        message="Scoring the wording against the rule patterns of every task type")
            classification = TaskClassifierAgent(services).classify(request, clf_res, session.last_task_type())
            events.emit("agent_finished", phase="0/1 Understanding", agent="task_classifier", step_id="classify", message=clf_res.summary,
                        thinking=narration.classification(classification),
                        decision=f"task type = {classification.task_type.value} (confidence {classification.confidence:.2f}, {classification.method})",
                        model=_model_used(self.llm, clf_res), data=_counts(clf_res))

            res_res = AgentResult(agent="context_resolver", step_id="resolve")
            events.emit("agent_started", phase="0/1 Understanding", agent="context_resolver", step_id="resolve",
                        message="Resolving equipment tags, parameters, values, scenario and the safety gate")
            req = ContextResolverAgent(services).resolve(request, classification, session, res_res, followup=followup)
            req = self._survey_instead_of_clarifying(req, uploaded, events)
            rs.task_type = req.task_type.value
            rs.safety_status = req.safety_status.value
            rs.entities = [e.name or e.mention for e in req.entities]
            events.emit("agent_finished", phase="0/1 Understanding", agent="context_resolver", step_id="resolve", message=res_res.summary,
                        thinking=narration.resolution(req),
                        decision=f"{len(req.entities)} entity(ies) resolved, safety {req.safety_status.value}"
                                 + (f", ambiguous ({', '.join(req.ambiguities)})" if req.ambiguities else ""),
                        model=_model_used(self.llm, res_res), data=_counts(res_res))

            services.prior_results["classify"] = clf_res
            services.prior_results["resolve"] = res_res
            phases.append(AuditPhase(name="understanding", agent="task_classifier+context_resolver", status="done", duration_ms=int((time.time() - t0) * 1000), note=f"{classification.task_type.value} ({classification.method}); {res_res.summary}"))
            events.emit("phase_finished", phase="0/1 Understanding", message=f"{req.task_type.value}: {res_res.summary}", data={"task_type": req.task_type.value, "entities": rs.entities, "safety": req.safety_status.value})
            self.audit.write(request.session_id, audit_id, "structured_request", {"request": req.model_dump(mode="json", exclude={"original"})})

            # ---------------- Phase 2 ---------------------------------------------------------
            t0 = phase("2 Specialist retrieval")
            events.emit("agent_started", phase="2 Specialist retrieval", agent="context_builder", step_id="retrieval",
                        message=f"Retrieving the Engineering Context Package along the '{narration.route_of(req.task_type)}' route")
            context = context_builder.build(req) if req.task_type != TaskType.AMBIGUOUS else ContextPackage(route="none")
            ctx_res = AgentResult(agent="context_builder", step_id="retrieval", duration_ms=int((time.time() - t0) * 1000),
                                  summary=f"route={context.route}; claims={len(context.claims)} relations={len(context.relations)} procedures={len(context.procedures)} chunks={len(context.chunks)}")
            phases.append(AuditPhase(name="retrieval", agent="context_builder", status="done", duration_ms=ctx_res.duration_ms, note=ctx_res.summary))
            events.emit("agent_finished", phase="2 Specialist retrieval", agent="context_builder", step_id="retrieval", message=ctx_res.summary,
                        thinking=narration.retrieval(req, context),
                        decision=f"route '{context.route}' returned {len(context.claims) + len(context.relations) + len(context.procedures) + len(context.chunks) + len(context.entities) + len(context.sections)} item(s)",
                        data=_counts(ctx_res))
            events.emit("phase_finished", phase="2 Specialist retrieval", message=ctx_res.summary, data={"route": context.route, "gaps": context.gaps})
            for gap in context.gaps:
                events.emit("warning", phase="2 Specialist retrieval", message=gap)

            # ---------------- Phase 3 ---------------------------------------------------------
            t0 = phase("3 Planning")
            planner = PlannerAgent(services)
            plan_res = AgentResult(agent="planner", step_id="plan")
            events.emit("agent_started", phase="3 Planning", agent="planner", step_id="plan",
                        message="Expanding the routing matrix into an executable step DAG")
            plan = planner.make_plan(req, plan_res)
            services.prior_results["plan"] = plan_res
            rs.goal = plan.goal
            rs.step_status = [{"step_id": s.step_id, "agent": s.agent, "goal": s.goal, "depends_on": s.depends_on, "status": s.status.value, "summary": None} for s in plan.steps]
            plan_res.duration_ms = plan_res.duration_ms or int((time.time() - t0) * 1000)
            phases.append(AuditPhase(name="planning", agent="planner", status="done", duration_ms=plan_res.duration_ms, note=plan.rationale))
            events.emit("agent_finished", phase="3 Planning", agent="planner", step_id="plan", message=plan.rationale,
                        thinking=narration.planning(req, plan),
                        decision=narration.plan_shape(plan),
                        model=_model_used(self.llm, plan_res), data=_counts(plan_res))
            events.emit("plan_created", phase="3 Planning", message=plan.rationale,
                        data={"steps": [{"id": s.step_id, "agent": s.agent, "goal": s.goal, "depends_on": s.depends_on,
                                         "mode": s.inputs.get("mode"), "optional": s.optional, "safety_sensitive": s.safety_sensitive} for s in plan.steps]})
            events.emit("phase_finished", phase="3 Planning", message=plan.rationale, data={"template": plan.template, "steps": len(plan.steps)})
            self.audit.write(request.session_id, audit_id, "plan", {"plan": plan.model_dump(mode="json")})

            # ---------------- Phase 4 ---------------------------------------------------------
            t0 = phase("4 Execution")
            executor = Executor(services)
            iteration = 0
            while True:
                try:
                    executor.run(plan, req, context)
                    break
                except ReplanRequested as rr:
                    iteration += 1
                    events.emit("replan", phase="4 Execution", step_id=rr.step_id, message=f"replan {iteration}: {rr.reason}")
                    self.audit.write(request.session_id, audit_id, "replan", {"iteration": iteration, "step": rr.step_id, "reason": rr.reason})
                    if iteration > self.cfg.governance.max_replan_iterations:
                        for s in plan.steps:
                            if s.status == StepStatus.PENDING:
                                s.mark(StepStatus.SKIPPED, "replan budget exhausted")
                        break
                    self._replan(plan, rr, req, iteration)
                    rs.step_status = [{"step_id": s.step_id, "agent": s.agent, "goal": s.goal, "depends_on": s.depends_on, "status": s.status.value,
                                       "summary": (services.prior_results[s.step_id].summary if s.step_id in services.prior_results else None)} for s in plan.steps]
            exec_results = {k: v for k, v in services.prior_results.items() if k not in ("classify", "resolve", "plan", "followup")}
            exec_note = f"{sum(1 for s in plan.steps if s.status == StepStatus.DONE)}/{len(plan.steps)} steps done" + (f", {iteration} replan(s)" if iteration else "")
            phases.append(AuditPhase(name="execution", agent="executor", status="done", duration_ms=int((time.time() - t0) * 1000), note=exec_note))
            events.emit("phase_finished", phase="4 Execution", message=exec_note,
                        data={"steps": len(plan.steps), "replans": iteration, "llm_calls": sum(r.llm_calls for r in exec_results.values())})
            for sid, r in exec_results.items():
                self.audit.write(request.session_id, audit_id, "step_result", {"step": sid, "agent": r.agent, "ok": r.ok, "summary": r.summary, "evidence": len(r.evidence), "llm_calls": r.llm_calls, "duration_ms": r.duration_ms, "missing": r.missing, "trace": r.trace[-3:]})

            # ---------------- Phase 5 ---------------------------------------------------------
            t0 = phase("5 Governance")
            events.emit("agent_started", phase="5 Governance", agent="verification", step_id="verify",
                        message="Checking every statement's numbers and wording against the evidence it cites")
            ver = VerificationAgent(services).run(req, context, PlanStep(step_id="verify", agent="verification", goal="Verify statements against evidence"))
            services.prior_results["verify"] = ver
            phases.append(AuditPhase(name="verification", agent="verification", status="done", duration_ms=ver.duration_ms, note=ver.summary))
            events.emit("agent_finished", phase="5 Governance", agent="verification", step_id="verify", message=ver.summary,
                        thinking=narration.verification(ver),
                        decision=f"grounding score {ver.content.get('overall_score', 'n/a')}",
                        model=_model_used(self.llm, ver), data=_counts(ver))
            all_results = {**exec_results, "verify": ver}

            # ---- answer composition ------------------------------------------------------
            # Everything above produced material. This turns it into the answer the engineer
            # reads. Skipped when the run is going to return a clarification or a refusal:
            # those are already written, and composing over them would blur them.
            compose_skipped = self._skip_composition(req, exec_results)
            if compose_skipped is None:
                t_comp = time.time()
                comp = AgentResult(agent="answer_composer", step_id="compose")
                events.emit("agent_started", phase="5 Governance", agent="answer_composer", step_id="compose",
                            message="Writing the answer from the retrieved material")
                composer = AnswerComposerAgent(services)
                try:
                    composer.compose(req, context, all_results, comp)
                except Exception as exc:                      # composition must never lose the answer
                    logger.exception("answer composition failed")
                    comp.ok = False
                    comp.trace.append(f"composition failed: {exc}")
                comp.duration_ms = int((time.time() - t_comp) * 1000)
                if comp.ok and comp.content.get("composed"):
                    all_results["compose"] = comp
                    services.prior_results["compose"] = comp
                phases.append(AuditPhase(name="composition", agent="answer_composer", status="done" if comp.ok else "failed",
                                         duration_ms=comp.duration_ms, note=comp.summary))
                events.emit("agent_finished", phase="5 Governance", agent="answer_composer", step_id="compose", message=comp.summary,
                            thinking=narration.composition(comp),
                            decision=f"{comp.content.get('source', 'none')}-written answer of {len(comp.content.get('answer', '').split())} words",
                            model=_model_used(self.llm, comp), data=_counts(comp))
            else:
                events.emit("warning", phase="5 Governance", message=f"answer composition skipped: {compose_skipped}")

            t_gov = time.time()
            events.emit("agent_started", phase="5 Governance", agent="governance", step_id="governance",
                        message="Applying policy, aggregating confidence and composing the released answer")
            gov = GovernanceAgent(services)
            resp = gov.compose(req, plan, all_results, audit_id, phases, self.backend_name, started, warnings=context.gaps, access=access)
            gov_ms = int((time.time() - t_gov) * 1000)
            events.emit("agent_finished", phase="5 Governance", agent="governance", step_id="governance",
                        message=f"status={resp.status}; confidence={resp.confidence.score}",
                        thinking=narration.governance(resp),
                        decision=f"{resp.status} at confidence {resp.confidence.score:.2f} ({resp.confidence.level})"
                                 + (", human review required" if resp.requires_human_review else ""),
                        model=(getattr(self.llm, "model", None) if resp.llm_calls else None),
                        data={"ok": True, "duration_ms": gov_ms, "llm_calls": resp.llm_calls, "blocks": len(resp.blocks), "evidence": len(resp.evidence), "missing": []})
            phases.append(AuditPhase(name="governance", agent="governance", status="done", duration_ms=gov_ms, note=f"status={resp.status}; confidence={resp.confidence.score}; review={resp.requires_human_review}"))
            resp.blocks[-1].phases = phases   # the audit block is last
            # ---- security: record the access decision, offer the escalation route, stamp the
            #      classification, then the release gate
            self._fill_security_envelope(resp, principal, access, key_error, uploaded)
            self._escalation_offer(resp, principal, access, request, events)
            self._stamp_classification(resp, principal, access, knowledge)
            resp = self._release_gate(resp, principal, access, knowledge, request, grant, events, uploaded)
            resp.timing_ms = int((time.time() - started) * 1000)
            self.hitl.record(resp, request.text)
            # session memory
            composed_answer = all_results.get("compose")
            session.turns.append(Turn(request=request.spoken_text,
                                      rewritten_request=request.text if request.asked_text else "",
                                      task_type=req.task_type.value if req.task_type != TaskType.AMBIGUOUS else (req.secondary_task_types[0].value if req.secondary_task_types else "ambiguous"),
                                      entities=[e.model_dump(mode="json") for e in req.entities], parameter=req.parameter, scenario=req.scenario, response_id=resp.response_id,
                                      status=resp.status, followup_kind=req.followup_kind,
                                      corrections=[c.model_dump(mode="json") for c in req.corrections],
                                      answer_preview=(composed_answer.content.get("answer") if composed_answer else resp.answer_markdown)[:600]))
            if resp.status == "clarification":
                session.pending_clarification = {"request": request.text, "missing": req.ambiguities, "intended": req.secondary_task_types[0].value if req.secondary_task_types else None}
            else:
                session.pending_clarification = None
            self.sessions.save(session)
            self.audit.write(request.session_id, audit_id, "final", {"response_id": resp.response_id, "status": resp.status, "confidence": resp.confidence.score, "review": resp.requires_human_review,
                                                                      "llm_calls": resp.llm_calls, "timing_ms": resp.timing_ms, "evidence": len(resp.evidence), "resources": self.resources.status()})
            rs.final = resp
            rs.final_status = resp.status
            rs.safety_flags = len(resp.safety_flags)
            events.emit("phase_finished", phase="5 Governance", message=f"{resp.status} (confidence {resp.confidence.score})")
            events.emit("final", phase="5 Governance", message=f"{resp.status} (confidence {resp.confidence.score})", data={"response_id": resp.response_id, "status": resp.status})
            return resp
        except Exception as exc:
            logger.exception("run failed")
            rs.error = f"{type(exc).__name__}: {exc}"
            self.audit.write(request.session_id, audit_id, "error", {"error": rs.error, "trace": traceback.format_exc()[-2000:]})
            events.emit("error", message=rs.error)
            resp = FinalResponse(response_id=f"resp-error-{audit_id}", session_id=request.session_id, task_type=TaskType.AMBIGUOUS, status="failed",
                                 answer_markdown=f"The request could not be processed: {rs.error}", confidence=Confidence(score=0.0, basis="run failed"), audit_trail_id=audit_id,
                                 timing_ms=int((time.time() - started) * 1000), backend=self.backend_name, warnings=[rs.error])
            rs.final = resp
            rs.final_status = "failed"
            return resp
        finally:
            rs.finished = time.time()
            rs.resources = self.resources.status()
            self.resources.end_request()

    # ------------------------------------------------------------------ access keys
    def _redeem_key(self, request: UserRequest, principal: Principal, events) -> tuple[object | None, str | None]:
        """Verify an access key against this caller and this question, or return None.

        A bad key is never fatal: the run continues at the caller's own role, and the reason the
        key was refused is put in front of them rather than swallowed. Every rejection is logged
        with its reason, because a pattern of rejections is what an attack looks like.
        """
        if not request.access_key or not self.cfg.security.enabled:
            return None, None
        try:
            grant = self.escalations.redeem(request.access_key, requester=principal.username, question=request.text)
        except EscalationError as exc:
            self.security_audit.write("key_rejected", principal=principal.username, role=principal.role.value,
                                      outcome=str(exc), question=request.text[:200])
            events.emit("warning", phase="0 Access", message=f"Access key refused: {exc}")
            return None, str(exc)
        self.security_audit.write("key_redeemed", principal=principal.username, role=principal.role.value,
                                  outcome=f"{len(grant.record_ids)} record(s) opened", grant_id=grant.grant_id,
                                  request_id=grant.request_id, documents=grant.document_ids)
        events.emit("phase_started", phase="0 Access", message="Approved access key verified")
        events.emit("agent_finished", phase="0 Access", agent="access_grant", step_id="grant",
                    message=f"grant {grant.grant_id} opens {len(grant.record_ids)} record(s)",
                    thinking=(f"Key signature verified against the server secret.\n"
                              f"Issued by {grant.issued_by} for request {grant.request_id}.\n"
                              f"Bound to {grant.requester}, to this question, and to "
                              f"{len(grant.record_ids)} record(s) in {', '.join(grant.document_ids)}.\n"
                              f"Expires in {grant.minutes_left()} minute(s); use {grant.uses + 1} of {grant.max_uses}."),
                    decision=f"opening {len(grant.record_ids)} named record(s) — nothing else in those documents",
                    data={"ok": True, "duration_ms": 0, "llm_calls": 0, "blocks": 0, "evidence": 0, "missing": []})
        return grant, None

    def _release_gate(self, resp, principal: Principal, access, knowledge, request: UserRequest, grant, events,
                      uploaded: list[str] | None = None):
        """The last check before an answer leaves, and the bookkeeping that follows it.

        ``uploaded`` are this conversation's own documents. They have to be named here as well as
        in the guard: the gate re-checks the provenance of the finished answer against what the
        caller may read, and a document the caller uploaded themselves is something they may read.
        Leaving it out withheld every answer drawn from an attachment.
        """
        if not self.cfg.security.enabled or not self.cfg.security.leak_check:
            return resp
        report = leakcheck.check_response(
            resp, allowed_documents=list(access.allowed) + list(uploaded or []),
            granted_records=access.granted_records,
            granted_documents=access.granted_documents, knowledge=knowledge)
        resp.warnings.extend(f.detail for f in report.findings if f.severity == "note")
        if not report.ok:
            self.security_audit.write("release_blocked", principal=principal.username, role=principal.role.value,
                                      outcome=report.summary(), question=request.text[:200],
                                      findings=[f.model_dump() for f in report.blocking])
            events.emit("error", phase="5 Governance", message="Release check failed; the answer was withheld.")
            return leakcheck.refusal_response(resp, report)
        if grant is not None and resp.status in ("answered", "needs_review"):
            self.escalations.consume(grant)
            self.security_audit.write("grant_consumed", principal=principal.username, role=principal.role.value,
                                      outcome=f"answer released under {grant.grant_id}", grant_id=grant.grant_id,
                                      records_used=len(knowledge.released_by_grant))
        return resp

    def _fill_security_envelope(self, resp, principal: Principal, access, key_error: str | None = None,
                                uploaded: list[str] | None = None) -> None:
        """Put the access decision on the response as fields, not only as prose.

        The classification footer and the escalation note say all of this in words, which is what
        a terminal needs. A web client needs the same facts as data so it can colour a banner and
        offer the "track this request" button, rather than parsing English out of the answer.
        Filled whether or not the prose banner is switched on, and before the escalation offer,
        which adds the request id it raises.
        """
        used = sorted({ev.document_id for ev in resp.evidence})
        # A document uploaded into this conversation has no classification, because nobody has
        # classified it — `tag_of` would fail it closed to SECRET and stamp the answer with a
        # tier it does not belong to. It is the caller's own file, so it contributes no tag.
        mine = set(uploaded or [])
        tags = sorted({self.classifications.tag_of(d).value for d in used if d not in mine})
        env = resp.security
        env.access_control = bool(self.cfg.security.enabled)
        env.principal = principal.username
        env.role = principal.role.value
        env.authenticated = principal.authenticated
        env.classification = max(tags) if tags else None
        env.source_documents = used
        env.readable_documents = list(access.allowed) + sorted(mine & set(used))
        env.attached_documents = sorted(mine)
        env.withheld_documents = list(access.denied)
        env.escalation_target = access.escalation_target()
        env.grant_id = access.grant_id
        env.released_records = len(access.granted_records)
        env.access_key_error = key_error

    def _stamp_classification(self, resp, principal: Principal, access, knowledge) -> None:
        """Say what this answer was built from and who it was released to.

        An engineer reading an answer should be able to see its classification without asking,
        the way a printed document carries it in the footer. It is also the fastest way for a
        reviewer to spot an answer that drew on more than it should have.
        """
        if not (self.cfg.security.enabled and self.cfg.security.show_classification_banner):
            return
        env = resp.security
        line = (f"*Classification: **{env.classification or '—'}** · built from {', '.join(env.source_documents) or 'no document'} · "
                f"released to {principal.username} ({principal.role.value})"
                + (f" under grant {access.grant_id}" if access.grant_id else "")
                + (f" · {len(access.denied)} document(s) withheld by classification" if access.denied else "") + ".*")
        resp.answer_markdown = (resp.answer_markdown or "").rstrip() + "\n\n" + line
        resp.warnings = [w for w in resp.warnings if w]

    def _escalation_offer(self, resp, principal: Principal, access, request: UserRequest, events) -> None:
        """When material above the caller's role bears on the question, say so and open the door.

        This runs *after* the answer, so what the caller could be told, they already were. The
        offer adds one paragraph: how much restricted material exists, who can release it, and
        the request id to quote. It never adds content.
        """
        if not (self.cfg.security.enabled and self.cfg.security.escalation_enabled and access.denied):
            return
        if not principal.authenticated or access.grant_id:
            return
        try:
            scope = self.scope_for_question(principal, request.text, access.denied)
        except Exception as exc:
            logger.warning("escalation scoping failed: %s", exc)
            return
        if scope.is_empty:
            return
        # Who to name is decided by the *scope*, not by everything being withheld. A question that
        # only the desalter answers goes to a manager even when the CDU manual is also shut to this
        # caller, because routing every request to the highest authority is how approval becomes a
        # rubber stamp — and because naming a role that is not the one the request was actually
        # raised with leaves the requester chasing the wrong person.
        deciders = scope.deciders()
        target = (deciders[0].value if deciders else None) or access.escalation_target() or Role.ADMIN.value
        request_id = None
        if self.cfg.security.auto_raise_requests:
            try:
                raised = self.escalations.raise_request(requester=principal.username, requester_role=principal.role,
                                                        question=request.text, scope=scope,
                                                        session_id=request.session_id)
                request_id = raised.request_id
                target = raised.approver_role.value
                self.security_audit.write("request_raised", principal=principal.username, role=principal.role.value,
                                          outcome=f"raised to {raised.approver_role.value}", request_id=request_id,
                                          documents=scope.document_ids, records=len(scope.record_ids),
                                          question=request.text[:200])
            except EscalationError as exc:
                logger.info("auto-raise skipped: %s", exc)
        from workbench.core.blocks import CalloutBlock

        note = (f"**Material you are not cleared for bears on this question.** "
                f"{scope.summary()}. Nothing from it is included above.\n\n"
                + (f"An access request has been raised for you: **{request_id}**, waiting on "
                   f"{_a(role_title(target))}. They see the passages themselves before deciding. "
                   f"If they approve, you get a one-time key; re-ask this exact question with that "
                   f"key and the answer will include those records and nothing else."
                   if request_id else
                   f"Ask {_a(role_title(target))} to release it."))
        resp.blocks.append(CalloutBlock(id="escalation", level="info", title="Restricted material exists", markdown=note))
        resp.answer_markdown = (resp.answer_markdown or "").rstrip() + "\n\n" + note
        resp.warnings.append(f"{len(scope.record_ids)} record(s) in {', '.join(scope.document_ids)} were withheld by classification")
        resp.security.access_request_id = request_id
        resp.security.escalation_target = target
        resp.security.withheld_summary = scope.summary()
        resp.security.withheld_records = len(scope.record_ids)
        resp.security.withheld_documents = list(scope.document_ids)

    # ------------------------------------------------------------------ composition gate
    @staticmethod
    def _skip_composition(req: StructuredRequest, exec_results: dict[str, AgentResult]) -> str | None:
        """Why this run should not be recomposed into prose, or None to compose it.

        A clarification and a refusal are answers in their own right, already worded for the
        situation; running them back through the composer would soften a refusal and bury a
        question the engineer has to answer.
        """
        if req.safety_status == SafetyStatus.RESTRICTED:
            return "the request is restricted; the documented authorization route is returned verbatim"
        if any(b.type == "clarification" for r in exec_results.values() for b in r.blocks):
            return "the run is asking the engineer a question rather than answering one"
        if not any(r.blocks or r.evidence for r in exec_results.values()):
            return "no material was gathered to compose from"
        return None

    # ------------------------------------------------------------------ access refusal
    def _unauthorized(self, request: UserRequest, access, principal: Principal, audit_id: str, started: float,
                      phases: list[AuditPhase]) -> FinalResponse:
        """The answer when the principal is cleared for nothing that is loaded.

        It names the documents, their tags and the role that opens each, and it says what to do
        next — a bare "access denied" leaves an engineer with nowhere to go. The question itself
        is never evaluated: no retrieval runs, so there is nothing to leak even by accident.
        """
        from workbench.core.blocks import CalloutBlock, TextBlock

        signed_in = principal.authenticated
        listing = "\n".join(f"- **{d['title']}** — `{d['tag']}`, readable by **{role_title(d['min_role'])}** and above"
                            for d in self.document_catalogue())
        if signed_in:
            target = access.escalation_target()
            head = (f"Your role ({role_title(principal.role)}) is not cleared for any document loaded here, "
                    f"so this question was not evaluated at all — no retrieval ran and nothing was read.")
            how = (f"- Ask {_a(role_title(target))} to release the material this question needs.\n"
                   f"- They review exactly what would be released and, if they agree, you get a one-time key.\n"
                   f"- Re-asking this same question with that key answers from that material and nothing else."
                   if target else "- Contact the documentation owner; there is no higher role to escalate to.")
            title_text = "Not cleared for this material"
        else:
            head = ("You are not signed in, so no document is readable and the question was not evaluated. "
                    "The workbench asks who you are before it answers anything.")
            how = ("- Sign in, and the workstation searches whatever your role may read.\n"
                   "- Accounts are created per role; ask your administrator which one you hold.")
            title_text = "Sign-in required"
        blocks = [
            CalloutBlock(id="lead", level="danger", title=title_text, markdown=head),
            TextBlock(id="documents", title="Documents loaded here, and who may read them",
                      markdown=listing or "_No document is loaded._"),
            TextBlock(id="how", title="What to do next", markdown=how),
        ]
        md = "\n\n".join(b.markdown for b in blocks)
        phases.append(AuditPhase(name="access", agent="policy", status="denied", duration_ms=0, note=access.message()))
        resp = FinalResponse(
            response_id=f"resp-denied-{audit_id}", session_id=request.session_id, task_type=TaskType.AMBIGUOUS,
            status="unauthorized", answer_markdown=md, blocks=blocks,
            confidence=Confidence(score=0.0, basis="the request was not evaluated: the caller is not cleared for any loaded document"),
            audit_trail_id=audit_id, timing_ms=int((time.time() - started) * 1000), backend=self.backend_name,
            warnings=[access.message()],
        )
        self._fill_security_envelope(resp, principal, access)
        return resp

    # ------------------------------------------------------------------ replanning
    @staticmethod
    def _replan(plan: Plan, rr: ReplanRequested, req: StructuredRequest, iteration: int) -> None:
        """Replace the failed required step by the best fallback route and let dependants continue."""
        failed = plan.step(rr.step_id)
        failed.mark(StepStatus.FAILED, rr.reason)
        plan.iteration = iteration
        fallback_id = f"fb{iteration}"
        if failed.agent == "procedure":
            fb = PlanStep(step_id=fallback_id, agent="explanation", goal="No procedure block matched — retrieve the prose that describes the operation", inputs={"mode": "support"}, optional=True)
        elif failed.agent == "diagnostic":
            fb = PlanStep(step_id=fallback_id, agent="explanation", goal="No upset section matched — retrieve descriptive text about the equipment and variable", inputs={"mode": "support"}, optional=True)
        elif failed.agent == "calculation":
            fb = PlanStep(step_id=fallback_id, agent="explanation", goal="No documented limits — retrieve consequences / operating text", inputs={"mode": "consequences"}, optional=True)
        elif failed.agent in ("lookup", "graph", "comparison", "revision_conflict"):
            fb = PlanStep(step_id=fallback_id, agent="explanation", goal="Structured data missing — retrieve grounded passages instead", inputs={"mode": "support"}, optional=True)
        else:
            fb = PlanStep(step_id=fallback_id, agent="cross_document", goal="Locate where the topic is covered", inputs={"mode": "find"}, optional=True)
        plan.steps.append(fb)
        # dependants of the failed step now depend on the fallback and become optional
        for s in plan.steps:
            if rr.step_id in s.depends_on and s.step_id != fallback_id:
                s.depends_on = [fallback_id if d == rr.step_id else d for d in s.depends_on]
                s.optional = True
        plan.rationale += f"; replan {iteration}: {failed.agent} -> {fb.agent}"
