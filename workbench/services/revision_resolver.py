"""Revision & authority resolution for competing claims.

Ranking (highest first): temporal status current > design > historical > defunct; document
authority rank (specification/datasheet > operating manual > upload); later revision; source
type (table/specification > rule/prose); confidence; later page as a weak tiebreaker
(a revised value tends to appear in the equipment section, not the introduction).
When two claims share a context key but differ in value, the preferred claim is stated and the
other is kept as historical evidence — never deleted, never silently averaged.
"""
from __future__ import annotations

import re

from workbench.core.knowledge import ClaimRecord, DocumentInfo

TEMPORAL_RANK = {"current": 3, "design": 2, "": 2, None: 2, "historical": 1, "defunct": 0}
SOURCE_RANK = {"table": 3, "specification": 3, "rule": 2, "prose": 2, "llm_pass1": 1, "llm_pass2": 1}


def _revision_number(rev: str | None) -> float:
    if not rev:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", str(rev))
    return float(m.group(1)) if m else 0.0


def authority_score(c: ClaimRecord, docs: dict[str, DocumentInfo]) -> tuple:
    d = docs.get(c.document_id)
    return (
        TEMPORAL_RANK.get(c.temporal_status, 2),
        d.authority_rank if d else 50,
        _revision_number(c.revision),
        SOURCE_RANK.get(c.source, 1),
        round(c.confidence, 2),
        c.page or 0,
    )


def rank_claims(claims: list[ClaimRecord], docs: dict[str, DocumentInfo]) -> list[ClaimRecord]:
    return sorted(claims, key=lambda c: authority_score(c, docs), reverse=True)


def explain_preference(preferred: ClaimRecord, other: ClaimRecord, docs: dict[str, DocumentInfo]) -> str:
    reasons = []
    dp, do = docs.get(preferred.document_id), docs.get(other.document_id)
    if TEMPORAL_RANK.get(preferred.temporal_status, 2) > TEMPORAL_RANK.get(other.temporal_status, 2):
        reasons.append(f"it is marked {preferred.temporal_status or 'current'} while the other is {other.temporal_status or 'unspecified'}")
    if dp and do and dp.authority_rank != do.authority_rank:
        reasons.append(f"{dp.title or dp.document_id} is the more authoritative document type")
    if _revision_number(preferred.revision) > _revision_number(other.revision):
        reasons.append(f"it belongs to the later revision ({preferred.revision} vs {other.revision})")
    if SOURCE_RANK.get(preferred.source, 1) > SOURCE_RANK.get(other.source, 1):
        reasons.append(f"it comes from a {preferred.source} (structured data) rather than {other.source}")
    if not reasons:
        if (preferred.page or 0) > (other.page or 0):
            reasons.append("both have equal authority; the value stated in the equipment section (later page) is listed first")
        else:
            reasons.append("both have equal authority; neither can be preferred from the documents alone")
    return "Preferred because " + "; ".join(reasons) + ". The other value is kept as evidence."
