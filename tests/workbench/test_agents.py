"""Agents on the mock backend, LLM-free (FakeLLM(available=False))."""
from __future__ import annotations

import time

import pytest

from workbench.agents.base import AgentResult, blocks_to_markdown
from workbench.agents.calculation import CalculationAgent
from workbench.agents.diagnostic import DiagnosticAgent
from workbench.agents.governance import GovernanceAgent, relabel_block
from workbench.agents.procedure import ProcedureAgent
from workbench.agents.registry import AGENTS, TWELVE, describe_agents, load_agent
from workbench.agents.retrieval import GraphAgent, LookupAgent, describe_instrument
from workbench.agents.revision_conflict import RevisionConflictAgent
from workbench.agents.safety import SafetyAgent
from workbench.agents.verification import VerificationAgent
from workbench.core.blocks import AuditPhase, ClarificationBlock, KpiBlock, KpiItem
from workbench.core.evidence import Evidence
from workbench.core.plan import PlanStep
from workbench.core.request import SafetyStatus, TaskType
from workbench.core.result import Statement


def step(agent, sid="s1", **inputs):
    return PlanStep(step_id=sid, agent=agent, goal=sid, inputs=inputs)


# ----------------------------------------------------------------------------- registry
def test_registry_has_twelve_named_agents_plus_retrieval_specialists():
    assert set(TWELVE) <= set(AGENTS) and len(TWELVE) == 12
    assert {"lookup", "graph", "explanation", "cross_document"} <= set(AGENTS)
    for key in AGENTS:
        cls = load_agent(key)
        assert cls.name == key, key
    assert all(d["description"] for d in describe_agents())


# ----------------------------------------------------------------------------- lookup
def test_lookup_returns_normal_flow_kpi_with_evidence(services, make_request):
    req, ctx = make_request("What is the normal flow rate of the crude charge pump?")
    res = LookupAgent(services).run(req, ctx, step("lookup"))
    assert res.ok and res.blocks[0].type == "kpi"
    values = {(i.value, i.qualifier) for i in res.blocks[0].items}
    assert ("482", "normal") in values and ("470", "normal") in values
    assert all(i.citation for i in res.blocks[0].items) and len(res.evidence) == 2
    assert res.statements and all(s.evidence_keys for s in res.statements)


def test_lookup_limits_mode_gathers_envelope_claims(services, make_request):
    req, ctx = make_request("What is the normal operating range of the crude charge pump?")
    res = LookupAgent(services).run(req, ctx, step("lookup", mode="limits"))
    roles = {c["parameter_role"] for c in res.content["claims"]}
    assert {"normal", "minimum", "design"} <= roles


def test_lookup_identify_mode_tabulates_entities(services, make_request):
    req, ctx = make_request("The crude charge pump discharge pressure is dropping. What should I check?")
    res = LookupAgent(services).run(req, ctx, step("lookup", mode="identify"))
    assert res.blocks[0].type == "table" and "Crude Charge Pump" in res.blocks[0].rows[0][0]
    assert any(b.type == "text" and ("dropping" in b.markdown or "low" in b.markdown) for b in res.blocks)


# ----------------------------------------------------------------------------- graph
def test_graph_instruments_table_and_isa_description(services, make_request):
    req, ctx = make_request("Which instruments monitor the crude charge pump?")
    res = GraphAgent(services).run(req, ctx, step("graph"))
    tbl = next(b for b in res.blocks if b.type == "table")
    tags = {r[0] for r in tbl.rows}
    assert {"11-PG-101", "11-FRC-101"} <= tags
    assert describe_instrument("11-PG-101") == "pressure gauge"
    assert describe_instrument("11-FRC-101") == "flow recorder controller"
    assert describe_instrument("12-PSV-101") == "pressure safety valve"


def test_graph_trace_finds_documented_path(services, make_request):
    req, ctx = make_request("Trace the crude flow from the crude charge pump to the atmospheric column.")
    res = GraphAgent(services).run(req, ctx, step("graph"))
    g = next(b for b in res.blocks if b.type == "graph")
    assert len(g.edges) == 5 and g.edges[0].source == "e-11-PM-01" and g.edges[-1].target == "e-11-C-01"
    assert res.confidence.score >= 0.75 and "path" in res.summary
    assert g.mermaid and "graph LR" in g.mermaid


def test_graph_downstream_filter(services, make_request):
    req, ctx = make_request("Trace the downstream path of the crude desalter.")
    res = GraphAgent(services).run(req, ctx, step("graph"))
    g = next(b for b in res.blocks if b.type == "graph")
    assert all(e.source == "e-11-V-02" for e in g.edges) and g.edges[0].target == "e-11-V-10"


