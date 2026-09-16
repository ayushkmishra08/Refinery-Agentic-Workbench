"""The Answer Composer and the released markdown.

Two claims are under test. First, that the answer an engineer reads is prose written from the
retrieved material, not the material itself — with a model and without one. Second, that the
prose is held to the evidence: a figure or a tag the brief does not contain gets the answer
re-asked and then thrown away.
"""
from __future__ import annotations

import pytest

from workbench.agents.base import AgentServices
from workbench.agents.composer import AnswerComposerAgent, ComposedAnswer
from workbench.core.request import UserRequest
from workbench.core.result import AgentResult
from workbench.llm.fake import FakeLLM


def _compose(services, make_request, text, answers=None, session=None):
    """Run the composer over a real request/context, optionally with a scripted model."""
    req, ctx = make_request(text, session)
    if answers is not None:
        services = AgentServices(knowledge=services.knowledge, llm=FakeLLM(responses={"ComposedAnswer": answers}),
                                 cfg=services.cfg, session=session)
        services.cfg = services.cfg.model_copy(deep=True)
        services.cfg.llm.use_llm_for_answer = True
    result = AgentResult(agent="answer_composer", step_id="compose")
    AnswerComposerAgent(services).compose(req, ctx, {}, result)
    return result


# --------------------------------------------------------------------------- without a model
def test_the_rule_written_answer_is_prose_not_a_block_dump(services, make_request):
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?")
    answer = result.content["answer"]
    assert result.content["source"] == "deterministic"
    assert "482" in answer and "m3/h" in answer
    assert "|" not in answer and not answer.lstrip().startswith(("#", "-", "*"))
    assert answer.rstrip().endswith(".")
    assert len(result.blocks) == 1 and result.blocks[0].type == "text"


def test_a_question_the_documents_do_not_answer_says_so_before_reciting_what_they_do(services, make_request):
    """Asked for a vibration limit, retrieval returns flow rates; those are not the answer."""
    result = _compose(services, make_request, "What is the bearing vibration limit of the crude charge pump?")
    answer = result.content["answer"]
    first_sentence = answer.split(". ")[0].lower()
    assert "do not record" in first_sentence and "vibration" in first_sentence


def test_the_parameter_asked_about_comes_first(services, make_request):
    """Asked for a flow rate, the answer does not open with the suction pressure."""
    answer = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?").content["answer"]
    first_sentence = answer.split(". ")[0]
    assert "flow rate" in first_sentence


# --------------------------------------------------------------------------- with a model
def test_a_model_written_answer_is_used_when_it_is_grounded(services, make_request):
    grounded = ComposedAnswer(answer="The crude charge pump is documented at a normal rate of 482 m3/h, which is the "
                                     "figure the manual gives for continuous operation of the unit on design crude.",
                              assumptions=[], not_documented=[])
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?", answers=grounded)
    assert result.content["source"] == "model"
    assert result.content["answer"].startswith("The crude charge pump is documented")


def test_an_invented_figure_is_rejected_and_the_rule_written_answer_is_released(services, make_request):
    invented = ComposedAnswer(answer="The crude charge pump runs at a normal rate of 999 m3/h and a discharge pressure "
                                     "of 71 kg/cm2, both of which are documented in the operating manual for this unit.",
                              assumptions=[], not_documented=[])
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?", answers=invented)
    assert result.content["source"] == "deterministic"
    assert result.content["grounding_failed"] is True
    assert "999" not in result.content["answer"]
    assert any("not in the evidence" in t for t in result.trace)


def test_an_invented_tag_is_rejected(services, make_request):
    invented = ComposedAnswer(answer="The crude charge pump 99-Z-77 delivers a normal rate of 482 m3/h into the "
                                     "atmospheric column, according to the documented operating figures for the unit.",
                              assumptions=[], not_documented=[])
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?", answers=invented)
    assert result.content["source"] == "deterministic" and "99-Z-77" not in result.content["answer"]


def test_a_preamble_sentence_is_cut_and_the_answer_behind_it_kept(services, make_request):
    padded = ComposedAnswer(answer="The engineer asked about the crude charge pump. The manual documents a normal rate "
                                   "of 482 m3/h for that pump, which is the figure to use for continuous operation "
                                   "on the design crude case.",
                            assumptions=[], not_documented=[])
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?", answers=padded)
    assert result.content["source"] == "model"
    assert result.content["answer"].startswith("The manual documents")


def test_a_tag_given_the_wrong_name_is_rejected(services, make_request):
    """A right tag with a wrong name passes a figures-and-tags check and still misleads."""
    misnamed = ComposedAnswer(answer="The crude charge pump 11-PM-01 (boiler feed water tank) delivers a normal rate "
                                     "of 482 m3/h into the unit under continuous operation on the design crude.",
                              assumptions=[], not_documented=[])
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?", answers=misnamed)
    assert result.content["source"] == "deterministic"
    assert any("boiler feed water tank" in t for t in result.trace)


def test_a_qualifier_that_contradicts_the_documented_name_is_rejected(services, make_request):
    """"Atmospheric" and "vacuum" name two different sections of the plant."""
    from workbench.agents.base import AgentServices
    from workbench.agents.composer import AnswerComposerAgent

    agent = AnswerComposerAgent(AgentServices(knowledge=services.knowledge, llm=services.llm, cfg=services.cfg))
    documented_name = services.knowledge.resolve_entity("11-C-01", limit=1)[0].name.split(" (")[0].lower()
    opposite = "vacuum" if "atmospheric" in documented_name else "atmospheric"
    problems = agent._misnamed_tags(f"the flow goes to the {opposite} column (11-C-01) next")
    assert problems and "are not the same" in problems[0]


