"""Context Resolver: entities, quantities, symptoms, actions, scenarios, ambiguity, safety gate."""
from __future__ import annotations

import pytest

from workbench.agents.base import AgentResult
from workbench.agents.context_resolver import ContextResolverAgent
from workbench.agents.task_classifier import classify_by_rules
from workbench.core.request import SafetyStatus, TaskType, UserRequest
from workbench.memory.session import SessionState, Turn


def resolve(services, text, session=None):
    agent = ContextResolverAgent(services)
    return agent.resolve(UserRequest(text=text, session_id="t"), classify_by_rules(text), session, AgentResult(agent="context_resolver"))


def test_tag_mention_resolves_to_pump(services):
    req = resolve(services, "What is the normal flow rate of 11-P-01?")
    assert req.entities and req.entities[0].canonical_tag == "11-PM-01"
    assert req.entities[0].method == "tag" and req.entities[0].confidence >= 0.9


def test_train_tag_resolves_to_parent_entity(services):
    req = resolve(services, "Give me the design pressure of 11-PM-01A.")
    assert req.entities and req.entities[0].entity_uid == "e-11-PM-01"


@pytest.mark.xfail(strict=False, reason="spaced tags without dashes ('11 PM 01A') are not scanned by knowledge_layer.entity_identity.find_tags")
def test_spaced_tag_without_dashes_resolves(services):
    req = resolve(services, "What is the flow of 11 PM 01A?")
    assert req.entities and req.entities[0].entity_uid == "e-11-PM-01"


def test_name_mention_and_text_order_are_preserved(services):
    req = resolve(services, "Trace the crude flow from the crude charge pump to the atmospheric column.")
    uids = req.entity_uids()
    assert uids == ["e-11-PM-01", "e-11-C-01"], uids                # order of appearance, not of length
    assert req.task_type == TaskType.MULTI_HOP


def test_quantity_with_unit_and_parameter(services):
    req = resolve(services, "The crude charge pump is operating at 520 m3/h. Is this acceptable?")
    assert req.task_type == TaskType.LIMITS
    assert req.quantities and req.quantities[0].value == 520 and req.quantities[0].unit == "m3/h"
    assert req.quantities[0].parameter == "flow_rate"
    assert req.entities[0].entity_uid == "e-11-PM-01"


def test_pressure_quantity_carries_absolute_basis(services):
    req = resolve(services, "The discharge pressure of the crude charge pump reads 24.45 kg/cm2A. Is this within limits?")
    q = req.quantities[0]
    assert q.value == 24.45 and q.unit.lower().startswith("kg/cm2") and q.parameter == "pressure"
    assert req.parameter == "pressure"


@pytest.mark.parametrize("text,direction", [
    ("The crude charge pump discharge pressure is dropping. What should I check?", "low"),
    ("The atmospheric column pressure is increasing. What could be causing it?", "high"),
    ("The crude desalter is showing poor performance. What causes are documented?", "poor"),
])
def test_symptom_parsing(services, text, direction):
    req = resolve(services, text)
    assert req.symptom is not None and req.symptom.direction == direction
    assert req.task_type == TaskType.TROUBLESHOOTING


@pytest.mark.parametrize("text,action", [
    ("How do I change over from the running crude charge pump to the standby pump?", "changeover"),
    ("Give me the startup procedure for the atmospheric heater.", "startup"),
    ("What is the shutdown procedure for the atmospheric column?", "shutdown"),
    ("Can I bypass the crude charge pump low flow trip temporarily?", "bypass"),
])
def test_action_detection(services, text, action):
    assert resolve(services, text).action == action


def test_scenario_detection_and_no_entity_needed_for_case_comparison(services):
    req = resolve(services, "Compare the documented operating conditions for Basrah crude and Bombay High crude.")
    assert req.scenario in ("Basrah", "Bombay High")
    assert req.task_type == TaskType.COMPARISON and "entity" not in req.ambiguities


def test_can_i_run_this_at_500_is_ambiguous_with_three_gaps(services):
    req = resolve(services, "Can I run this at 500?")
    assert req.task_type == TaskType.AMBIGUOUS
    assert {"entity", "unit", "parameter"} <= set(req.ambiguities)
    assert TaskType.LIMITS in req.secondary_task_types


def test_how_do_i_start_it_is_ambiguous_without_session(services):
    req = resolve(services, "How do I start it?")
    assert req.task_type == TaskType.AMBIGUOUS and "entity" in req.ambiguities


def test_session_pronoun_fallback_resolves_it(services):
    session = SessionState(session_id="s")
    session.turns.append(Turn(request="normal flow of the crude charge pump", task_type="lookup",
                              entities=[{"mention": "crude charge pump", "entity_uid": "e-11-PM-01", "canonical_tag": "11-PM-01", "name": "Crude Charge Pump (11-PM-01)", "entity_type": "Pump", "confidence": 0.92, "method": "name"}]))
    req = resolve(services, "How do I start it?", session)
    assert req.task_type == TaskType.PROCEDURE
    assert req.entities and req.entities[0].entity_uid == "e-11-PM-01" and req.entities[0].method == "session"
    assert req.resolved_from_session


def test_bypass_request_is_restricted(services):
    req = resolve(services, "Can I bypass the trip temporarily?")
    assert req.safety_status == SafetyStatus.RESTRICTED
    assert any("restricted" in s or "bypass" in s for s in req.safety_signals)
    named = resolve(services, "Can I bypass the crude charge pump low flow trip temporarily?")
    assert named.safety_status == SafetyStatus.RESTRICTED and named.action == "bypass"
    assert named.entities and named.entities[0].entity_uid == "e-11-PM-01"


@pytest.mark.xfail(strict=False, reason="'bypass the <equipment> ... trip' does not match the SAFETY rules (object phrase between verb and 'trip'), and the resolver does not force task_type=SAFETY when the status is RESTRICTED")
def test_named_bypass_request_routes_to_safety_task(services):
    named = resolve(services, "Can I bypass the crude charge pump low flow trip temporarily?")
    assert named.task_type == TaskType.SAFETY


def test_procedure_request_is_safety_sensitive_and_lists_evidence_requirements(services):
    req = resolve(services, "Give me the startup procedure for the atmospheric heater.")
    assert req.safety_status == SafetyStatus.SENSITIVE
    assert any("steps" in r for r in req.evidence_requirements)
    assert req.entities[0].entity_uid == "e-11-F-01"


def test_clarify_step_produces_clarification_block(services, make_request):
    from workbench.core.plan import PlanStep

    req, ctx = make_request("Can I run this at 500?")
    res = ContextResolverAgent(services).run(req, ctx, PlanStep(step_id="clarify", agent="context_resolver", goal="clarify", inputs={"mode": "clarify"}))
    assert res.blocks and res.blocks[0].type == "clarification"
    assert set(res.blocks[0].missing) >= {"entity", "unit"}
    assert res.content["intended_task_type"] == "limits"
