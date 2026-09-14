from pathlib import Path

from workbench.services.backends.mock_backend import MockKnowledgeBackend

FIX = Path(__file__).resolve().parents[2] / "workbench" / "fixtures"


def test_resolve_and_context_scoped_claims():
    kb = MockKnowledgeBackend(FIX)
    ent = kb.resolve_entity("11 PM 01A")
    assert ent and ent[0]["entity_uid"] == "e-11-PM-01"
    normal = kb.entity_claims("e-11-PM-01", {"predicate": "flow_rate", "parameter_role": "normal"})
    assert [c["value"] for c in normal] == [482]
