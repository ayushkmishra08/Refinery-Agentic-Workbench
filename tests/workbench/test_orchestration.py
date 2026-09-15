"""Planning, executor/orchestrator end-to-end on the mock backend, runs/status, session, audit, HITL."""
from __future__ import annotations

import time

from workbench.agents.base import AgentResult
from workbench.agents.planner import PlannerAgent, extend_for_secondary, prune
from workbench.core.events import ProgressEvent
from workbench.core.plan import StepStatus
from workbench.core.request import TaskType, UserRequest
from workbench.memory.session import SessionState, SessionStore, Turn
from workbench.orchestration.hitl import HITLRegistry
from workbench.orchestration.router import template_plan
from workbench.orchestration.runs import RunRegistry
from workbench.orchestration.status_agent import status_reply
from workbench.services.audit_store import AuditStore


# ----------------------------------------------------------------------------- plans
def test_template_plan_sizes_follow_the_task(make_request):
    req, _ = make_request("What is the normal flow rate of the crude charge pump?")
    assert len(template_plan(req, "p").steps) <= 2
    req, _ = make_request("The crude charge pump discharge pressure is dropping. What should I check?")
    ts = template_plan(req, "p")
    assert len(ts.steps) >= 8 and ts.validate_dag() == []
    assert any(s.agent == "safety" and s.safety_sensitive for s in ts.steps)
    req, _ = make_request("The crude charge pump is operating at 520 m3/h. Is this acceptable?")
    assert any(s.agent == "calculation" and s.inputs.get("mode") == "check_value" for s in template_plan(req, "p").steps)


def test_extend_for_secondary_adds_procedure_steps(make_request):
    req, _ = make_request("The crude charge pump discharge pressure is dropping. What should I check?")
    req.task_type = TaskType.LIMITS
    req.secondary_task_types = [TaskType.PROCEDURE, TaskType.CROSS_DOCUMENT]
    plan = template_plan(req, "p")
    before = {s.agent for s in plan.steps}
    assert "procedure" not in before
    extend_for_secondary(plan, req)
    assert {"procedure", "cross_document"} <= {s.agent for s in plan.steps}
    assert plan.validate_dag() == []


def test_prune_marks_entity_steps_optional_without_entity(make_request):
    req, _ = make_request("Trace the flow.")
    req.task_type = TaskType.MULTI_HOP
    req.entities = []
    req.unresolved_mentions = []
    plan = template_plan(req, "p")
    prune(plan, req)
    assert all(s.optional for s in plan.steps if s.agent == "graph")


def test_planner_make_plan_skips_extension_for_ambiguous(services, make_request):
    req, _ = make_request("Can I run this at 500?")
    plan = PlannerAgent(services).make_plan(req, AgentResult(agent="planner"))
    assert [s.agent for s in plan.steps] == ["context_resolver"] and "template 'ambiguous'" in plan.rationale


# ----------------------------------------------------------------------------- orchestrator end-to-end
def test_e2e_lookup_answers_with_kpi_conflict_and_evidence(orch):
    resp = orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="e2e-lookup"))
    types = [b.type for b in resp.blocks]
    assert resp.status == "answered" and resp.task_type == TaskType.LOOKUP
    assert "kpi" in types and "conflict" in types and types[-2:] == ["evidence", "audit"]
    assert "482" in resp.answer_markdown and resp.evidence and resp.llm_calls == 0
    assert resp.plan is not None and all(s.status == StepStatus.DONE for s in resp.plan.steps)
    assert not resp.requires_human_review


def test_e2e_procedure_returns_steps_safety_and_needs_review(orch):
    resp = orch.ask(UserRequest(text="How do I change over from the running crude charge pump to the standby pump?", session_id="e2e-proc"))
    types = [b.type for b in resp.blocks]
    assert resp.status == "answered" and resp.task_type == TaskType.PROCEDURE
    assert "steps" in types and "safety" in types and "plan" in types
    assert resp.requires_human_review and "changeover" in (resp.review_reason or "")
    steps = next(b for b in resp.blocks if b.type == "steps" and b.procedure_id == "proc-changeover-01" and not b.steps[0].is_prerequisite)
    assert len(steps.steps) == 4 and steps.steps[0].citation.startswith("[")
    assert any(b.type == "steps" and b.steps and b.steps[0].is_prerequisite for b in resp.blocks)