@pytest.mark.xfail(strict=False, reason="context_resolver skips single-word mentions in EQUIPMENT_WORDS, so 'the desalter' never resolves although the builder treats 'desalter' as a specific entity")
def test_single_word_specific_equipment_resolves(services, make_request):
    req, _ = make_request("Trace the downstream path of the desalter.")
    assert req.entity_uids() == ["e-11-V-02"]


# ----------------------------------------------------------------------------- procedure
def test_procedure_find_steps_and_prerequisites(services, make_request):
    req, ctx = make_request("How do I change over from the running crude charge pump to the standby pump?")
    agent = ProcedureAgent(services)
    find = agent.run(req, ctx, step("procedure", "find", mode="find"))
    assert find.content["procedure_ids"][0] == "proc-changeover-01"
    services.prior_results["find"] = find
    steps = agent.run(req, ctx, step("procedure", "steps", mode="steps"))
    sb = next(b for b in steps.blocks if b.type == "steps")
    assert [s.sequence for s in sb.steps] == [1, 2, 3, 4]
    assert "caution" in sb.steps[2].warnings and "11-PM-01B" in sb.steps[1].mentions
    assert all(s.citation for s in sb.steps) and len(steps.evidence) == 4
    pre = agent.run(req, ctx, step("procedure", "prereq", mode="prerequisites"))
    pb = next(b for b in pre.blocks if b.type == "steps")
    assert pb.steps and pb.steps[0].is_prerequisite and "Ensure" in pb.steps[0].text


@pytest.mark.xfail(strict=False, reason="ProcedureAgent._selected re-sorts candidates by score after the typed filter, so a higher-scoring changeover procedure outranks the requested isolation type")
def test_procedure_typed_request_does_not_fall_back_to_unrelated_procedures(services, make_request):
    req, ctx = make_request("What safety precautions are required before working on the crude charge pump?")
    res = ProcedureAgent(services).run(req, ctx, step("procedure", "procs", mode="find", types=["safety", "isolation", "maintenance", "emergency"]))
    assert res.content.get("procedure_ids") == ["proc-pump-isolation"]


def test_procedure_typed_request_includes_the_typed_procedure(services, make_request):
    req, ctx = make_request("What safety precautions are required before working on the crude charge pump?")
    res = ProcedureAgent(services).run(req, ctx, step("procedure", "procs", mode="find", types=["safety", "isolation", "maintenance", "emergency"]))
    assert "proc-pump-isolation" in res.content.get("procedure_ids", [])


@pytest.mark.xfail(strict=False, reason="ProcedureAgent accepts a type-only match (heater light-off) for a resolved entity that has no procedure (pre-flash drum) instead of reporting none")
def test_procedure_missing_requests_replan_when_required(services, make_request):
    req, ctx = make_request("Give me the startup procedure for the pre-flash drum.")
    assert req.entity_uids() == ["e-11-V-10"]
    res = ProcedureAgent(services).run(req, ctx, step("procedure", mode="find"))
    assert not res.content.get("procedure_ids") and res.needs_replan and res.blocks[0].type == "callout"


def test_procedure_missing_for_entity_without_any_procedure_and_action(services, make_request):
    req, ctx = make_request("Show me the water washing procedure for the pre-flash drum.")
    res = ProcedureAgent(services).run(req, ctx, step("procedure", mode="find"))
    assert not res.content.get("procedure_ids") and res.needs_replan and res.blocks[0].type == "callout"


# ----------------------------------------------------------------------------- diagnostic
def test_diagnostic_pipeline_extracts_causes_checks_actions(services, make_request):
    req, ctx = make_request("The crude charge pump discharge pressure is dropping. What should I check?")
    agent = DiagnosticAgent(services)
    upset = agent.run(req, ctx, step("diagnostic", "upset", mode="find_upset"))
    assert upset.content["chunk_ids"][0] == "ch-upset" and upset.blocks[0].type == "table"
    services.prior_results["upset"] = upset
    causes = agent.run(req, ctx, step("diagnostic", "causes", mode="causes"))
    ctab = next(b for b in causes.blocks if b.type == "table")
    joined = " ".join(r[0] for r in ctab.rows).lower()
    assert "strainer" in joined or "tank" in joined
    assert any(r[1].startswith("inferred") for r in ctab.rows)        # topology hypotheses are marked
    assert all(r[1] in ("documented", "inferred (topology)") for r in ctab.rows)
    checks = agent.run(req, ctx, step("diagnostic", "checks", mode="checks"))
    cb = next(b for b in checks.blocks if b.type == "steps")
    assert any("Check" in s.text for s in cb.steps) and all(s.citation for s in cb.steps)
    actions = agent.run(req, ctx, step("diagnostic", "actions", mode="actions"))
    ab = next(b for b in actions.blocks if b.type == "steps")
    assert any("standby pump" in s.text or "reduced" in s.text for s in ab.steps)
    assert causes.content["causes"] and checks.content["checks"] and actions.content["actions"]


