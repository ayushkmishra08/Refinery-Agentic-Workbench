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
from workbench.core.request import StructuredRequest, TaskType, UserRequest
from workbench.core.result import AgentResult, FinalResponse
from workbench.llm.client import build_llm
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
        services = AgentServices(knowledge=self.knowledge, llm=self.llm, cfg=self.cfg, events=events, resources=self.resources)
        session = self.sessions.load(request.session_id)
        self.audit.write(request.session_id, audit_id, "request", {"text": request.text, "run_id": rs.run_id, "attachments": [a.model_dump() for a in request.attachments]})

        def phase(name: str):
            events.emit("phase_started", phase=name, message=name)
            return time.time()

        try:
            # ---------------- Phase 0/1 -------------------------------------------------------
            t0 = phase("0/1 Understanding")
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
            req = ContextResolverAgent(services).resolve(request, classification, session, res_res)
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
            context = self.context_builder.build(req) if req.task_type != TaskType.AMBIGUOUS else ContextPackage(route="none")
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
            exec_results = {k: v for k, v in services.prior_results.items() if k not in ("classify", "resolve", "plan")}
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
            t_gov = time.time()
            events.emit("agent_started", phase="5 Governance", agent="governance", step_id="governance",
                        message="Applying policy, aggregating confidence and composing the released answer")
            gov = GovernanceAgent(services)
            resp = gov.compose(req, plan, all_results, audit_id, phases, self.backend_name, started, warnings=context.gaps)
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
            session.turns.append(Turn(request=request.text, task_type=req.task_type.value if req.task_type != TaskType.AMBIGUOUS else (req.secondary_task_types[0].value if req.secondary_task_types else "ambiguous"),
                                      entities=[e.model_dump(mode="json") for e in req.entities], parameter=req.parameter, scenario=req.scenario, response_id=resp.response_id,
                                      status=resp.status, answer_preview=resp.answer_markdown[:200]))
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
