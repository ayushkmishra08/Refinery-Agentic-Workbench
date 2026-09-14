"""Uniform agent output and the final response contract (Final Output box)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from workbench.core.evidence import Citation, Confidence, Evidence


class SafetyFlag(BaseModel):
    severity: str                # info | caution | warning | danger
    message: str
    evidence: list[Evidence] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent: str
    ok: bool = True
    content: dict = Field(default_factory=dict)          # agent-specific structured payload
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=lambda: Confidence(score=0.0))
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    needs_replan: bool = False
    replan_reason: str | None = None
    trace: list[str] = Field(default_factory=list)       # audit lines


class FinalResponse(BaseModel):
    answer: str
    steps: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    provenance: list[Evidence] = Field(default_factory=list)
    audit_trail_id: str
    requires_human_review: bool = False
