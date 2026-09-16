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
import threading
import time
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
from workbench.security.auth import AuthService, Principal
from workbench.security.classification import ClassificationRegistry
from workbench.security.guard import GuardedKnowledgeService
from workbench.security.policy import AccessPolicy
from workbench.security.roles import Role, title as role_title

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
    def login(self, username: str, password: str, *, label: str = "") -> Principal:
        """Verify a password and mint a session token. Raises AuthError on refusal."""
        principal = self.auth.authenticate(username, password, label=label)
        self.audit.write("auth", self.audit.new_id(), "login", {"principal": principal.username, "role": principal.role.value, "label": label})
        return principal

    def logout(self, token: str | None) -> bool:
        return self.auth.revoke(token)

    def principal(self, token: str | None) -> Principal:
        return self.auth.principal_for(token) if self.cfg.security.enabled else Principal(
            username="unrestricted", role=Role.ADMIN, display_name="Access control disabled", authenticated=True)

    def access_for(self, token: str | None):
        """(principal, decision) for a token, against the documents currently loaded."""
        principal = self.principal(token)
        return principal, self.policy.decide(principal, self.knowledge.documents())

    def restricted_documents(self) -> list[dict]:
        """What is loaded and what each document needs, for a login prompt that names it."""
        out = []
        for d in self.knowledge.documents():
            dc = self.classifications.classify(d)
            out.append({"document_id": d.document_id, "title": d.title or d.document_id,
                        "clearance": dc.clearance.value, "reason": dc.reason})
        return out

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
        session = self.sessions.load(request.session_id)
        self.audit.write(request.session_id, audit_id, "request", {"text": request.text, "run_id": rs.run_id, "attachments": [a.model_dump() for a in request.attachments]})

        # ---------------- access control ------------------------------------------------------
        principal, access = self.access_for(request.auth_token)
        rs.principal = principal.describe()
        self.audit.write(request.session_id, audit_id, "access", access.audit_payload())
        if access.nothing_allowed and self.cfg.security.enabled:
            events.emit("access_denied", phase="0 Access", message=access.message(),
                        data={"role": principal.role.value, "required_roles": access.required_roles()})
            resp = self._unauthorized(request, access, audit_id, started, phases)
            rs.final, rs.final_status, rs.finished = resp, resp.status, time.time()
            self.resources.end_request()
            return resp
        if access.denied:
            events.emit("warning", phase="0 Access", message=access.message())
        knowledge = GuardedKnowledgeService(self.knowledge, access.allowed, enabled=self.cfg.security.enabled)
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
    def _unauthorized(self, request: UserRequest, access, audit_id: str, started: float,
                      phases: list[AuditPhase]) -> FinalResponse:
        """The answer when the principal is cleared for nothing that is loaded.

        It names the document, the classification, and the role that would open it, and it says
        how to sign in — a bare "access denied" leaves an engineer with nowhere to go.
        """
        from workbench.core.blocks import CalloutBlock, TextBlock

        roles = ", ".join(role_title(r) for r in access.required_roles()) or "a cleared role"
        docs = self.restricted_documents()
        listing = "\n".join(f"- **{d['title']}** — classified {d['clearance']} ({d['reason']})" for d in docs)
        blocks = [
            CalloutBlock(id="lead", level="danger", title="Sign-in required",
                         markdown=f"{access.message()} Nothing from these documents is shown until a cleared user signs in."),
            TextBlock(id="documents", title="Loaded documents", markdown=listing or "_No document is loaded._"),
            TextBlock(id="how", title="How to sign in", markdown=(
                "- In the terminal: `python -m workbench login` (the default account is `lead`).\n"
                f"- Over the API: `POST /auth/login` with a username and password, then send the token back as `auth_token`.\n"
                f"- The role needed for this material: **{roles}**.")),
        ]
        md = "\n\n".join(b.markdown if b.type == "text" else b.markdown for b in blocks)
        phases.append(AuditPhase(name="access", agent="policy", status="denied", duration_ms=0, note=access.message()))
        return FinalResponse(
            response_id=f"resp-denied-{audit_id}", session_id=request.session_id, task_type=TaskType.AMBIGUOUS,
            status="unauthorized", answer_markdown=md, blocks=blocks,
            confidence=Confidence(score=0.0, basis="the request was not evaluated: the caller is not cleared for the loaded documents"),
            audit_trail_id=audit_id, timing_ms=int((time.time() - started) * 1000), backend=self.backend_name,
            warnings=[access.message()],
        )

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
