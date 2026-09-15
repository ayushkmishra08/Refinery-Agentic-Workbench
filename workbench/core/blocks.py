"""Render blocks: the frontend-facing output contract.

Every answer is a list of typed blocks. A frontend renders each block with a dedicated
component (table, ordered steps, gauge, graph, evidence list, ...). Blocks are only
emitted when the content calls for them: a lookup is a KPI row plus evidence, a trace is
a graph, a procedure is a steps block, a limit check is a gauge.

The JSON schema of this file is exported to docs/schema/ by ``python -m workbench schema``.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class _Block(BaseModel):
    id: str = ""
    title: str | None = None
    citations: list[str] = Field(default_factory=list, description="Evidence ref labels ([1], [2]) that support this block")


class TextBlock(_Block):
    type: Literal["text"] = "text"
    markdown: str


class CalloutBlock(_Block):
    type: Literal["callout"] = "callout"
    level: Literal["info", "success", "warning", "danger"] = "info"
    markdown: str


class KpiItem(BaseModel):
    label: str
    value: str
    unit: str | None = None
    qualifier: str | None = None          # normal / minimum / design / discharge ...
    citation: str | None = None


class KpiBlock(_Block):
    type: Literal["kpi"] = "kpi"
    items: list[KpiItem]


class TableBlock(_Block):
    type: Literal["table"] = "table"
    columns: list[str]
    rows: list[list[str | float | int | None]]
    row_citations: list[list[str]] = Field(default_factory=list, description="Per row, evidence refs")
    caption: str | None = None


class StepItem(BaseModel):
    sequence: int
    text: str
    page: int | None = None
    citation: str | None = None
    is_prerequisite: bool = False
    warnings: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list, description="Equipment tags mentioned in the step")


class StepsBlock(_Block):
    type: Literal["steps"] = "steps"
    procedure_id: str | None = None
    procedure_type: str | None = None
    document_id: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    prerequisites: list[StepItem] = Field(default_factory=list)
    steps: list[StepItem]


class GraphNode(BaseModel):
    id: str
    label: str
    type: str | None = None
    is_focus: bool = False
    properties: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    citation: str | None = None
    inferred: bool = False


class GraphBlock(_Block):
    type: Literal["graph"] = "graph"
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    layout: Literal["left-right", "top-down", "radial"] = "left-right"
    mermaid: str | None = Field(default=None, description="Pre-rendered Mermaid source for frontends without a graph library")


class PlanTask(BaseModel):
    id: str
    title: str
    agent: str
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "done", "failed", "skipped"] = "pending"
    summary: str | None = None
    safety_sensitive: bool = False


class PlanBlock(_Block):
    type: Literal["plan"] = "plan"
    goal: str
    tasks: list[PlanTask]
    mermaid: str | None = None


class EvidenceItem(BaseModel):
    ref: str                                   # [1]
    document_id: str
    page: int | None = None
    chunk_id: str | None = None
    claim_id: str | None = None
    section_path: str | None = None
    text: str
    source: str = "graph"                      # table | specification | procedure | narrative | rule | calculator | upload
    revision: str | None = None


class EvidenceBlock(_Block):
    type: Literal["evidence"] = "evidence"
    items: list[EvidenceItem]


class ComparisonCell(BaseModel):
    value: str | None = None
    unit: str | None = None
    citation: str | None = None
    note: str | None = None


class ComparisonBlock(_Block):
    type: Literal["comparison"] = "comparison"
    subjects: list[str]
    attributes: list[str]
    cells: list[list[ComparisonCell]] = Field(description="rows = attributes, cols = subjects")
    differences: list[str] = Field(default_factory=list)


class GaugeMarker(BaseModel):
    label: str                                  # minimum / normal / maximum / design / trip / alarm
    value: float
    citation: str | None = None


class LimitGaugeBlock(_Block):
    type: Literal["limit_gauge"] = "limit_gauge"
    entity: str
    parameter: str
    unit: str
    value: float | None = None
    markers: list[GaugeMarker]
    verdict: Literal["within_normal", "within_design", "outside_design", "unknown"] = "unknown"
    message: str = ""


class ConflictClaim(BaseModel):
    value: str
    unit: str | None = None
    document_id: str
    revision: str | None = None
    page: int | None = None
    source: str
    context: str | None = None                  # role / location / scenario summary
    citation: str | None = None
    temporal_status: str | None = None


class ConflictBlock(_Block):
    type: Literal["conflict"] = "conflict"
    subject: str
    parameter: str
    claims: list[ConflictClaim]
    status: Literal["corroborated", "different_context", "potential_conflict", "resolved", "unresolved"]
    preferred_index: int | None = None
    resolution: str = ""


class SafetyFlagItem(BaseModel):
    severity: Literal["info", "caution", "warning", "danger"]
    message: str
    citation: str | None = None
    requires_authorization: bool = False


class SafetyBlock(_Block):
    type: Literal["safety"] = "safety"
    flags: list[SafetyFlagItem]


class ConfidenceBlock(_Block):
    type: Literal["confidence"] = "confidence"
    score: float = Field(ge=0.0, le=1.0)
    level: Literal["high", "medium", "low"]
    basis: str
    uncertainties: list[str] = Field(default_factory=list)


class ClarificationBlock(_Block):
    type: Literal["clarification"] = "clarification"
    question: str
    missing: list[str]
    options: list[str] = Field(default_factory=list)


class ImageBlock(_Block):
    type: Literal["image"] = "image"
    url: str
    caption: str | None = None
    page: int | None = None


class AuditPhase(BaseModel):
    name: str
    agent: str | None = None
    status: str
    duration_ms: int
    note: str | None = None


class AuditBlock(_Block):
    type: Literal["audit"] = "audit"
    audit_id: str
    phases: list[AuditPhase]
    llm_calls: int = 0
    backend: str = ""


Block = Annotated[
    Union[
        TextBlock, CalloutBlock, KpiBlock, TableBlock, StepsBlock, GraphBlock, PlanBlock,
        EvidenceBlock, ComparisonBlock, LimitGaugeBlock, ConflictBlock, SafetyBlock,
        ConfidenceBlock, ClarificationBlock, ImageBlock, AuditBlock,
    ],
    Field(discriminator="type"),
]
