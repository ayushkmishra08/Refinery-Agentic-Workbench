"""Core contracts: task types, routing completeness, plan DAG, render blocks, schemas."""
from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter

from workbench.core import blocks as B
from workbench.core.blocks import Block
from workbench.core.evidence import Confidence, Evidence
from workbench.core.plan import Plan, PlanStep, StepStatus
from workbench.core.request import TaskType, UserRequest
from workbench.core.result import FinalResponse
from workbench.orchestration.router import RETRIEVAL_ROUTE, ROUTING_MATRIX, SAFETY_REVIEWED


def test_routing_matrix_and_routes_cover_every_task_type():
    assert set(ROUTING_MATRIX) == set(TaskType)
    assert set(RETRIEVAL_ROUTE) == set(TaskType)
    assert set(RETRIEVAL_ROUTE.values()) <= {"claims", "graph", "proc", "hybrid", "inventory", "none"}
    assert SAFETY_REVIEWED <= set(TaskType)


def test_plan_ready_steps_respects_dependencies():
    plan = Plan(plan_id="p", goal="g", steps=[
        PlanStep(step_id="a", agent="lookup", goal="x"),
        PlanStep(step_id="b", agent="safety", goal="y", depends_on=["a"]),
        PlanStep(step_id="c", agent="report", goal="z", depends_on=["a", "b"]),
    ])
    assert [s.step_id for s in plan.ready_steps()] == ["a"]
    plan.step("a").mark(StepStatus.DONE)
    assert [s.step_id for s in plan.ready_steps()] == ["b"]
    plan.step("b").mark(StepStatus.SKIPPED)          # skipped counts as satisfied
    assert [s.step_id for s in plan.ready_steps()] == ["c"]
    assert not plan.is_complete()
    plan.step("c").mark(StepStatus.FAILED, "boom")
    assert plan.is_complete()
    assert [s.step_id for s in plan.failed_required()] == ["c"]


def test_plan_validate_dag_detects_cycles_and_unknown_deps():
    ok = Plan(plan_id="p", goal="g", steps=[PlanStep(step_id="a", agent="lookup", goal="x"), PlanStep(step_id="b", agent="lookup", goal="y", depends_on=["a"])])
    assert ok.validate_dag() == []
    cyc = Plan(plan_id="p", goal="g", steps=[PlanStep(step_id="a", agent="lookup", goal="x", depends_on=["b"]), PlanStep(step_id="b", agent="lookup", goal="y", depends_on=["a"])])
    assert any("cycle" in p for p in cyc.validate_dag())
    unknown = Plan(plan_id="p", goal="g", steps=[PlanStep(step_id="a", agent="lookup", goal="x", depends_on=["zz"])])
    assert any("unknown" in p for p in unknown.validate_dag())
    dup = Plan(plan_id="p", goal="g", steps=[PlanStep(step_id="a", agent="lookup", goal="x"), PlanStep(step_id="a", agent="lookup", goal="y")])
    assert any("duplicate" in p for p in dup.validate_dag())


def test_plan_to_mermaid_lists_nodes_and_edges():
    plan = Plan(plan_id="p", goal="g", steps=[PlanStep(step_id="a", agent="lookup", goal="Find \"it\""), PlanStep(step_id="b", agent="safety", goal="Check", depends_on=["a"])])
    mm = plan.to_mermaid()
    assert mm.startswith("graph TD")
    assert "a --> b" in mm
    assert "Find 'it'" in mm and '""' not in mm          # inner double quotes are turned into single quotes
    assert "<i>lookup</i>" in mm


