"""Pydantic schema for validation results.

The validation layer runs deterministic checks on LLM extraction output:
schema validation, evidence validation, numerical/unit validation,
domain/relationship validation, and contradiction detection.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ValidationSeverity(str, Enum):
    """Severity of a validation issue."""
    ERROR = "error"       # Must be rejected
    WARNING = "warning"   # Suspicious, flagged for review
    INFO = "info"         # Informational note


class ValidationCategory(str, Enum):
    """Category of validation check."""
    SCHEMA = "schema"
    EVIDENCE = "evidence"
    NUMERICAL_UNIT = "numerical_unit"
    DOMAIN_RELATIONSHIP = "domain_relationship"
    CONTRADICTION = "contradiction"
    CONSISTENCY = "consistency"


class ValidationIssue(BaseModel):
    """A single validation issue found."""
    issue_id: str
    category: ValidationCategory
    severity: ValidationSeverity
    message: str = Field(description="Human-readable description of the issue")
    item_type: str = Field(description="What was being validated: 'entity', 'relationship', 'claim'")
    item_id: str = Field(default="", description="ID of the item with the issue")
    details: dict[str, str] = Field(
        default_factory=dict,
        description="Additional details about the issue",
    )

    # For contradiction issues
    conflicting_item_id: str | None = Field(default=None)
    conflicting_document: str | None = Field(default=None)


class ValidationResult(BaseModel):
    """Complete validation result for a chunk extraction."""
    chunk_id: str
    document_id: str

    # Outcome
    passed: bool = Field(description="True if no ERROR-level issues were found")
    total_checks: int = Field(default=0)
    passed_checks: int = Field(default=0)
    failed_checks: int = Field(default=0)
    warning_checks: int = Field(default=0)

    # Issues found
    issues: list[ValidationIssue] = Field(default_factory=list)

    # Counts by category
    schema_errors: int = Field(default=0)
    evidence_errors: int = Field(default=0)
    unit_errors: int = Field(default=0)
    domain_errors: int = Field(default=0)
    contradictions: int = Field(default=0)

    # Items that passed validation
    valid_entities: int = Field(default=0)
    valid_relationships: int = Field(default=0)
    valid_claims: int = Field(default=0)

    # Items rejected
    rejected_entities: int = Field(default=0)
    rejected_relationships: int = Field(default=0)
    rejected_claims: int = Field(default=0)

    validation_timestamp: str = Field(default="")
    validation_duration_seconds: float = Field(default=0.0)


class DocumentValidationSummary(BaseModel):
    """Aggregated validation results for a whole document."""
    document_id: str
    total_chunks_validated: int = 0
    chunks_passed: int = 0
    chunks_with_errors: int = 0
    chunks_with_warnings: int = 0

    total_issues: int = 0
    total_errors: int = 0
    total_warnings: int = 0
    total_contradictions: int = 0

    # Aggregated valid counts
    total_valid_entities: int = 0
    total_valid_relationships: int = 0
    total_valid_claims: int = 0

    # Aggregated rejection counts
    total_rejected_entities: int = 0
    total_rejected_relationships: int = 0
    total_rejected_claims: int = 0

    # All issues for review
    all_issues: list[ValidationIssue] = Field(default_factory=list)
