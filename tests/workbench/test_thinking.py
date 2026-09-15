"""Thinking trace: narration text, event collection, JSON persistence and terminal rendering."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from workbench.app.thinking_display import ThinkingDisplay
from workbench.core.events import ProgressEvent
from workbench.core.plan import PlanStep, StepStatus
from workbench.core.request import TaskType, UserRequest
from workbench.core.result import AgentResult
from workbench.orchestration import narration
from workbench.services.thinking_store import ThinkingStore, ThinkingTraceCollector


# ------------------------------------------------------------------ narration


def test_classification_narration_names_the_winning_rules(make_request):
    from workbench.agents.task_classifier import classify_by_rules

    out = classify_by_rules("What is the recommended way to start the CDU?")
    assert out.task_type is TaskType.PROCEDURE
    text = narration.classification(out)
    assert "procedure=" in text                      # the rule scores are shown
    assert "LLM was not called" in text              # and why no model was needed
    assert out.intent == "" or out.intent in text


def test_resolution_narration_explains_entities_and_the_safety_gate(make_request):
    req, _ = make_request("What safety precautions are required before working on the crude charge pump?")
    text = narration.resolution(req)
    assert "Safety gate" in text
    assert req.entities == [] or req.entities[0].mention in text


def test_retrieval_narration_justifies_the_route(make_request):
    req, ctx = make_request("Trace the crude flow from the crude charge pump to the atmospheric column.")
    text = narration.retrieval(req, ctx)
    assert f"Route '{ctx.route}'" in text
    assert "Retrieved" in text


def test_planning_narration_lists_the_steps(make_request, services):
    from workbench.agents.planner import PlannerAgent

    req, _ = make_request("What is the shutdown procedure for the atmospheric column?")
    res = AgentResult(agent="planner", step_id="plan")
    plan = PlannerAgent(services).make_plan(req, res)
    text = narration.planning(req, plan)
    assert "Routing matrix" in text
    for step in plan.steps:
        assert step.step_id in text


def test_step_narration_uses_the_agent_trace():
    step = PlanStep(step_id="find", agent="procedure", goal="Find the procedure", inputs={"mode": "find"}, depends_on=["scope"])
    assert "consumes scope" in narration.step_start(step).lower()
    res = AgentResult(agent="procedure", step_id="find", summary="3 procedure(s)", trace=["Searched the procedure index; kept 3 of 8."], missing=["prerequisites"])
    text = narration.step_finished(step, res)
    assert "Searched the procedure index" in text
    assert "Could not satisfy: prerequisites" in text


def test_unusable_model_prose_is_rejected_before_it_reaches_the_answer():
    """A small local model may restate the prompt or narrate the task; neither is an answer."""
    from workbench.agents.base import usable_narrative

    question = "Why is the crude heated before entering the atmospheric column?"
    assert not usable_narrative(question, question)                                   # verbatim echo
    assert not usable_narrative("Because it is heated.", question)                    # too short
    assert not usable_narrative(
        "We are given a list of facts and missing information. The task is to write an "
        "executive summary in plain language for a refinery operations team.", question)  # scratchpad
    assert usable_narrative(
        "The crude is heated in the preheat train and the atmospheric heater so that the light "
        "ends flash at the column inlet, which cuts the furnace duty needed for separation.", question)


def test_narration_never_leaks_python_reprs(make_request, services):
    """Thinking text is read by a person; list/tuple reprs would mean a raw value slipped through."""
    from workbench.agents.planner import PlannerAgent

    req, ctx = make_request("The crude charge pump discharge pressure is dropping. What should I check?")
    res = AgentResult(agent="planner", step_id="plan")
    plan = PlannerAgent(services).make_plan(req, res)
    for text in (narration.resolution(req), narration.retrieval(req, ctx), narration.planning(req, plan)):
        assert "[('" not in text and "', '" not in text


# ------------------------------------------------------------------ collector / store


def _events() -> list[ProgressEvent]:
    return [
        ProgressEvent(event="phase_started", phase="0/1 Understanding", message="0/1 Understanding"),
        ProgressEvent(event="agent_started", phase="0/1 Understanding", agent="task_classifier", step_id="classify", message="Scoring the wording"),
        ProgressEvent(event="agent_finished", phase="0/1 Understanding", agent="task_classifier", step_id="classify", message="procedure (0.98, rules)",
                      thinking="Rule scores: procedure=4.4.", decision="task type = procedure", data={"ok": True, "duration_ms": 2, "llm_calls": 0}),
        ProgressEvent(event="phase_finished", phase="0/1 Understanding", message="procedure"),
        ProgressEvent(event="phase_started", phase="3 Planning", message="3 Planning"),
        ProgressEvent(event="plan_created", phase="3 Planning", message="template 'procedure'", data={"steps": [{"id": "find", "agent": "procedure", "goal": "Find it", "depends_on": []}]}),
        ProgressEvent(event="llm_call", agent="planner", message="plan_refine", model="qwen3:4b", data={"prompt": "planner"}),
        ProgressEvent(event="agent_finished", phase="3 Planning", agent="planner", step_id="plan", message="template 'procedure' (1 step)",
                      thinking="Template expands to 1 step.", decision="template 'procedure', 1 steps", model="qwen3:4b",
                      data={"ok": True, "duration_ms": 900, "llm_calls": 1}),
        ProgressEvent(event="warning", phase="3 Planning", message="no entity resolved"),
    ]


def test_collector_groups_agents_under_their_phase():
    c = ThinkingTraceCollector("q", llm_model="qwen3:4b", backend="mock")
    for ev in _events():
        c.on_event(ev)
    data = c.to_dict()
    assert [p["name"] for p in data["phases"]] == ["0/1 Understanding", "3 Planning"]
    assert [a["name"] for a in data["phases"][0]["agents"]] == ["task_classifier"]
    classifier = data["phases"][0]["agents"][0]
    assert classifier["goal"] == "Scoring the wording"           # from agent_started
    assert classifier["decision"] == "task type = procedure"     # from agent_finished
    assert data["phases"][1]["llm_calls"] == 1
    assert data["llm_calls_detail"][0]["model"] == "qwen3:4b"
    assert data["plan"]["steps"][0]["id"] == "find"
    assert data["warnings"] == ["no entity resolved"]


def test_collector_records_the_final_response(orch):
    from workbench.core.request import UserRequest as UR

    c = ThinkingTraceCollector("What is the shutdown procedure for the atmospheric column?")
    resp = orch.ask(UR(text=c.query, session_id="trace-test"), on_event=c.on_event)
    data = c.record_response(resp).to_dict()
    assert data["audit_id"] == resp.audit_trail_id
    assert data["status"] == resp.status
    assert data["task_type"] == resp.task_type.value
    assert data["confidence"]["score"] == resp.confidence.score
    assert data["phases"], "every run produces at least one phase"
    assert all(s.get("status") in {st.value for st in StepStatus} | {"replaced"} for s in data["plan"]["steps"])


def test_store_writes_one_readable_json_file_per_run(cfg):
    c = ThinkingTraceCollector("q")
    c.audit_id = "20260916-000000-abcdef"
    for ev in _events():
        c.on_event(ev)
    path = ThinkingStore(cfg.paths.thinking_dir).save(c)
    assert path.exists() and path.name.endswith("_20260916-000000-abcdef.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["query"] == "q"
    assert len(data["phases"]) == 2
    assert ThinkingStore(cfg.paths.thinking_dir).latest(5)[0] == path


# ------------------------------------------------------------------ terminal rendering


def test_display_renders_every_phase_and_the_answer(orch):
    display = ThinkingDisplay("What is the shutdown procedure for the atmospheric column?", llm_model="none", backend="mock", profile="gpu_4gb")
    buf = io.StringIO()
    with redirect_stdout(buf):
        resp = orch.ask(UserRequest(text=display.query, session_id="display-test"), on_event=display.on_event)
        display.print_answer(resp, "### answer body")
    out = buf.getvalue()
    for phase in ("Understanding", "Specialist Retrieval", "Planning", "Execution", "Governance"):
        assert phase in out
    assert "Execution DAG" in out
    assert "ANSWER" in out and "### answer body" in out
    assert resp.status.upper() in out


def test_display_wraps_long_lines_to_the_terminal_width():
    display = ThinkingDisplay("q")
    display.width = 60
    buf = io.StringIO()
    with redirect_stdout(buf):
        display.on_event(ProgressEvent(event="agent_finished", phase="4 Execution", agent="procedure", step_id="find",
                                       message="ok", thinking="word " * 60, decision="d", data={"ok": True, "duration_ms": 1}))
    assert all(len(line) <= 60 for line in buf.getvalue().split("\n"))


def test_display_survives_an_event_with_no_thinking():
    display = ThinkingDisplay("q")
    buf = io.StringIO()
    with redirect_stdout(buf):
        display.on_event(ProgressEvent(event="agent_started", phase="4 Execution", agent="lookup", step_id="lookup", message="Look it up"))
        display.on_event(ProgressEvent(event="agent_finished", phase="4 Execution", agent="lookup", step_id="lookup", message="2 values", data={"ok": True}))
    assert "Look it up" in buf.getvalue() and "2 values" in buf.getvalue()
