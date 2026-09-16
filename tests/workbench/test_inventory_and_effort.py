"""Survey questions, out-of-scope handling and the effort levels."""
from __future__ import annotations

import pytest

from workbench.agents.base import REASONING_BLOCKS, blocks_to_markdown
from workbench.agents.task_classifier import classify_by_rules
from workbench.config import EFFORT_LEVELS, load_config
from workbench.core.request import TaskType, UserRequest


# ------------------------------------------------------------------ the entity-listing API


def test_backend_lists_entities_and_counts_them_by_class(mock_knowledge):
    counts = mock_knowledge.entity_type_counts()
    assert counts and all(isinstance(n, int) and n > 0 for n in counts.values())
    assert list(counts.values()) == sorted(counts.values(), reverse=True), "largest class first"
    pumps = mock_knowledge.list_entities(entity_type="Pump")
    assert pumps and all((e.entity_type or "").lower() == "pump" for e in pumps)
    listed = mock_knowledge.list_entities(limit=3)
    assert len(listed) <= 3
    assert all(e.canonical_tag for e in listed), "tagged_only is the default"
    assert [e.mention_count for e in listed] == sorted((e.mention_count for e in listed), reverse=True)


def test_the_class_counts_agree_with_what_the_listing_returns(mock_knowledge):
    """A summary that says 12 heaters above a table of 6 is worse than no summary."""
    for cls, n in mock_knowledge.entity_type_counts().items():
        assert len(mock_knowledge.list_entities(entity_type=cls, limit=2000)) == n, cls


def test_only_plant_numbered_tags_count_as_equipment(mock_knowledge):
    """'D-86' is the ASTM distillation method and 'C-1' a checklist row; neither is plant equipment."""
    listed = mock_knowledge.list_entities(limit=2000)
    assert listed, "the fixtures do contain plant-numbered equipment"
    assert all(len((e.canonical_tag or "").split("-")) >= 3 for e in listed), [e.canonical_tag for e in listed]
    relaxed = mock_knowledge.list_entities(limit=2000, plant_only=False)
    assert len(relaxed) >= len(listed), "the filter can be switched off"


# ------------------------------------------------------------------ classification


@pytest.mark.parametrize("text", [
    "What are all the equipments in the refinery?",
    "List all the pumps",
    "How many columns are there?",
    "What does this manual cover?",
    "Give me an overview of the unit",
    "Show me the equipment list",
    "What can you tell me?",
])
def test_survey_questions_classify_as_inventory(text):
    assert classify_by_rules(text).task_type is TaskType.INVENTORY


@pytest.mark.parametrize("text,expected", [
    ("Which pumps are available for RCO transfer?", TaskType.LOOKUP),
    ("Which documents describe the startup procedure for the atmospheric heater?", TaskType.CROSS_DOCUMENT),
    ("Find all sections that mention crude charge pump changeover.", TaskType.CROSS_DOCUMENT),
    ("Which equipment receives the RCO from the atmospheric column?", TaskType.MULTI_HOP),
])
def test_inventory_rules_do_not_swallow_neighbouring_task_types(text, expected):
    assert classify_by_rules(text).task_type is expected


# ------------------------------------------------------------------ the survey answer


def test_a_survey_question_is_answered_instead_of_bounced_for_clarification(orch):
    resp = orch.ask(UserRequest(text="What are all the equipments in the refinery?", session_id="inv-1"))
    assert resp.task_type is TaskType.INVENTORY
    assert resp.status == "answered", "a scope-wide question has no missing entity to ask about"
    assert any(b.type == "table" for b in resp.blocks)
    md = blocks_to_markdown(resp.blocks)
    assert "Equipment classes on record" in md


def test_a_class_question_lists_only_that_class(orch):
    resp = orch.ask(UserRequest(text="List all the pumps", session_id="inv-2"))
    assert resp.task_type is TaskType.INVENTORY
    assert resp.entities == [], "'pumps' is the scope of the question, not a resolved entity"
    table = next(b for b in resp.blocks if b.type == "table" and b.id == "inventory")
    classes = {row[2] for row in table.rows}
    assert classes == {"Pump"}


# ------------------------------------------------------------------ out of scope


@pytest.mark.parametrize("text", ["hello", "help", "What is the capital of France?", "Write me a poem about pumps", "asdfghjkl"])
def test_requests_the_documents_cannot_serve_get_the_capability_reply(orch, text):
    resp = orch.ask(UserRequest(text=text, session_id=f"oos-{abs(hash(text)) % 999}"))
    md = blocks_to_markdown(resp.blocks)
    assert "I answer engineering questions from the documents" in md
    assert "Try one of these" in md
    assert not any(b.type == "clarification" for b in resp.blocks), "asking which pump they mean would be wrong here"


def test_an_underspecified_but_in_scope_request_still_asks_for_the_entity(orch):
    resp = orch.ask(UserRequest(text="How do I start it?", session_id="amb-1"))
    assert resp.status == "clarification"
    block = next(b for b in resp.blocks if b.type == "clarification")
    assert block.options, "the clarification offers equipment to choose from"


# ------------------------------------------------------------------ effort levels


def test_effort_levels_are_ordered_by_how_much_work_they_allow():
    levels = [EFFORT_LEVELS[n] for n in ("low", "medium", "high", "ultra")]
    for cheaper, richer in zip(levels, levels[1:]):
        assert cheaper.retrieval_k <= richer.retrieval_k
        assert cheaper.graph_hops <= richer.graph_hops
        assert cheaper.procedure_candidates <= richer.procedure_candidates
        assert cheaper.inventory_limit <= richer.inventory_limit


def test_applying_an_effort_level_repoints_retrieval_and_llm_switches():
    low, ultra = load_config("low"), load_config("ultra")
    assert low.effort.name == "low" and ultra.effort.name == "ultra"
    assert low.retrieval.final_k < ultra.retrieval.final_k
    assert low.retrieval.use_vectors is False and ultra.retrieval.use_vectors is True
    assert low.llm.use_llm_for_classification is False and ultra.llm.use_llm_for_classification is True
    assert low.governance.max_replan_iterations <= ultra.governance.max_replan_iterations


def test_an_unknown_effort_name_falls_back_to_medium():
    assert load_config("banana").effort.name == "medium"


def test_the_profile_still_vetoes_a_reranker_it_has_no_model_for():
    cfg = load_config("ultra")
    cfg.retrieval.reranker_model = None
    cfg.apply_effort("ultra")
    assert cfg.retrieval.use_reranker is False


# ------------------------------------------------------------------ CLI answer shape


def test_the_cli_answer_drops_the_reasoning_blocks_but_the_response_keeps_them(orch):
    resp = orch.ask(UserRequest(text="What is the normal flow rate of the crude charge pump?", session_id="clean-1"))
    assert any(b.type in ("confidence", "audit") for b in resp.blocks), "the frontend contract is unchanged"
    cli = blocks_to_markdown(resp.blocks, skip=REASONING_BLOCKS)
    assert "**Confidence:**" not in cli
    assert "Verification notes" not in cli
    assert "_Audit " not in cli
    assert "### Evidence" in cli, "citations stay: they are the answer's support, not its scoring"
