"""Deterministic validation layer — runs AFTER LLM extraction, BEFORE graph insertion.

Five validation layers:
  1. Schema validation — Pydantic model conformance
  2. Evidence validation — evidence text must appear in source chunk
  3. Numerical/unit validation — impossible predicate/unit combinations rejected
  4. Domain/relationship validation — impossible entity-type/relationship combinations
  5. Contradiction detection — conflicting claims flagged, never silently overwritten
"""

from __future__ import annotations

import logging
import re
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timezone

from schemas.claims import (
    CLAIM_UNIT_RULES,
    VALID_UNITS,
    ClaimCategory,
    EngineeringClaim,
    UnitFamily,
)
from schemas.knowledge import (
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelationship,
    EntityType,
    RelationshipType,
)
from schemas.validation import (
    DocumentValidationSummary,
    ValidationCategory,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from src.chunker import Chunk
from src.config import PipelineConfig
from src.memory import Neo4jMemory

logger = logging.getLogger(__name__)

# Impossible entity-type / relationship-type combinations
IMPOSSIBLE_RELATIONSHIPS: set[tuple[str, str]] = {
    ("motor", "SUCTION_FROM"),
    ("motor", "FEEDS"),
    ("motor", "RECEIVES_FROM"),
    ("instrument", "FEEDS"),
    ("instrument", "SUCTION_FROM"),
    ("instrument", "DISCHARGES_TO"),
    ("valve", "DRIVEN_BY"),
    ("alarm", "FEEDS"),
    ("trip", "FEEDS"),
    ("interlock", "FEEDS"),
    ("chemical", "DRIVEN_BY"),
    ("hazard", "FEEDS"),
    ("procedure", "SUCTION_FROM"),
}

# Suspicious (but not impossible) combinations
SUSPICIOUS_RELATIONSHIPS: set[tuple[str, str]] = {
    ("product", "FEEDS"),
    ("effluent", "FEEDS"),
    ("stream", "DRIVEN_BY"),
}


def _classify_unit(unit: str) -> UnitFamily:
    """Determine which unit family a unit string belongs to."""
    if not unit or unit.strip() == "":
        return UnitFamily.TEXT
    unit_clean = unit.strip()
    for family, valid_set in VALID_UNITS.items():
        if unit_clean in valid_set:
            return family
    return UnitFamily.UNKNOWN


def _fuzzy_match(needle: str, haystack: str, threshold: float = 0.8) -> bool:
    """Check if needle appears in haystack with fuzzy matching."""
    if not needle or not haystack:
        return False
    # First try exact substring
    if needle.lower() in haystack.lower():
        return True
    # Fall back to sequence matching
    ratio = SequenceMatcher(None, needle.lower(), haystack.lower()).ratio()
    return ratio >= threshold


class ExtractionValidator:
    """Validates LLM extraction output before graph insertion."""

    def __init__(self, config: PipelineConfig, memory: Neo4jMemory | None = None):
        self.config = config
        self.memory = memory

    def validate(
        self,
        extraction: ChunkExtraction,
        chunk: Chunk,
    ) -> ValidationResult:
        """Run all validation layers on a chunk extraction.

        Args:
            extraction: LLM extraction output.
            chunk: Original chunk (for evidence checking).

        Returns:
            ValidationResult with pass/fail and all issues found.
        """
        issues: list[ValidationIssue] = []
        total_checks = 0
        valid_entities = 0
        valid_relationships = 0
        valid_claims = 0
        rejected_entities = 0
        rejected_relationships = 0
        rejected_claims = 0

        # Layer 1: Schema validation (already done by Pydantic — check completeness)
        for entity in extraction.entities:
            total_checks += 1
            entity_issues = self._validate_entity_schema(entity)
            if entity_issues:
                issues.extend(entity_issues)
                rejected_entities += 1
            else:
                valid_entities += 1

        # Layer 2 + 3: Evidence and unit validation for claims
        for claim in extraction.claims:
            total_checks += 1
            claim_issues = []

            # Evidence check
            evidence_issue = self._validate_evidence(claim.evidence, chunk.text, "claim", claim.claim_id)
            if evidence_issue:
                claim_issues.append(evidence_issue)

            # Unit validation
            unit_issues = self._validate_claim_units(claim)
            claim_issues.extend(unit_issues)

            if any(i.severity == ValidationSeverity.ERROR for i in claim_issues):
                rejected_claims += 1
            else:
                valid_claims += 1
            issues.extend(claim_issues)

        # Layer 4: Relationship validation
        for rel in extraction.relationships:
            total_checks += 1
            rel_issues = []

            # Evidence check
            evidence_issue = self._validate_evidence(
                rel.evidence, chunk.text, "relationship", rel.relationship_id,
            )
            if evidence_issue:
                rel_issues.append(evidence_issue)

            # Domain checks
            domain_issues = self._validate_relationship_domain(rel)
            rel_issues.extend(domain_issues)

            if any(i.severity == ValidationSeverity.ERROR for i in rel_issues):
                rejected_relationships += 1
            else:
                valid_relationships += 1
            issues.extend(rel_issues)

        # Layer 5: Contradiction detection
        if self.memory:
            for claim in extraction.claims:
                total_checks += 1
                conflict_issues = self._check_contradictions(claim)
                issues.extend(conflict_issues)

        # Compute summary
        errors = sum(1 for i in issues if i.severity == ValidationSeverity.ERROR)
        warnings = sum(1 for i in issues if i.severity == ValidationSeverity.WARNING)
        passed = errors == 0

        result = ValidationResult(
            chunk_id=extraction.chunk_id,
            document_id=extraction.document_id,
            passed=passed,
            total_checks=total_checks,
            passed_checks=total_checks - errors,
            failed_checks=errors,
            warning_checks=warnings,
            issues=issues,
            schema_errors=sum(
                1 for i in issues
                if i.category == ValidationCategory.SCHEMA
                and i.severity == ValidationSeverity.ERROR
            ),
            evidence_errors=sum(
                1 for i in issues
                if i.category == ValidationCategory.EVIDENCE
                and i.severity == ValidationSeverity.ERROR
            ),
            unit_errors=sum(
                1 for i in issues
                if i.category == ValidationCategory.NUMERICAL_UNIT
                and i.severity == ValidationSeverity.ERROR
            ),
            domain_errors=sum(
                1 for i in issues
                if i.category == ValidationCategory.DOMAIN_RELATIONSHIP
                and i.severity == ValidationSeverity.ERROR
            ),
            contradictions=sum(
                1 for i in issues
                if i.category == ValidationCategory.CONTRADICTION
            ),
            valid_entities=valid_entities,
            valid_relationships=valid_relationships,
            valid_claims=valid_claims,
            rejected_entities=rejected_entities,
            rejected_relationships=rejected_relationships,
            rejected_claims=rejected_claims,
            validation_timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if not passed:
            logger.warning(
                f"Validation failed for {extraction.chunk_id}: "
                f"{errors} errors, {warnings} warnings"
            )

        return result

    def _validate_entity_schema(self, entity: ExtractedEntity) -> list[ValidationIssue]:
        """Validate entity completeness."""
        issues = []
        if not entity.name:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.SCHEMA,
                severity=ValidationSeverity.ERROR,
                message="Entity missing name",
                item_type="entity",
                item_id=entity.entity_id,
            ))
        if not entity.evidence:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.EVIDENCE,
                severity=ValidationSeverity.ERROR,
                message=f"Entity '{entity.name}' missing evidence",
                item_type="entity",
                item_id=entity.entity_id,
            ))
        return issues

    def _validate_evidence(
        self, evidence: str, source_text: str, item_type: str, item_id: str,
    ) -> ValidationIssue | None:
        """Check that evidence text appears in the source chunk."""
        if not evidence:
            return ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.EVIDENCE,
                severity=ValidationSeverity.ERROR,
                message=f"Missing evidence for {item_type}",
                item_type=item_type,
                item_id=item_id,
            )

        threshold = self.config.validation.evidence_similarity_threshold
        if not _fuzzy_match(evidence, source_text, threshold):
            return ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.EVIDENCE,
                severity=ValidationSeverity.WARNING,
                message=f"Evidence text not found in source chunk (similarity < {threshold})",
                item_type=item_type,
                item_id=item_id,
                details={"evidence_preview": evidence[:100]},
            )

        return None

    def _validate_claim_units(self, claim: EngineeringClaim) -> list[ValidationIssue]:
        """Validate that claim predicate and unit are compatible."""
        issues = []

        if claim.predicate not in CLAIM_UNIT_RULES:
            return issues  # No rule for this predicate

        valid_families = CLAIM_UNIT_RULES[claim.predicate]
        actual_family = _classify_unit(claim.unit)

        if actual_family == UnitFamily.UNKNOWN and claim.unit:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.NUMERICAL_UNIT,
                severity=ValidationSeverity.WARNING,
                message=f"Unknown unit '{claim.unit}' for {claim.predicate.value}",
                item_type="claim",
                item_id=claim.claim_id,
            ))
        elif actual_family != UnitFamily.UNKNOWN and actual_family not in valid_families:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.NUMERICAL_UNIT,
                severity=ValidationSeverity.ERROR,
                message=(
                    f"Impossible unit '{claim.unit}' ({actual_family.value}) "
                    f"for predicate '{claim.predicate.value}' "
                    f"(expected: {', '.join(f.value for f in valid_families)})"
                ),
                item_type="claim",
                item_id=claim.claim_id,
                details={
                    "value": claim.value,
                    "unit": claim.unit,
                    "predicate": claim.predicate.value,
                },
            ))

        return issues

    def _validate_relationship_domain(
        self, rel: ExtractedRelationship,
    ) -> list[ValidationIssue]:
        """Validate entity-type/relationship-type compatibility."""
        issues = []

        # Check subject type if known
        if rel.subject_type:
            key = (rel.subject_type.value, rel.predicate.value)
            if key in IMPOSSIBLE_RELATIONSHIPS:
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4())[:8],
                    category=ValidationCategory.DOMAIN_RELATIONSHIP,
                    severity=ValidationSeverity.ERROR,
                    message=(
                        f"Impossible: {rel.subject_type.value} cannot have "
                        f"relationship {rel.predicate.value}"
                    ),
                    item_type="relationship",
                    item_id=rel.relationship_id,
                ))
            elif key in SUSPICIOUS_RELATIONSHIPS:
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4())[:8],
                    category=ValidationCategory.DOMAIN_RELATIONSHIP,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Suspicious: {rel.subject_type.value} having "
                        f"relationship {rel.predicate.value} is unusual"
                    ),
                    item_type="relationship",
                    item_id=rel.relationship_id,
                ))

        return issues

    def _check_contradictions(self, claim: EngineeringClaim) -> list[ValidationIssue]:
        """Check for contradicting claims in the graph."""
        issues = []

        if not self.memory or not claim.subject_uid:
            return issues

        try:
            conflicts = self.memory.check_claim_conflict(
                claim.subject_uid,
                claim.predicate.value,
                claim.value,
            )
            for conflict in conflicts:
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4())[:8],
                    category=ValidationCategory.CONTRADICTION,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Contradiction: {claim.subject} {claim.predicate.value} = "
                        f"{claim.value} {claim.unit} conflicts with existing "
                        f"value {conflict.get('value', '?')} {conflict.get('unit', '')} "
                        f"from {conflict.get('document_id', '?')} p.{conflict.get('page', '?')}"
                    ),
                    item_type="claim",
                    item_id=claim.claim_id,
                    conflicting_item_id=conflict.get("uid"),
                    conflicting_document=conflict.get("document_id"),
                ))
        except Exception as e:
            logger.debug(f"Contradiction check error: {e}")

        return issues
