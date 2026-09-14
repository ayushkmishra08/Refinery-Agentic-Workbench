"""Phase 2 output: the Engineering Context Package handed to planning/execution."""
from __future__ import annotations

from pydantic import BaseModel, Field

from workbench.core.evidence import Evidence


class ContextPackage(BaseModel):
    entities: list[dict] = Field(default_factory=list)          # entity nodes + claims
    relationships: list[dict] = Field(default_factory=list)
    procedures: list[dict] = Field(default_factory=list)        # procedure + ordered steps
    specifications: list[dict] = Field(default_factory=list)    # contextual claims (role/location/mode)
    instrumentation: list[dict] = Field(default_factory=list)
    chunks: list[dict] = Field(default_factory=list)            # retrieval hits with retrieval_text
    conflicts: list[dict] = Field(default_factory=list)         # CONFLICTS_WITH pairs touched
    revisions: list[dict] = Field(default_factory=list)         # authority / revision resolution
    evidence: list[Evidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)               # evidence requirements not met
