"""EvidenceStore labelling and revision/authority ranking."""
from __future__ import annotations

from workbench.core.evidence import Evidence
from workbench.core.knowledge import ClaimRecord, DocumentInfo
from workbench.services.evidence_store import EvidenceStore, evidence_from_claim
from workbench.services.revision_resolver import authority_score, explain_preference, rank_claims


def ev(text, claim_id=None, chunk_id=None, page=1):
    return Evidence(document_id="doc", text=text, claim_id=claim_id, chunk_id=chunk_id, page=page)


def test_store_dedupes_by_key_and_labels_in_order_of_first_use():
    s = EvidenceStore()
    k1 = s.add(ev("a", claim_id="c1"))
    k1b = s.add(ev("a (again)", claim_id="c1"))
    k2 = s.add(ev("b", chunk_id="ch1"))
    k3 = s.add(ev("c", chunk_id="ch2"))
    assert k1 == k1b and len(s.all()) == 3
    assert s.label(k2) == "[1]"                 # first labelled wins [1] regardless of insertion order
    assert s.label(k1) == "[2]"
    assert s.label(k2) == "[1]"                 # stable
    assert s.label("nope") is None
    assert [e.text for e in s.labelled()] == ["b", "a"]
    assert s.get(k3) is not None and s.has(k3) and not s.has("zz")


def test_relabel_citations_keeps_existing_labels_and_dedupes():
    s = EvidenceStore()
    k = s.add(ev("x", chunk_id="ch1"))
    assert s.relabel_citations([k, k, "[7]", "missing"]) == ["[1]", "[7]"]
    block = s.to_block()
    assert block.type == "evidence" and block.items[0].ref == "[1]" and block.items[0].chunk_id == "ch1"


def test_evidence_from_claim_maps_provenance():
    c = ClaimRecord(claim_id="c1", subject="pump", predicate="flow_rate", value="482", numeric_value=482.0, unit="m3/h", document_id="doc", page=61, chunk_id="ch", evidence="Normal flow 482 m3/h", source="table", revision="0")
    e = evidence_from_claim(c)
    assert e.claim_id == "c1" and e.page == 61 and e.source == "table" and e.revision == "0" and "482" in e.text


def _c(cid, value, **kw):
    base = dict(claim_id=cid, subject="pump", predicate="flow_rate", value=str(value), numeric_value=float(value), unit="m3/h", document_id="manual", page=10,
                evidence=f"flow {value}", source="table", temporal_status="current", confidence=0.8, context_key="flow_rate|normal|||||")
    base.update(kw)
    return ClaimRecord(**base)


DOCS = {"manual": DocumentInfo(document_id="manual", title="Manual", authority_rank=60, revision="0"),
        "spec": DocumentInfo(document_id="spec", title="Datasheet", authority_rank=80, revision="2"),
        "upload": DocumentInfo(document_id="upload", title="Upload", authority_rank=30)}


def test_rank_prefers_current_over_historical():
    hist = _c("h", 470, temporal_status="historical")
    cur = _c("c", 482, temporal_status="current")
    assert rank_claims([hist, cur], DOCS)[0].claim_id == "c"
    assert "historical" in explain_preference(cur, hist, DOCS)


def test_rank_prefers_higher_document_authority_and_later_revision():
    a = _c("a", 482, document_id="manual", revision="0")
    b = _c("b", 490, document_id="spec", revision="2")
    ranked = rank_claims([a, b], DOCS)
    assert ranked[0].claim_id == "b"
    text = explain_preference(b, a, DOCS)
    assert "authoritative" in text and "revision" in text.lower()
    assert authority_score(b, DOCS) > authority_score(a, DOCS)


def test_rank_prefers_table_over_prose_then_confidence_then_page():
    prose = _c("p", 470, source="prose")
    table = _c("t", 482, source="table")
    assert rank_claims([prose, table], DOCS)[0].claim_id == "t"
    assert "structured data" in explain_preference(table, prose, DOCS)
    low = _c("l", 1, confidence=0.5, page=5)
    high = _c("h", 2, confidence=0.9, page=3)
    assert rank_claims([low, high], DOCS)[0].claim_id == "h"
    late = _c("late", 1, page=200)
    early = _c("early", 2, page=20)
    assert rank_claims([early, late], DOCS)[0].claim_id == "late"
    assert "later page" in explain_preference(late, early, DOCS)


def test_explain_preference_when_nothing_distinguishes():
    a = _c("a", 1)
    b = _c("b", 2)
    assert "neither can be preferred" in explain_preference(a, b, DOCS)