EV = Evidence(document_id="doc", text="Normal flow 482 m3/h", page=61, chunk_id="ch1", source="table")
SAMPLE_BLOCKS: list[Block] = [
    B.TextBlock(markdown="hello"),
    B.CalloutBlock(level="warning", markdown="careful"),
    B.KpiBlock(items=[B.KpiItem(label="flow", value="482", unit="m3/h", qualifier="normal", citation="[1]")]),
    B.TableBlock(columns=["a", "b"], rows=[["x", 1], [None, 2.5]], row_citations=[["[1]"], []]),
    B.StepsBlock(steps=[B.StepItem(sequence=1, text="do", page=3, warnings=["caution"], mentions=["11-P-01"])], prerequisites=[B.StepItem(sequence=0, text="pre", is_prerequisite=True)]),
    B.GraphBlock(nodes=[B.GraphNode(id="n1", label="Pump", is_focus=True), B.GraphNode(id="n2", label="Column")], edges=[B.GraphEdge(source="n1", target="n2", label="feeds")], mermaid="graph LR"),
    B.PlanBlock(goal="g", tasks=[B.PlanTask(id="t1", title="T", agent="lookup", status="done")]),
    B.EvidenceBlock(items=[B.EvidenceItem(ref="[1]", document_id="doc", text="t", page=1)]),
    B.ComparisonBlock(subjects=["A", "B"], attributes=["flow"], cells=[[B.ComparisonCell(value="1"), B.ComparisonCell(value=None, note="n/a")]], differences=["flow differs"]),
    B.LimitGaugeBlock(entity="pump", parameter="flow", unit="m3/h", value=520.0, markers=[B.GaugeMarker(label="normal", value=482.0)], verdict="within_design", message="m"),
    B.ConflictBlock(subject="pump", parameter="flow", claims=[B.ConflictClaim(value="482", document_id="doc", source="table")], status="corroborated"),
    B.SafetyBlock(flags=[B.SafetyFlagItem(severity="danger", message="stop", requires_authorization=True)]),
    B.ConfidenceBlock(score=0.7, level="medium", basis="b", uncertainties=["u"]),
    B.ClarificationBlock(question="which?", missing=["entity"], options=["11-P-01"]),
    B.ImageBlock(url="img/1.png", caption="c", page=2),
    B.AuditBlock(audit_id="a1", phases=[B.AuditPhase(name="p", status="done", duration_ms=3)], llm_calls=0, backend="mock"),
]


@pytest.mark.parametrize("block", SAMPLE_BLOCKS, ids=[b.type for b in SAMPLE_BLOCKS])
def test_every_block_type_round_trips_through_the_discriminated_union(block):
    adapter = TypeAdapter(Block)
    raw = adapter.dump_json(block)
    back = adapter.validate_json(raw)
    assert type(back) is type(block)
    assert back == block


def test_block_types_are_unique_and_match_class_names():
    types = [b.type for b in SAMPLE_BLOCKS]
    assert len(types) == len(set(types)) == 16


def test_final_response_schema_exports_and_validates():
    schema = FinalResponse.model_json_schema()
    assert "blocks" in schema["properties"] and "confidence" in schema["properties"]
    resp = FinalResponse(response_id="r", session_id="s", task_type=TaskType.LOOKUP, confidence=Confidence(score=0.5), audit_trail_id="a", blocks=SAMPLE_BLOCKS)
    data = json.loads(resp.model_dump_json())
    assert data["task_type"] == "lookup" and len(data["blocks"]) == len(SAMPLE_BLOCKS)
    assert FinalResponse.model_validate(data).blocks[0].type == "text"
    assert Confidence(score=0.8).level == "high" and Confidence(score=0.5).level == "medium" and Confidence(score=0.1).level == "low"


def test_evidence_key_is_stable_and_deduplicates_same_claim():
    a = Evidence(document_id="d", text="x", claim_id="c1")
    b = Evidence(document_id="d", text="different text", claim_id="c1")
    assert a.key() == b.key()
    assert Evidence(document_id="d", text="x", chunk_id="ch1").key() != a.key()


def test_user_request_defaults():
    r = UserRequest(text="hi")
    assert r.session_id == "default" and r.attachments == [] and r.options == {}