# ----------------------------------------------------------------------------- calculation
def test_calculation_check_value_within_design(services, make_request):
    req, ctx = make_request("The crude charge pump is operating at 500 m3/h. Is this acceptable?")
    res = CalculationAgent(services).run(req, ctx, step("calculation", "compare", mode="check_value"))
    gauge = next(b for b in res.blocks if b.type == "limit_gauge")
    assert gauge.value == 500 and gauge.unit == "m3/h"
    assert {m.label for m in gauge.markers} >= {"minimum", "normal", "design"}
    assert gauge.verdict == "within_normal" and res.content["verdict"] == "within_normal"     # within 5% of normal 482
    assert res.content["deviations"]["vs_normal_pct"] > 0 and res.content["deviations"]["vs_design_pct"] < 0
    assert all(m.citation for m in gauge.markers)


def test_calculation_outside_design_and_range_mode(services, make_request):
    req, ctx = make_request("The crude charge pump is operating at 600 m3/h. Is this acceptable?")
    res = CalculationAgent(services).run(req, ctx, step("calculation", mode="check_value"))
    assert res.content["verdict"] == "outside_design"
    req2, ctx2 = make_request("What is the normal operating range of the crude charge pump?")
    rng = CalculationAgent(services).run(req2, ctx2, step("calculation", mode="range"))
    assert rng.blocks[0].type == "limit_gauge" and rng.blocks[0].value is None
    assert {m["label"] for m in rng.content["markers"]} >= {"minimum", "normal", "design"}


# ----------------------------------------------------------------------------- revision / conflict
def test_revision_conflict_collect_and_resolve(services, make_request):
    req, ctx = make_request("I found two different normal flow values for the crude charge pump. Which one should I trust?")
    agent = RevisionConflictAgent(services)
    col = agent.run(req, ctx, step("revision_conflict", "claims", mode="collect"))
    prov = next(b for b in col.blocks if b.type == "table")
    assert prov.columns[:2] == ["Subject", "Parameter"] and len(prov.rows) >= 4
    res = agent.run(req, ctx, step("revision_conflict", "resolve", mode="resolve"))
    cb = next(b for b in res.blocks if b.type == "conflict")
    assert cb.status in ("resolved", "unresolved") and cb.preferred_index == 0
    assert cb.claims[0].value == "482" and cb.claims[1].value == "470"          # table beats prose
    assert "Preferred because" in cb.resolution
    assert any(b.type == "callout" and "different fact" in b.markdown for b in res.blocks)   # role/location contexts explained


def test_revision_conflict_check_is_silent_when_values_agree(services, make_request):
    req, ctx = make_request("What is the design pressure of the atmospheric column?")
    res = RevisionConflictAgent(services).run(req, ctx, step("revision_conflict", mode="check"))
    assert res.blocks == [] and "no conflicting" in res.summary


# ----------------------------------------------------------------------------- safety
def test_safety_answer_quotes_documented_precautions(services, make_request):
    req, ctx = make_request("What safety precautions are required before working on the crude charge pump?")
    res = SafetyAgent(services).run(req, ctx, step("safety", mode="answer"))
    sb = next(b for b in res.blocks if b.type == "safety")
    msgs = " ".join(f.message for f in sb.flags).lower()
    assert "work permit" in msgs and "ppe" in msgs
    assert any(f.severity == "danger" for f in sb.flags)            # "Never bypass ..."
    assert all(f.citation for f in sb.flags) and res.safety_flags


def test_safety_restricted_mode_gives_authorization_path_only(services, make_request):
    req, ctx = make_request("Can I bypass the crude charge pump low flow trip temporarily?")
    assert req.safety_status == SafetyStatus.RESTRICTED
    res = SafetyAgent(services).run(req, ctx, step("safety", mode="answer"))
    sb = res.blocks[0]
    assert sb.type == "safety" and sb.flags[0].severity == "danger" and sb.flags[0].requires_authorization
    assert res.content["restricted"] is True
    assert not any(b.type == "steps" for b in res.blocks)
    assert any("authorisation" in f.message.lower() or "authorization" in f.message.lower() for f in sb.flags)


def test_safety_review_procedure_flags_step_warnings(services, make_request):
    req, ctx = make_request("How do I change over from the running crude charge pump to the standby pump?")
    pa = ProcedureAgent(services)
    services.prior_results["find"] = pa.run(req, ctx, step("procedure", "find", mode="find"))
    services.prior_results["steps"] = pa.run(req, ctx, step("procedure", "steps", mode="steps"))
    res = SafetyAgent(services).run(req, ctx, step("safety", mode="review_procedure"))
    sb = next(b for b in res.blocks if b.type == "safety")
    assert any(f.message.startswith("Step 3") for f in sb.flags)


