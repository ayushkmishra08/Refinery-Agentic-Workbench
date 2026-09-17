"""Uniform agent output (AgentResult) and the final response contract (FinalResponse).

AgentResult.blocks are the render blocks an agent contributes. AgentResult.evidence is
the evidence those blocks cite (by the block's local citation labels, which the
EvidenceStore re-labels globally at governance time). AgentResult.content holds the
structured payload other agents may consume (e.g. the Procedure agent's steps feed the
Safety agent; the Calculation agent's verdict feeds the Report agent).
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from workbench.core.blocks import Block
from workbench.core.evidence import Confidence, Evidence, SafetyFlag
from workbench.core.plan import Plan
from workbench.core.request import TaskType


class Statement(BaseModel):
    """A checkable assertion: text plus the evidence keys that support it."""
    text: str
    evidence_keys: list[str] = Field(default_factory=list)
    numbers: list[str] = Field(default_factory=list)   # numeric tokens found in the text (verification checks they appear in evidence)
    kind: str = "fact"                                  # fact | inference | topology | calculation


class AgentResult(BaseModel):
    agent: str
    step_id: str = ""
    ok: bool = True
    summary: str = ""                                    # one-line, for the plan block / audit
    content: dict = Field(default_factory=dict)
    blocks: list[Block] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    statements: list[Statement] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=lambda: Confidence(score=0.0))
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    needs_replan: bool = False
    replan_reason: str | None = None
    missing: list[str] = Field(default_factory=list)   # evidence requirements not satisfied
    llm_calls: int = 0
    duration_ms: int = 0
    trace: list[str] = Field(default_factory=list)

    def add_evidence(self, ev: Evidence) -> str:
        """Register evidence and return its key (used as a local citation label)."""
        k = ev.key()
        if not any(e.key() == k for e in self.evidence):
            self.evidence.append(ev)
        return k


class SecurityEnvelope(BaseModel):
    """What this answer was built from, who it went to, and what was kept back.

    The same facts the classification footer and the escalation note state in prose, as fields a
    frontend can render as a banner, a chip and a button. Populated by the orchestrator on every
    response while access control is on; a client that ignores it still sees everything in
    ``answer_markdown``, which is why the prose is not removed when this is filled in.
    """
    access_control: bool = False              # False when the deployment runs with security off
    principal: str = "guest"
    role: str = "guest"
    authenticated: bool = False
    classification: str | None = None         # highest tag among the documents the answer used
    source_documents: list[str] = Field(default_factory=list)
    readable_documents: list[str] = Field(default_factory=list)
    withheld_documents: list[str] = Field(default_factory=list, description="Documents that bear on the question but sit above this role")
    withheld_summary: str = ""
    withheld_records: int = 0
    escalation_target: str | None = None      # the lowest role that can release what was withheld
    access_request_id: str | None = None      # raised automatically when restricted material bears on the question
    attached_documents: list[str] = Field(
        default_factory=list,
        description="Documents uploaded into this conversation. Readable by the person who "
                    "uploaded them and by nobody else; never part of the knowledge layer.",
    )
    grant_id: str | None = None
    access_key_error: str | None = Field(
        default=None,
        description="Why a supplied access key was refused. The run still happens at the caller's "
                    "own role, but they are told the key did not apply rather than left to read a "
                    "thin answer and guess why.",
    )               # set when the answer was released under an approved key
    released_records: int = 0
    release_blocked: bool = False             # the finished answer failed the provenance re-check


class FinalResponse(BaseModel):
    response_id: str
    session_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_type: TaskType
    secondary_task_types: list[TaskType] = Field(default_factory=list)
    status: str = "answered"            # answered | clarification | restricted | failed | needs_review
    answer_markdown: str = ""           # plain-text fallback for clients that cannot render blocks
    blocks: list[Block] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: Confidence
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    requires_human_review: bool = False
    review_reason: str | None = None
    plan: Plan | None = None
    audit_trail_id: str
    entities: list[str] = Field(default_factory=list)
    timing_ms: int = 0
    llm_calls: int = 0
    backend: str = ""
    warnings: list[str] = Field(default_factory=list)
    security: SecurityEnvelope = Field(default_factory=SecurityEnvelope)
