"""Evidence and provenance carried by every result. Mirrors the claim/chunk provenance
fields written by the knowledge layer (document_id, page, chunk_id, evidence, source)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    document_id: str
    page: int | None = None
    chunk_id: str | None = None
    claim_id: str | None = None
    text: str
    source: str = "graph"              # graph | vector | table | rule | calculator
    revision: str | None = None
    grounding: float = 1.0


class Citation(BaseModel):
    label: str                          # [1], [2] ...
    evidence: Evidence


class Confidence(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    basis: str = ""                     # why: n independent sources, deterministic calc, ...
    uncertainties: list[str] = Field(default_factory=list)
