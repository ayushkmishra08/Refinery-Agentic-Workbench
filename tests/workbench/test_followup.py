"""Follow-up resolution: reading a turn in the context of the ones before it.

The pipeline downstream reads one sentence with no memory, so a follow-up has to be rewritten
into a standalone question before it. The cases that matter are the ones an engineer actually
types at a console: a substitution ("what if we use X instead"), an elaboration ("and at
start-up?") and a bare pronoun ("why is it like that?").
"""
from __future__ import annotations

from workbench.core.request import UserRequest
from workbench.memory.followup import classify_followup, resolve_followup
from workbench.memory.session import SessionState, Turn

PUMP = {"mention": "12-P-01", "entity_uid": "e-12-P-01", "canonical_tag": "12-P-01",
        "name": "Quench Pumps (12-P-01)", "entity_type": "Pump", "confidence": 0.98, "method": "tag"}


def _session(*, entities=(PUMP,), request="what does pump 12-P-01 do?", parameter=None, answer="") -> SessionState:
    s = SessionState(session_id="t")
    s.turns.append(Turn(request=request, task_type="explanation", entities=list(entities),
                        parameter=parameter, answer_preview=answer))
    return s


# --------------------------------------------------------------------------- classification
def test_a_substitution_is_recognised_however_it_is_worded():
    s = _session()
    for text in ["what if we use 11-E-01 instead",
                 "what if we used 11-E-01 instead of that",
                 "could we put 11-E-01 in place of it",
                 "swap it for 11-E-01",
                 "and 11-E-01 instead?"]:
        assert classify_followup(text, s) == "substitution", text


def test_an_elaboration_and_a_pronoun_are_told_apart():
    s = _session()
    assert classify_followup("and at start-up?", s) == "elaboration"
    assert classify_followup("why?", s) == "elaboration"
    assert classify_followup("what is its discharge pressure?", s) == "continuation"


def test_a_question_that_names_its_own_subject_is_not_a_follow_up():
    s = _session()
    assert classify_followup("What is the design pressure of the vacuum column 12-C-01?", s) == "new"
    assert classify_followup("How do I start the crude charge pump after maintenance?", s) == "new"


def test_the_first_turn_of_a_conversation_is_never_a_follow_up():
    assert classify_followup("what if we use 11-E-01 instead", None) == "new"
    assert classify_followup("why?", SessionState(session_id="t")) == "new"


# --------------------------------------------------------------------------- rewriting
def test_a_substitution_carries_the_previous_subject_as_the_baseline():
    fu = resolve_followup("what if we use 11-E-01 instead", _session())
    assert fu.kind == "substitution" and fu.is_followup
    assert "12-P-01" in fu.rewritten and "11-E-01" in fu.rewritten
    assert "compare" in fu.rewritten.lower()
    assert fu.baseline_labels == ["12-P-01 (Quench Pumps)"]


def test_an_elaboration_attaches_the_new_angle_to_the_old_subject():
    fu = resolve_followup("and at start-up?", _session())
    assert fu.kind == "elaboration" and "12-P-01" in fu.rewritten and "start-up" in fu.rewritten


def test_a_pronoun_is_replaced_by_the_subject_it_refers_to():
    fu = resolve_followup("what is its discharge pressure?", _session())
    assert "12-P-01" in fu.rewritten and " it " not in f" {fu.rewritten} "


def test_the_parameter_of_the_previous_turn_is_carried_forward():
    fu = resolve_followup("and at start-up?", _session(parameter="flow_rate"))
    assert fu.carried_parameter == "flow_rate"


def test_nothing_is_carried_when_the_previous_turn_resolved_no_equipment():
    fu = resolve_followup("what if we use 11-E-01 instead", _session(entities=()))
    assert not fu.is_followup and "nothing to carry forward" in fu.note


# --------------------------------------------------------------------------- through the pipeline
def test_a_substitution_becomes_a_comparison_with_both_subjects_present(orch):
    orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="fu-1"))
    resp = orch.ask(UserRequest(text="what if we use 11-C-01 instead?", session_id="fu-1"))
    assert resp.task_type.value == "comparison"
    joined = " ".join(resp.entities)
    assert "11-C-01" in joined and any("Pump" in e or "pump" in e for e in resp.entities)


def test_the_session_records_what_the_engineer_typed_and_what_it_was_read_as(orch):
    orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="fu-2"))
    orch.ask(UserRequest(text="and at start-up?", session_id="fu-2"))
    last = orch.session_for("fu-2").turns[-1]
    assert last.request == "and at start-up?"
    assert last.rewritten_request and "start-up" in last.rewritten_request
    assert last.followup_kind == "elaboration"


def test_an_undocumented_tag_is_not_quietly_replaced_by_the_previous_subject(orch):
    """A short question naming its own (wrong) tag must not inherit the last turn's equipment.

    The session fallback exists for "why is it like that?". Letting it fire here would answer
    about a different machine than the one asked about, without saying so.
    """
    orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="fu-4"))
    resp = orch.ask(UserRequest(text="what does pump 99-Z-77 do", session_id="fu-4"))
    assert resp.status == "clarification" or "99-Z-77" in resp.answer_markdown
    assert "482" not in resp.answer_markdown, "the previous turn's value must not be served as this one's answer"


def test_a_self_contained_question_is_left_exactly_as_typed(orch):
    orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="fu-3"))
    orch.ask(UserRequest(text="What is the design pressure of the vacuum column?", session_id="fu-3"))
    last = orch.session_for("fu-3").turns[-1]
    assert last.rewritten_request == "" and last.followup_kind == "new"
