"""Evidence and provenance carried by every result.

Mirrors the provenance the knowledge layer writes on claims, relationships and chunks
(document_id, page, chunk_id, evidence text, source). Every statement an agent makes
must point at one or more Evidence objects; the Verification agent enforces it.
"""
from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    document_id: str
    text: str
    page: int | None = None
    chunk_id: str | None = None
    claim_id: str | None = None
    section_path: str | None = None
    source: str = "graph"           # table | specification | procedure | narrative | rule | calculator | upload | graph
    revision: str | None = None
    grounding: float = 1.0          # 1.0 = verbatim from the knowledge layer; < 1 when paraphrased/derived
    ref: str | None = None          # citation label assigned by the EvidenceStore, e.g. "[3]"

    def key(self) -> str:
        """Stable identity used for de-duplication across agents."""
        basis = self.claim_id or self.chunk_id or f"{self.document_id}:{self.page}:{self.text[:80]}"
        return hashlib.md5(basis.encode("utf-8")).hexdigest()[:12]


class Confidence(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    basis: str = ""
    uncertainties: list[str] = Field(default_factory=list)

    @property
    def level(self) -> str:
        return "high" if self.score >= 0.75 else "medium" if self.score >= 0.45 else "low"


class SafetyFlag(BaseModel):
    severity: str                   # info | caution | warning | danger
    message: str
    evidence: list[Evidence] = Field(default_factory=list)
    requires_authorization: bool = False
    source_agent: str = "safety"
