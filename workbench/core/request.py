"""Phase 0/1 contract: UserRequest -> StructuredRequest.

TaskType values are the columns of the routing matrix (docs/architecture/agent_workflow.png)
plus three the benchmark set needs: explanation ("why" questions), provenance
("show me the evidence / which revision") and scenario comparison is folded into comparison.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    LOOKUP = "lookup"                    # entity property retrieval
    MULTI_HOP = "multi_hop"              # trace flow, upstream/downstream, instruments-of
    PROCEDURE = "procedure"              # ordered steps, prerequisites, checks
    TROUBLESHOOTING = "troubleshooting"  # symptom -> causes -> checks -> corrective actions
    LIMITS = "limits"                    # operating envelope, value acceptability
    EXPLANATION = "explanation"          # why questions
    SAFETY = "safety"                    # precautions, isolation, PPE, interlocks, bypass requests
    COMPARISON = "comparison"            # entities / scenarios / modes / procedures / revisions
    CONFLICT = "conflict"                # differing values, which to trust
    PROVENANCE = "provenance"            # evidence for a value, revision, source type
    PLANNING = "planning"                # work plan / checklist / investigation plan
    REPORT = "report"                    # engineering report generation
    CROSS_DOCUMENT = "cross_document"    # which documents / sections / referenced procedure / standing instructions
    AMBIGUOUS = "ambiguous"              # missing entity / parameter / unit -> clarification


class SafetyStatus(str, Enum):
    CLEAR = "clear"
    SENSITIVE = "sensitive"      # safety agent reviews the answer and adds precautions
    RESTRICTED = "restricted"    # bypass / defeat protection: answer only with documented authorization path + HITL


class Attachment(BaseModel):
    name: str
    path: str
    media_type: str = "application/pdf"


class UserRequest(BaseModel):
    text: str
    session_id: str = "default"
    user_role: str = "engineer"
    attachments: list[Attachment] = Field(default_factory=list)
    options: dict = Field(default_factory=dict, description="Frontend hints: {'want_report': true, 'max_steps': 12}")


class ResolvedEntity(BaseModel):
    mention: str
    entity_uid: str | None = None
    canonical_tag: str | None = None
    name: str | None = None
    entity_type: str | None = None
    confidence: float = 0.0
    method: str = ""                  # tag | alias | name | glossary | session | llm
    candidates: list[str] = Field(default_factory=list, description="Other plausible entities when ambiguous")


class QuantityMention(BaseModel):
    raw: str
    value: float
    unit: str | None = None
    parameter: str | None = None      # flow_rate / pressure / temperature ... when inferable


class Symptom(BaseModel):
    variable: str                     # discharge pressure / column pressure / flow
    direction: str                    # low | high | dropping | rising | fluctuating | no_flow | poor_performance | trip
    entity_mention: str | None = None
    raw: str = ""


class ClassifierOutput(BaseModel):
    task_type: TaskType
    secondary: list[TaskType] = Field(default_factory=list)
    intent: str = ""
    confidence: float = 0.0
    method: str = "rules"             # rules | llm | rules+llm
    signals: list[str] = Field(default_factory=list)


class StructuredRequest(BaseModel):
    original: UserRequest
    task_type: TaskType
    secondary_task_types: list[TaskType] = Field(default_factory=list)
    intent: str = ""
    entities: list[ResolvedEntity] = Field(default_factory=list)
    unresolved_mentions: list[str] = Field(default_factory=list)
    action: str | None = None         # startup | shutdown | changeover | isolation | restart | inspection | maintenance
    parameter: str | None = None      # flow_rate | pressure | temperature | level | ...
    quantities: list[QuantityMention] = Field(default_factory=list)
    symptom: Symptom | None = None
    scenario: str | None = None       # Basrah | Bombay High | BH mode | PG mode | ...
    operating_mode: str | None = None # normal_operation | startup | shutdown | emergency | temporary
    document_scope: list[str] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    safety_status: SafetyStatus = SafetyStatus.CLEAR
    safety_signals: list[str] = Field(default_factory=list)
    classifier: ClassifierOutput | None = None
    resolved_from_session: bool = False

    @property
    def primary_entity(self) -> ResolvedEntity | None:
        return next((e for e in self.entities if e.entity_uid), None) or (self.entities[0] if self.entities else None)

    def entity_uids(self) -> list[str]:
        return [e.entity_uid for e in self.entities if e.entity_uid]