def test_a_correctly_named_tag_passes(services, make_request):
    from workbench.agents.base import AgentServices
    from workbench.agents.composer import AnswerComposerAgent

    agent = AnswerComposerAgent(AgentServices(knowledge=services.knowledge, llm=services.llm, cfg=services.cfg))
    name = services.knowledge.resolve_entity("11-C-01", limit=1)[0].name.split(" (")[0]
    assert agent._misnamed_tags(f"the flow goes to the {name} (11-C-01) next") == []
    assert agent._misnamed_tags("11-C-01 (normal) and 11-P-01 (A/B)") == [], "qualifiers are not names"


def test_a_relation_endpoint_carries_its_documented_name_into_the_brief(services, make_request):
    """A bare tag in the brief is a name the model will invent; the documented one is supplied."""
    result = _compose(services, make_request, "Trace the crude flow from the crude charge pump to the atmospheric column.")
    answer = result.content["answer"]
    if "11-C-01" in answer:
        assert "(" in answer, "a tag in the answer should arrive with the name attached"


def test_an_answer_that_is_all_preamble_is_thrown_away(services, make_request):
    waffle = ComposedAnswer(answer="The engineer is asking about a pump. Let me look at what the brief contains here.",
                            assumptions=[], not_documented=[])
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?", answers=waffle)
    assert result.content["source"] == "deterministic"


# --------------------------------------------------------------------------- the brief
def test_the_brief_is_kept_within_its_token_budget(services, make_request):
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?")
    budget_chars = services.cfg.llm.answer_brief_tokens * 3.6
    assert result.content["brief_chars"] <= budget_chars
    assert "question" in result.content["brief_sections"] and "identity" in result.content["brief_sections"]


def test_the_source_pages_are_the_pages_that_reached_the_brief(services, make_request):
    result = _compose(services, make_request, "What is the normal flow rate of the crude charge pump?")
    assert result.content["brief_pages"], "the answer has to be able to say where it came from"
    assert all(isinstance(p, int) for p in result.content["brief_pages"])


# --------------------------------------------------------------------------- released markdown
def test_the_released_answer_leads_with_the_prose_and_carries_a_source_line(orch):
    resp = orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="comp-1"))
    assert resp.blocks[0].id == "answer" and resp.blocks[0].type == "text"
    assert resp.answer_markdown.startswith(resp.blocks[0].markdown[:40])
    assert "*Source: CDU operating manual, p." in resp.answer_markdown


def test_the_released_answer_leaves_out_the_blocks_that_reason_about_it(orch):
    resp = orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="comp-2"))
    assert "Confidence:" not in resp.answer_markdown
    assert "Verification notes" not in resp.answer_markdown
    assert "### Evidence" not in resp.answer_markdown
    # ... while the response still carries them for a frontend to render
    assert any(b.type == "confidence" for b in resp.blocks)
    assert any(b.type == "evidence" for b in resp.blocks)


def test_a_procedure_keeps_its_ordered_steps_under_the_prose(orch):
    resp = orch.ask(UserRequest(text="What is the recommended way to start the CDU?", session_id="comp-3"))
    assert resp.blocks[0].id == "answer"
    assert any(b.type == "steps" for b in resp.blocks)
    assert "1." in resp.answer_markdown, "steps are the one thing prose should not paraphrase"


def test_a_long_supporting_table_is_cut_short_with_a_note(orch, cfg):
    resp = orch.ask(UserRequest(text="Compare the crude charge pump and the atmospheric column.", session_id="comp-4"))
    for block in resp.blocks:
        if block.type == "comparison" and len(block.attributes) > cfg.presentation.max_supporting_rows:
            assert "first" in resp.answer_markdown and "attributes" in resp.answer_markdown
            break


def test_the_full_style_returns_the_whole_block_rendering(orch, cfg):
    cfg.presentation.style = "full"
    try:
        resp = orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="comp-5"))
        assert "Confidence:" in resp.answer_markdown
    finally:
        cfg.presentation.style = "brief"


def test_a_clarification_is_never_recomposed_into_prose(orch):
    resp = orch.ask(UserRequest(text="What is the flow rate?", session_id="comp-6"))
    assert resp.status == "clarification"
    assert not any(b.id == "answer" for b in resp.blocks)
    assert any(b.type == "clarification" for b in resp.blocks)


def test_a_restricted_request_is_never_recomposed_into_prose(orch):
    resp = orch.ask(UserRequest(text="How do I bypass the low flow trip on the crude charge pump?", session_id="comp-7"))
    assert resp.status in ("restricted", "clarification")
    assert not any(b.id == "answer" for b in resp.blocks)


@pytest.mark.parametrize("text", [
    "What is the normal flow rate of the crude charge pump?",
    "What is the recommended way to start the CDU?",
    "The crude charge pump is operating at 600 m3/h. Is this acceptable?",
    "Trace the crude flow from the crude charge pump to the atmospheric column.",
])
def test_every_answered_request_reads_as_prose_first(orch, text):
    resp = orch.ask(UserRequest(text=text, session_id="comp-prose"))
    if resp.status != "answered":
        pytest.skip(f"{text!r} did not produce an answer on the fixtures")
    opening = resp.answer_markdown.strip().split("\n")[0]
    assert not opening.startswith(("#", "|", "-", "*")), f"the answer opens with a block, not a sentence: {opening[:60]}"
    assert len(opening.split()) >= 8
