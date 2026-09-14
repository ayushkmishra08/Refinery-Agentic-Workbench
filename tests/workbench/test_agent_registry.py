from workbench.agents.registry import AGENTS, load_agent

EXPECTED = {"task_classifier", "context_resolver", "planner", "procedure", "diagnostic", "calculation",
            "comparison", "safety", "revision_conflict", "report", "verification", "governance"}


def test_all_twelve_agents_registered_and_importable():
    assert set(AGENTS) == EXPECTED
    for key in AGENTS:
        assert load_agent(key).name == key
