"""Phase 0 output: the Structured Request (task type + context + entities + safety status)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """Columns of the routing matrix in docs/architecture/agent_workflow.png."""
    LOOKUP = "lookup"
    MULTI_HOP = "multi_hop"
    PROCEDURE = "procedure"
    TROUBLESHOOTING = "troubleshooting"
    LIMITS = "limits"
    SAFETY = "safety"
    COMPARISON = "comparison"
    CONFLICT = "conflict"
    PLANNING = "planning"
    REPORT = "report"
    AMBIGUOUS = "ambiguous"
    CROSS_DOCUMENT = "cross_document"


class SafetyStatus(str, Enum):
    CLEAR = "clear"
    SENSITIVE = "sensitive"        # safety agent must review the answer
    BLOCKED = "blocked"            # policy gate refuses / needs HITL


class ResolvedEntity(BaseModel):
    mention: str
    canonical_tag: str | None = None
    entity_uid: str | None = None
    entity_type: str | None = None
    confidence: float = 0.0


class UserRequest(BaseModel):
    text: str
    session_id: str
    user_role: str = "engineer"


class StructuredRequest(BaseModel):
    original: UserRequest
    task_type: TaskType
    secondary_task_types: list[TaskType] = Field(default_factory=list)
    intent: str = ""
    entities: list[ResolvedEntity] = Field(default_factory=list)
    scenario: str | None = None            # Basrah / BH mode / ...
    operating_mode: str | None = None      # startup / normal_operation / ...
    document_scope: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    safety_status: SafetyStatus = SafetyStatus.CLEAR
    classifier_confidence: float = 0.0