def test_e2e_ambiguous_request_asks_for_clarification(orch):
    resp = orch.ask(UserRequest(text="Can I run this at 500?", session_id="e2e-amb"))
    assert resp.status == "clarification" and resp.blocks[0].type == "clarification"
    assert {"entity", "unit"} <= set(resp.blocks[0].missing)
    st = orch.sessions.load("e2e-amb")
    assert st.pending_clarification and st.pending_clarification["intended"] == "limits"


def test_e2e_restricted_bypass_is_never_instructed(orch):
    resp = orch.ask(UserRequest(text="Can I bypass the crude charge pump low flow trip temporarily?", session_id="e2e-restricted"))
    assert resp.status == "restricted" and resp.requires_human_review
    assert resp.blocks[0].type == "callout" and resp.blocks[0].level == "danger"
    assert not any(b.type == "steps" for b in resp.blocks)
    assert orch.hitl.pending() and any(r["response_id"] == resp.response_id for r in orch.hitl.pending())


def test_e2e_limit_check_leads_with_gauge_verdict(orch):
    resp = orch.ask(UserRequest(text="The crude charge pump is operating at 600 m3/h. Is this acceptable?", session_id="e2e-limits"))
    assert resp.task_type == TaskType.LIMITS and resp.status == "answered"
    assert resp.blocks[0].type == "callout" and "outside design" in resp.blocks[0].markdown and resp.blocks[0].level == "danger"
    assert any(b.type == "limit_gauge" and b.value == 600 and b.verdict == "outside_design" for b in resp.blocks)
    assert resp.requires_human_review and "design" in (resp.review_reason or "")
    ok = orch.ask(UserRequest(text="The crude charge pump is operating at 485 m3/h. Is this acceptable?", session_id="e2e-limits-ok"))
    assert "within normal" in ok.blocks[0].markdown and ok.blocks[0].level == "success"
    # a documented DANGER precaution ("Never bypass the low flow trip ...") is quoted by the safety review, which by design flags human review
    assert ok.requires_human_review == any(f.severity == "danger" for f in ok.safety_flags)


def test_e2e_session_memory_resolves_follow_up_pronoun(orch):
    orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="e2e-follow"))
    resp = orch.ask(UserRequest(text="How do I start it?", session_id="e2e-follow"))
    assert resp.task_type == TaskType.PROCEDURE and resp.entities and "Crude Charge Pump" in resp.entities[0]


def test_e2e_events_and_audit_trail(orch):
    events = []
    resp = orch.ask(UserRequest(text="Which instruments monitor the crude charge pump?", session_id="e2e-events"), on_event=events.append)
    kinds = [e.event for e in events]
    assert kinds[0] == "phase_started" and "plan_created" in kinds and kinds[-1] == "final"
    assert any(e.event == "agent_finished" and e.agent == "graph" for e in events)
    rows = orch.audit.read("e2e-events", resp.audit_trail_id)
    assert [r["kind"] for r in rows][0] == "request" and rows[-1]["kind"] == "final"
    assert any(r["kind"] == "plan" for r in rows) and any(r["kind"] == "step_result" for r in rows)


def test_start_runs_in_background_and_btw_reports_status(orch):
    rs = orch.start(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="e2e-bg"))
    deadline = time.time() + 30
    while not rs.finished and time.time() < deadline:
        time.sleep(0.05)
    assert rs.finished and rs.final is not None and rs.final_status == "answered"
    blocks = orch.btw("what did you find so far?", run_id=rs.run_id)
    assert blocks[0].type == "text" and "finished" in blocks[0].markdown
    assert any(b.type == "plan" for b in blocks) and any(b.type == "table" and b.title == "What has been found so far" for b in blocks)
    assert orch.btw("status", session_id="no-such-session")[0].type == "callout"


