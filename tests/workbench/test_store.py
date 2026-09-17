"""IndexStore over the mock fixtures (keyword retrieval only)."""
from __future__ import annotations

from workbench.services.backends.mock_backend import MockKnowledgeBackend


def store(cfg) -> MockKnowledgeBackend:
    return MockKnowledgeBackend(cfg.paths.fixtures_dir)


def test_documents_and_stats(cfg):
    s = store(cfg)
    docs = s.documents()
    assert docs[0].document_id == "CDU operating manual" and docs[0].revision == "0"
    st = s.stats()
    # three fixture documents now: the CDU manual plus the desalter and API 610 tiers that the
    # access-control tests need. The CDU figures are the first three of each count.
    assert st["documents"] == 3 and st["entities"] == 12 and st["claims"] == 19 and st["procedures"] == 4 and st["chunks"] == 10 and st["vectors"] == 0


def test_resolve_entity_exact_alias_prefers_tagged(cfg):
    s = store(cfg)
    hits = s.resolve_entity("crude charge pump")
    assert hits and hits[0].entity_uid == "e-11-PM-01"
    assert s.resolve_entity("CRUDE CHARGE PUMPS")[0].entity_uid == "e-11-PM-01"      # plural / case
    assert s.resolve_entity("Desalter")[0].entity_uid == "e-11-V-02"


def test_resolve_entity_child_and_parent_tags(cfg):
    s = store(cfg)
    assert s.resolve_entity("11-PM-01A")[0].entity_uid == "e-11-PM-01"
    assert s.resolve_entity("11-PM-01")[0].entity_uid == "e-11-PM-01"
    assert s.resolve_entity("11 PM 01A")[0].entity_uid == "e-11-PM-01"             # spaced, fuzzy tokens
    assert s.resolve_entity("PM-01")[0].entity_uid == "e-11-PM-01"                 # no plant prefix -> suffix match
    assert s.resolve_entity("") == []


def test_resolve_entity_fuzzy_name(cfg):
    s = store(cfg)
    hits = s.resolve_entity("atmos distillation column")
    assert hits and hits[0].entity_uid == "e-11-C-01"
    assert s.resolve_entity("zzz unknown gadget") == []


def test_search_entities_by_type(cfg):
    s = store(cfg)
    pumps = s.search_entities("pump", entity_type="Pump")
    assert "e-11-PM-01" in [e.entity_uid for e in pumps]
    assert s.get_entity("e-11-F-01").canonical_tag == "11-F-01"
    assert s.get_entity("nope") is None


def test_entity_claims_with_predicate_and_context_filter(cfg):
    s = store(cfg)
    all_flow = s.entity_claims("e-11-PM-01", predicate="flow_rate")
    assert {c.claim_id for c in all_flow} == {"c1", "c2", "c3", "c7"}
    normal = s.entity_claims("e-11-PM-01", predicate="flow_rate", context={"parameter_role": "normal"})
    assert {c.claim_id for c in normal} == {"c1", "c7"}
    disch = s.entity_claims("e-11-PM-01", predicate="pressure", context={"location": "discharge"})
    assert [c.claim_id for c in disch] == ["c4"] and disch[0].pressure_basis == "absolute"


def test_conflicts_for_detects_same_context_different_value(cfg):
    s = store(cfg)
    conflicts = s.conflicts_for("e-11-PM-01", predicate="flow_rate")
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.status == "potential_conflict" and {x.claim_id for x in c.claims} == {"c1", "c7"}
    assert s.conflicts_for("e-11-C-01") == []                                        # single values only


def test_search_claims_by_subject_and_text(cfg):
    s = store(cfg)
    assert {c.claim_id for c in s.search_claims(subject="atmospheric column", predicate="pressure")} == {"c9", "c10"}
    assert any(c.claim_id == "c6" for c in s.search_claims(text="design pressure 31.7"))


def test_entity_neighbors_and_two_hop(cfg):
    s = store(cfg)
    one = s.entity_neighbors("e-11-PM-01")
    assert {r.rel_type for r in one} == {"FEEDS", "MONITORS", "CONTROLS"}
    out_only = s.entity_neighbors("e-11-PM-01", rel_types=["FEEDS"], direction="out")
    assert [r.target_uid for r in out_only] == ["e-11-E-01"]
    two = s.entity_neighbors("e-11-PM-01", rel_types=["FEEDS"], hops=2)
    assert {r.target_uid for r in two} >= {"e-11-E-01", "e-11-V-02"}


def test_procedures_by_entity_type_and_query(cfg):
    s = store(cfg)
    by_entity = s.procedures(entity_uid="e-11-PM-01")
    assert {p.procedure_id for p in by_entity} == {"proc-changeover-01", "proc-pump-isolation"}
    assert by_entity[0].score > 0
    typed = s.procedures(procedure_type="startup")
    assert typed and "proc-heater-lightoff" in [p.procedure_id for p in typed]
    q = s.procedures(entity_uid="e-11-PM-01", query="change over to the standby pump")
    assert q[0].procedure_id == "proc-changeover-01"
    assert s.get_procedure("proc-heater-lightoff").steps[0].tags == ["11-F-01"]
    assert s.get_procedure("nope") is None


def test_search_chunks_keyword_with_filters_and_entity_anchor(cfg):
    s = store(cfg)
    hits = s.search_chunks("pump losing suction discharge pressure dropping", k=3)
    assert hits and hits[0].chunk_id == "ch-upset" and "keyword" in hits[0].match_reason
    only_safety = s.search_chunks("pump", k=3, chunk_types=["safety"])
    assert [c.chunk_id for c in only_safety] == ["ch-safety"]
    anchored = s.search_chunks("controls flow", k=4, entity_uids=["e-11-FRC-101"])
    assert anchored and "entity" in anchored[0].match_reason
    assert s.get_chunk("ch-feed").chapter_number == 6
    assert [c.chunk_id for c in s.chunks_for_entity("e-11-FRC-101")] == ["ch-control"]


def test_sections_glossary_and_structure_lists_are_empty_but_safe(cfg):
    s = store(cfg)
    assert s.sections("feed") == [] and s.glossary("ATF") == []
    assert s.document_references() == [] and s.standing_instructions("bypass") == [] and s.cross_references() == []
    assert s.chapters() == []