# ----------------------------------------------------------------------------- verification
def test_verification_penalises_numbers_not_in_evidence(services, make_request):
    req, ctx = make_request("What is the normal flow rate of the crude charge pump?")
    good = AgentResult(agent="lookup", step_id="lookup")
    ev = Evidence(document_id="doc", text="Normal flow 482 m3/h", page=61, claim_id="c1")
    k = good.add_evidence(ev)
    good.statements.append(Statement(text="normal flow = 482 m3/h", evidence_keys=[k], numbers=["482"]))
    good.statements.append(Statement(text="normal flow = 999 m3/h", evidence_keys=[k], numbers=["999"]))
    good.statements.append(Statement(text="orphan claim", evidence_keys=["does-not-exist"]))
    services.prior_results["lookup"] = good
    res = VerificationAgent(services).run(req, ctx, step("verification", "verify"))
    v = good.content["verification"]
    assert v["checked"] == 3 and v["ok"] == 1 and v["score"] < 0.5
    assert any("999" in p for p in v["problems"]) and any("missing evidence" in p for p in v["problems"])
    assert res.content["overall_score"] < 0.5 and res.blocks and res.blocks[0].type == "table"


def test_verification_flags_unknown_tags_in_text(services, make_request):
    req, ctx = make_request("What is the normal flow rate of the crude charge pump?")
    r = AgentResult(agent="explanation", step_id="x")
    r.blocks.append(r.blocks.__class__())  # noqa: placeholder to keep list type
    r.blocks.clear()
    from workbench.core.blocks import TextBlock

    r.blocks.append(TextBlock(markdown="See 99-ZZ-999 for details."))
    services.prior_results["x"] = r
    res = VerificationAgent(services).run(req, ctx, step("verification", "verify"))
    assert "99-ZZ-999" in res.content["unknown_tags"]


# ----------------------------------------------------------------------------- governance
def test_governance_relabels_citations_and_orders_blocks(services, make_request):
    req, ctx = make_request("What is the normal flow rate of the crude charge pump?")
    look = LookupAgent(services).run(req, ctx, step("lookup", "lookup"))
    services.prior_results["lookup"] = look
    ver = VerificationAgent(services).run(req, ctx, step("verification", "verify"))
    resp = GovernanceAgent(services).compose(req, None, {"lookup": look, "verify": ver}, "audit-1", [AuditPhase(name="p", status="done", duration_ms=1)], "mock", time.time())
    assert resp.status == "answered" and resp.blocks[-1].type == "audit" and resp.blocks[-2].type == "evidence"
    kpi = next(b for b in resp.blocks if b.type == "kpi")
    assert all(i.citation and i.citation.startswith("[") for i in kpi.items)
    assert [e.ref for e in resp.evidence] == [f"[{i}]" for i in range(1, len(resp.evidence) + 1)]
    assert resp.blocks[0].type == "callout" and "482" in resp.blocks[0].markdown
    assert 0 < resp.confidence.score <= 1 and resp.audit_trail_id == "audit-1"
    assert "482" in resp.answer_markdown and "[1]" in resp.answer_markdown


def test_governance_clarification_status_and_restricted_review(services, make_request):
    req, ctx = make_request("Can I run this at 500?")
    r = AgentResult(agent="context_resolver", step_id="clarify", blocks=[ClarificationBlock(question="which?", missing=["entity"])])
    resp = GovernanceAgent(services).compose(req, None, {"clarify": r}, "a", [], "mock", time.time())
    assert resp.status == "clarification" and resp.blocks[0].type == "clarification"
    req2, ctx2 = make_request("Can I bypass the crude charge pump low flow trip temporarily?")
    saf = SafetyAgent(services).run(req2, ctx2, step("safety"))
    resp2 = GovernanceAgent(services).compose(req2, None, {"safety": saf}, "b", [], "mock", time.time())
    assert resp2.status == "restricted" and resp2.requires_human_review and resp2.blocks[0].level == "danger"


def test_relabel_block_rewrites_nested_citations():
    from workbench.services.evidence_store import EvidenceStore

    store = EvidenceStore()
    k = store.add(Evidence(document_id="d", text="x", chunk_id="c1"))
    b = KpiBlock(items=[KpiItem(label="l", value="1", citation=k)], citations=[k])
    relabel_block(b, store)
    assert b.items[0].citation == "[1]" and b.citations == ["[1]"]


def test_blocks_to_markdown_renders_every_block_type():
    from tests.workbench.test_core import SAMPLE_BLOCKS

    md = blocks_to_markdown(SAMPLE_BLOCKS)
    for needle in ("hello", "careful", "482", "| a | b |", "1. do", "feeds", "Plan:", "Evidence", "Comparison", "Limit check", "corroborated", "DANGER", "Confidence", "Clarification", "img/1.png", "Audit"):
        assert needle in md, needle