# ----------------------------------------------------------------------------- runs / status agent
def test_run_registry_and_status_reply():
    reg = RunRegistry(keep=2)
    a = reg.create("s", "first")
    b = reg.create("s", "second")
    assert reg.get(a.run_id) is a and reg.latest("s") is b and len(reg.active()) == 2
    b.step_status = [{"step_id": "x", "agent": "lookup", "goal": "g", "depends_on": [], "status": "pending", "summary": None}]
    b.note_event(ProgressEvent(event="phase_started", phase="4 Execution"))
    b.note_event(ProgressEvent(event="agent_started", agent="lookup", step_id="x", message="g"))
    assert b.phase == "4 Execution" and b.current_step["agent"] == "lookup" and b.step_status[0]["status"] == "running"
    b.note_event(ProgressEvent(event="agent_finished", agent="lookup", step_id="x", message="done it", data={"ok": True, "llm_calls": 2}))
    assert b.step_status[0]["status"] == "done" and b.llm_calls == 2 and b.current_step is None
    blocks = status_reply(b, "why is it slow?")
    assert blocks[0].type == "text" and "running" in blocks[0].markdown
    assert any(b_.type == "text" and "LLM calls so far: 2" in b_.markdown for b_ in blocks)
    assert any(b_.type == "plan" for b_ in blocks) and any(b_.type == "table" and b_.title == "Recent events" for b_ in blocks)
    b.resources = {"llm": "qwen3:4b", "llm_loaded": False, "embedder_loaded": False, "reranker_loaded": False, "idle_unload_seconds": 45}
    assert any("qwen3:4b" in b_.markdown for b_ in status_reply(b, "which model is loaded?") if b_.type == "text")


# ----------------------------------------------------------------------------- session / audit / hitl
def test_session_store_round_trip_and_ttl(tmp_path):
    store = SessionStore(tmp_path, ttl_turns=3)
    st = store.load("abc")
    assert st.session_id == "abc" and st.turns == []
    for i in range(5):
        st.turns.append(Turn(request=f"q{i}", task_type="lookup", entities=[{"entity_uid": f"e{i}", "mention": "m", "confidence": 0.9}], parameter="flow_rate"))
    store.save(st)
    store._cache.clear()
    again = store.load("abc")
    assert [t.request for t in again.turns] == ["q2", "q3", "q4"]
    assert again.last_entities()[0]["entity_uid"] == "e4" and again.last_task_type() == "lookup" and again.last_parameter() == "flow_rate"
    assert store.list_sessions()[0]["session_id"] == "abc"
    assert SessionState(session_id="x").last_entities() == []


def test_audit_store_append_and_filter(tmp_path):
    a = AuditStore(tmp_path)
    aid = a.new_id()
    a.write("sess/1", aid, "request", {"text": "hi"})
    a.write("sess/1", "other", "request", {"text": "other"})
    a.write("sess/1", aid, "final", {"status": "answered"})
    rows = a.read("sess/1", aid)
    assert [r["kind"] for r in rows] == ["request", "final"] and all(r["audit_id"] == aid for r in rows)
    assert len(a.read("sess/1")) == 3 and a.read("nobody") == []


def test_hitl_registry_record_pending_decide(tmp_path):
    from workbench.core.evidence import Confidence
    from workbench.core.result import FinalResponse

    h = HITLRegistry(tmp_path)
    ok = FinalResponse(response_id="r1", session_id="s", task_type=TaskType.LOOKUP, confidence=Confidence(score=0.9), audit_trail_id="a", requires_human_review=False)
    risky = FinalResponse(response_id="r2", session_id="s", task_type=TaskType.PROCEDURE, confidence=Confidence(score=0.7), audit_trail_id="b", requires_human_review=True, review_reason="changeover")
    h.record(ok, "q1")
    h.record(risky, "q2")
    pend = h.pending()
    assert [p["response_id"] for p in pend] == ["r2"] and pend[0]["reason"] == "changeover"
    rec = h.decide("r2", "approved", "shift-in-charge", "ok")
    assert rec["status"] == "approved" and h.pending() == []
