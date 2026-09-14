"""Tests for deterministic routing/containment/control relationship rules."""

from knowledge_layer.schemas.knowledge import RelationshipType
from knowledge_layer.rule_relations import extract_rule_relationships
from knowledge_layer.validator import _fuzzy_match

KNOWN = ["Kerosene", "Diesel Header", "Stabilizer off Gas", "FCCU-II", "LPG", "Amine Treating Unit",
         "DSL", "Sour diesel storage tanks", "FO blend", "HFO header", "DHDS", "CDU", "VDU", "RCO",
         "Naphtha Stabilizer section", "Water Wash section", "Crude Distillation Unit",
         "off-site storage tanks", "crude column", "Stabilized Naphtha", "MS tanks", "MEROX",
         "Slop Cut", "Vacuum furnace", "Overhead accumulator"]

ROUTING = """## The products from CDU can be routed as follows:-

Stabilizer off Gas to FCCU-II

LPG to the Amine Treating Unit (ATU)

   Kerosene to

Storage

Diesel Header

MEROX when on ATF regulation

Slop.

  Stabilized Naphtha to

MS tanks

  Slop Cut to

(a) Vacuum furnace along with RCO (As recycle stream)

  DSL to

Sour diesel storage tanks

FO blend to HFO header

To DHDS upstream of 11-E-23

The unit is designed for a turndown capacity of 50%.

The CDU also comprises the Naphtha Stabilizer section and the Water Wash section.

The VDU is designed to process RCO from CDU.

The crude oil is pumped from the off-site storage tanks to the Crude Distillation Unit.

Overheads are routed to the Overhead accumulator.

11-P-01 A/B takes suction from 11-V-02 and discharges to 11-E-05.

The crude column is protected by PSV-1101. The desalter level is controlled by LIC-1102.

11-P-01 A/B is downstream of 11-E-05 and upstream of 11-V-02.
"""


def _triples(rels):
    return {(r.subject, r.predicate, r.object) for r in rels}


def test_routing_rules_extract_expected_triples():
    rels = extract_rule_relationships(ROUTING, "c1", "doc", 18, "INTRO", known_names=KNOWN)
    t = _triples(rels)
    # 1 "X to Y" single line
    assert ("Stabilizer off Gas", RelationshipType.FEEDS, "FCCU-II") in t
    assert ("LPG", RelationshipType.FEEDS, "Amine Treating Unit") in t
    # 2 list-item routing under "X to"
    assert ("Kerosene", RelationshipType.FEEDS, "Diesel Header") in t
    assert ("Kerosene", RelationshipType.FEEDS, "MEROX") in t
    assert ("Stabilized Naphtha", RelationshipType.FEEDS, "MS tanks") in t
    assert ("Slop Cut", RelationshipType.FEEDS, "Vacuum furnace") in t
    assert ("DSL", RelationshipType.FEEDS, "Sour diesel storage tanks") in t
    assert ("FO blend", RelationshipType.FEEDS, "HFO header") in t
    # 3 "To Y upstream of TAG" inside a routing list
    assert ("DSL", RelationshipType.FEEDS, "DHDS") in t
    # 4 comprises
    assert ("Naphtha Stabilizer section", RelationshipType.PART_OF, "CDU") in t
    assert ("Water Wash section", RelationshipType.PART_OF, "CDU") in t
    # 5 designed to process F from S
    assert ("VDU", RelationshipType.RECEIVES_FROM, "CDU") in t
    assert ("VDU", RelationshipType.HAS_FEED, "RCO") in t
    # 6 pumped from A to B
    assert ("off-site storage tanks", RelationshipType.FEEDS, "Crude Distillation Unit") in t
    # 7 is routed to
    assert ("Overheads", RelationshipType.DISCHARGES_TO, "Overhead accumulator") in t
    # 8 takes suction from / discharges to
    assert ("11-P-01 A/B", RelationshipType.SUCTION_FROM, "11-V-02") in t
    assert ("11-P-01 A/B", RelationshipType.DISCHARGES_TO, "11-E-05") in t
    # 9 protected by / controlled by
    assert ("crude column", RelationshipType.PROTECTED_BY, "PSV-1101") in t
    assert ("desalter level", RelationshipType.CONTROLLED_BY, "LIC-1102") in t
    # 10 upstream / downstream of TAG
    assert ("11-P-01 A/B", RelationshipType.DOWNSTREAM_OF, "11-E-05") in t
    assert ("11-P-01 A/B", RelationshipType.UPSTREAM_OF, "11-V-02") in t
    # generic destinations and sentences never become objects
    assert not any(o.lower() in ("storage", "slop") for _, _, o in t)
    assert not any(o.startswith("The unit") or o.startswith("unit is") for _, _, o in t)


def test_gating_partial_and_unknown():
    text = "Foo to Bar\n\nKerosene to Zeta line\n\n"
    rels = extract_rule_relationships(text, "c", "d", 1, known_names=["Kerosene"])
    t = {(r.subject, r.object): r for r in rels}
    assert ("Foo", "Bar") not in t                      # neither side known -> dropped
    assert ("Kerosene", "Zeta line") in t               # one side known -> partial
    assert t[("Kerosene", "Zeta line")].confidence == 0.6
    assert t[("Kerosene", "Zeta line")].predicate_raw.startswith("rule:partial:")
    tagged = extract_rule_relationships("11-P-01 takes suction from 11-V-02.", "c", "d", 1)
    assert tagged and tagged[0].confidence == 0.75      # both tags -> full confidence


def test_rule_evidence_is_verbatim_sentence():
    rels = extract_rule_relationships(ROUTING, "c1", "doc", 18, known_names=KNOWN)
    assert rels
    for r in rels:
        assert r.source == "rule"
        assert r.predicate_raw.startswith("rule:")
        assert _fuzzy_match(r.evidence, ROUTING, 0.8)


def test_context_bridge_is_ignored():
    text = "[Context from previous section:]\nKerosene to Diesel Header\n\n---\n\nNothing here."
    assert extract_rule_relationships(text, "c", "d", 1, known_names=KNOWN) == []
