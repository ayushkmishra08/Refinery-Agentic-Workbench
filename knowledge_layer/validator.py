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

from knowledge_layer.schemas.claims import (
    CLAIM_UNIT_RULES,
    VALID_UNITS,
    ClaimCategory,
    EngineeringClaim,
    UnitFamily,
)
from knowledge_layer.schemas.knowledge import (
    ChunkExtraction,
    ExtractedEntity,
    ExtractedRelationship,
    EntityType,
    RelationshipType,
)
from knowledge_layer.schemas.validation import (
    DocumentValidationSummary,
    ValidationCategory,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from knowledge_layer.chunker import Chunk
from knowledge_layer.config import PipelineConfig
from knowledge_layer.memory import Neo4jMemory
from knowledge_layer.table_context import is_distillation_point, is_numeric_like

logger = logging.getLogger(__name__)

# Words that name a table axis, not a thing in the plant
_GENERIC_SUBJECTS = {
    "specification", "specifications", "value", "values", "consumption", "data", "unit", "units",
    "remarks", "remark", "description", "quantity", "qty", "parameter", "parameters", "temperature",
    "pressure", "range", "limit", "limits", "requirement", "requirements", "condition", "conditions",
    "total", "property", "properties", "details", "particulars", "item", "items", "none", "n a", "na",
    "design", "normal", "minimum", "maximum", "min", "max", "case", "mode", "figure", "table",
}


def is_non_entity_name(name: str) -> bool:
    """True for subjects that are values or table labels rather than entities ('10%', 'IBP', '6.5-8.0')."""
    s = (name or "").strip()
    if not s:
        return True
    if is_numeric_like(s) or is_distillation_point(s):
        return True
    return re.sub(r"[^a-z]+", " ", s.lower()).strip() in _GENERIC_SUBJECTS

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


# Spellings the LLM / OCR commonly produce for the same unit
_UNIT_ALIASES: dict[str, str] = {
    "degc": "°C", "deg c": "°C", "deg. c": "°C", "deg.c": "°C", "ºc": "°C", "℃": "°C", "oc": "°C",
    "° c": "°C", "degree c": "°C", "degrees c": "°C", "degree celsius": "°C", "celsius": "°C",
    "degf": "°F", "deg f": "°F", "ºf": "°F", "℉": "°F", "degree f": "°F", "fahrenheit": "°F",
    "kelvin": "K",
    "kg/cm2g": "kg/cm2", "kg/cm2 g": "kg/cm2", "kg/cm2(g)": "kg/cm2", "kg/cm2a": "kg/cm2", "kg/cm2 a": "kg/cm2",
    "kg/cm²g": "kg/cm²", "kg/cm² g": "kg/cm²", "kg/cm2 (g)": "kg/cm2", "kg/cm2 (a)": "kg/cm2", "kgf/cm2": "kg/cm2",
    "kg/cm²a": "kg/cm²", "kg/cm² a": "kg/cm²", "kg/cm²(g)": "kg/cm²", "kg/cm²(a)": "kg/cm²",
    "kg/sq.cm": "kg/cm2", "kg/sqcm": "kg/cm2", "ksc": "kg/cm2", "kscg": "kg/cm2", "ksca": "kg/cm2",
    "bar g": "barg", "bar(g)": "barg", "bar a": "bara", "bar(a)": "bara",
    "m3/hr": "m3/h", "m³/hr": "m³/h", "cum/hr": "m3/h", "cu.m/hr": "m3/h", "nm3/hr": "Nm3/h",
    "m3h": "m3/h", "m³h": "m³/h", "m3hr": "m3/h", "m³hr": "m³/h", "cum/h": "m3/h",
    "kg/hr": "kg/h", "t/hr": "t/h", "tph": "t/h", "mt/hr": "MT/h", "mtph": "MT/h", "tpd": "t/d",
    "percent": "%", "pct": "%", "wt%": "%", "vol%": "%", "% wt": "%", "% vol": "%",
    "meters": "m", "meter": "m", "metres": "m", "metre": "m", "mtrs": "m", "mtr": "m",
    "deg": "°C",
}

_PRESSURE_BASIS_RE = re.compile(
    r"^(?:kg\s*/\s*cm\s*[2²]|kgf\s*/\s*cm\s*[2²]|ksc|bar|psi|kpa|mpa|atm)\s*[(\s]?\s*(?P<basis>[ag])\s*\)?$", re.I,
)


def pressure_basis(unit_raw: str) -> str:
    """'kg/cm2A' -> 'absolute', 'kg/cm2 g' / 'barg' / 'psig' -> 'gauge', 'kg/cm2' -> ''.

    Absolute and gauge pressures are different physical quantities; the
    canonical unit spelling drops the suffix, so the basis is kept separately
    on the claim and takes part in the conflict context key.
    """
    u = re.sub(r"\s+", " ", (unit_raw or "").strip())
    if not u:
        return ""
    low = u.lower()
    if low in ("barg", "psig", "kscg"):
        return "gauge"
    if low in ("bara", "psia", "ksca"):
        return "absolute"
    m = _PRESSURE_BASIS_RE.match(u)
    if m:
        return "absolute" if m.group("basis").lower() == "a" else "gauge"
    return ""


def normalize_unit(unit: str) -> str:
    """Canonicalise unit spelling (degC -> °C, kg/cm2g -> kg/cm2, ...)."""
    if not unit:
        return ""
    u = re.sub(r"\s+", " ", unit.strip())
    key = u.lower()
    if key in _UNIT_ALIASES:
        return _UNIT_ALIASES[key]
    # Case-insensitive match against the known unit spellings (except 1-letter units)
    for valid_set in VALID_UNITS.values():
        for v in valid_set:
            if len(v) > 1 and v.lower() == key:
                return v
    return u


def _classify_unit(unit: str) -> UnitFamily:
    """Determine which unit family a unit string belongs to."""
    if not unit or unit.strip() == "":
        return UnitFamily.TEXT
    unit_clean = normalize_unit(unit)
    for family, valid_set in VALID_UNITS.items():
        if unit_clean in valid_set:
            return family
    return UnitFamily.UNKNOWN


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.\-/°%]*")


def _norm_text(text: str) -> str:
    text = text.lower().replace(" ", " ")
    text = re.sub(r"[“”\"'`]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _fuzzy_match(needle: str, haystack: str, threshold: float = 0.8) -> bool:
    """Check that an evidence quote is supported by the source chunk.

    1. whitespace/quote-normalised substring match, else
    2. token containment: the fraction of the evidence's tokens (len > 1) that
       occur in the chunk must reach ``threshold``.  This tolerates the LLM
       collapsing table separators or re-punctuating, while still rejecting
       evidence that was invented.
    """
    if not needle or not haystack:
        return False
    n, h = _norm_text(needle), _norm_text(haystack)
    if n in h:
        return True
    n_tokens = [t for t in _TOKEN_RE.findall(n) if len(t) > 1]
    if not n_tokens:
        return False
    h_tokens = set(_TOKEN_RE.findall(h))
    hits = sum(1 for t in n_tokens if t in h_tokens)
    if hits / len(n_tokens) >= threshold:
        return True
    ratio = SequenceMatcher(None, n, h).ratio()
    return ratio >= threshold


# --------------------------------------------------------------------------- #
# Sentence-level grounding
# --------------------------------------------------------------------------- #
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(\[])|\n+")
WEAK_CONFIDENCE_CAP = 0.5


def split_sentences(text: str) -> list[str]:
    """Split chunk text into sentences; table rows / list items are one line each."""
    out: list[str] = []
    for part in _SENT_SPLIT_RE.split(text or ""):
        part = part.strip()
        if len(part) >= 3 and not part.startswith("[Context from previous section"):
            out.append(part)
    return out


def _name_keys(name: str) -> list[str]:
    """Match keys for a name: canonical tag (if any) + normalised text + content tokens."""
    from knowledge_layer.entity_identity import parse_tag
    keys: list[str] = []
    n = _norm_text(name or "")
    if not n:
        return keys
    ident = parse_tag(name)
    if ident:
        keys.append(("tag", ident.canonical))
    keys.append(("text", n))
    return keys


def _name_in_text(name: str, text: str, tokens_threshold: float = 0.8) -> bool:
    """Case-insensitive, tag-normalised, fuzzy containment of a name in a text."""
    from knowledge_layer.entity_identity import find_tags, parse_tag
    if not name or not text:
        return False
    n, t = _norm_text(name), _norm_text(text)
    if n in t:
        return True
    ident = parse_tag(name)
    if ident and ident.canonical.split("/")[-1] in find_tags(text):
        return True
    n_tokens = [x for x in _TOKEN_RE.findall(n) if len(x) > 1]
    if not n_tokens:
        return False
    t_tokens = set(_TOKEN_RE.findall(t))
    hits = sum(1 for x in n_tokens if x in t_tokens)
    return hits / len(n_tokens) >= tokens_threshold


def _value_in_text(value: str, text: str) -> bool:
    """Numeric-aware containment for claim values ("14.0" matches "14.0 kg/cm2g", "14" matches "14.0")."""
    if not value:
        return True
    v = _norm_text(value)
    t = _norm_text(text)
    if v in t:
        return True
    nums = re.findall(r"\d+(?:\.\d+)?", v)
    if nums:
        t_nums = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)}
        try:
            return all(float(x) in t_nums for x in nums)
        except ValueError:
            return False
    return _name_in_text(value, text, 0.8)


def ground_pair(
    subject: str, other: str, chunk_text: str, other_is_value: bool = False,
) -> tuple[str, str | None, int | None]:
    """Locate the sentence that grounds (subject, other).

    Returns (level, sentence, sentence_index) where level is
      "strong" - one sentence/row contains both,
      "weak"   - both appear in the chunk but never together,
      "none"   - subject or other never appears in the chunk.
    """
    sentences = split_sentences(chunk_text)
    other_hit = _value_in_text if other_is_value else _name_in_text
    subj_any = other_any = False
    first_subject_sentence: tuple[int, str] | None = None
    for i, sent in enumerate(sentences):
        s_in = _name_in_text(subject, sent)
        o_in = other_hit(other, sent)
        if s_in and first_subject_sentence is None:
            first_subject_sentence = (i, sent)
        subj_any |= s_in
        other_any |= o_in
        if s_in and o_in:
            return "strong", sent, i
    if not subj_any or not other_any:
        # last resort: whole-chunk containment (names split across line breaks)
        subj_any = subj_any or _name_in_text(subject, chunk_text)
        other_any = other_any or other_hit(other, chunk_text)
        if not subj_any or not other_any:
            return "none", None, None
    if first_subject_sentence:
        return "weak", first_subject_sentence[1], first_subject_sentence[0]
    return "weak", None, None


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
            if not entity_issues:
                had_evidence = bool(entity.evidence)
                evidence_ok = had_evidence and _fuzzy_match(
                    entity.evidence, chunk.text, self.config.validation.evidence_similarity_threshold,
                )
                if not evidence_ok:
                    # Missing or paraphrased quote: accept when the entity itself appears in the
                    # chunk and anchor the evidence to the sentence that names it (recall first;
                    # the graph never carries a quote that is not in the source).
                    level, sent, idx = ground_pair(entity.name, entity.name, chunk.text)
                    if level == "none" and entity.canonical_name:
                        level, sent, idx = ground_pair(entity.canonical_name, entity.canonical_name, chunk.text)
                    if level == "none":
                        entity_issues.append(ValidationIssue(
                            issue_id=str(uuid.uuid4())[:8],
                            category=ValidationCategory.EVIDENCE,
                            severity=ValidationSeverity.ERROR,
                            message=f"Entity '{entity.name}' does not appear in the source chunk"
                                    + ("" if had_evidence else " (and no evidence was given)"),
                            item_type="entity", item_id=entity.entity_id,
                        ))
                    else:
                        entity.evidence = sent or entity.evidence
                        entity.sentence_index = idx
                        issues.append(ValidationIssue(
                            issue_id=str(uuid.uuid4())[:8],
                            category=ValidationCategory.EVIDENCE,
                            severity=ValidationSeverity.WARNING,
                            message=f"Entity '{entity.name}': "
                                    + ("quote paraphrased" if had_evidence else "no quote given")
                                    + "; evidence anchored to the source sentence",
                            item_type="entity", item_id=entity.entity_id,
                        ))
            if entity_issues:
                issues.extend(entity_issues)
                rejected_entities += 1
            else:
                valid_entities += 1

        # Layer 2 + 3: Evidence and unit validation for claims
        for claim in extraction.claims:
            total_checks += 1
            claim_issues = []

            # The subject must be a thing, not a value or a table axis label
            if is_non_entity_name(claim.subject):
                claim_issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4())[:8],
                    category=ValidationCategory.SCHEMA,
                    severity=ValidationSeverity.ERROR,
                    message=f"claim: subject '{claim.subject}' is a value or table label, not an entity",
                    item_type="claim", item_id=claim.claim_id,
                ))

            # Sentence-level grounding (subject + value in one sentence/row)
            claim_issues.extend(self._ground_item(claim, claim.subject, claim.value, chunk.text, "claim",
                                                  claim.claim_id, other_is_value=True))

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

            for side, name in (("subject", rel.subject), ("object", rel.object)):
                if is_non_entity_name(name):
                    rel_issues.append(ValidationIssue(
                        issue_id=str(uuid.uuid4())[:8],
                        category=ValidationCategory.SCHEMA,
                        severity=ValidationSeverity.ERROR,
                        message=f"relationship: {side} '{name}' is a value or table label, not an entity",
                        item_type="relationship", item_id=rel.relationship_id,
                    ))

            # Sentence-level grounding (subject + object in one sentence/row)
            rel_issues.extend(self._ground_item(rel, rel.subject, rel.object, chunk.text, "relationship",
                                                rel.relationship_id))

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
        # A missing quote is not fatal here: validate() grounds the entity by name and
        # anchors evidence to the sentence that names it (rejected only if absent).
        return issues

    def _ground_item(
        self, item, subject: str, other: str, chunk_text: str, item_type: str, item_id: str,
        other_is_value: bool = False,
    ) -> list[ValidationIssue]:
        """Sentence-level grounding for relationships and claims.

        strong: a sentence/row names both -> evidence := that sentence.
        weak:   both appear in the chunk but apart -> WARNING, grounding='weak',
                confidence capped at WEAK_CONFIDENCE_CAP (still inserted).
        none:   subject or object/value absent from the chunk -> ERROR (rejected).
        Missing evidence on the item is tolerated when grounding succeeds.
        """
        # Evidence-first: if the item's own evidence is supported by the chunk and
        # names both sides (routing lists span two lines; table rows carry the value
        # while the subject sits in the header row), it is strongly grounded.
        other_hit = _value_in_text if other_is_value else _name_in_text
        if item.evidence and _fuzzy_match(item.evidence, chunk_text, self.config.validation.evidence_similarity_threshold):
            subj_ok = _name_in_text(subject, item.evidence) or (
                getattr(item, "is_from_table", False) and _name_in_text(subject, chunk_text)
            )
            if subj_ok and other_hit(other, item.evidence):
                item.grounding = "strong"
                return []
        level, sent, idx = ground_pair(subject, other, chunk_text, other_is_value=other_is_value)
        if level == "none":
            missing = subject if not _name_in_text(subject, chunk_text) else other
            return [ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.EVIDENCE,
                severity=ValidationSeverity.ERROR,
                message=f"{item_type}: '{missing}' does not appear in the source chunk",
                item_type=item_type, item_id=item_id,
                details={"subject": subject, "other": str(other)},
            )]
        item.grounding = level
        item.sentence_index = idx
        if level == "strong":
            item.evidence = sent or item.evidence
            return []
        # weak
        if not item.evidence or not _fuzzy_match(item.evidence, chunk_text, 0.6):
            item.evidence = sent or item.evidence
        item.confidence = min(item.confidence, WEAK_CONFIDENCE_CAP)
        return [ValidationIssue(
            issue_id=str(uuid.uuid4())[:8],
            category=ValidationCategory.EVIDENCE,
            severity=ValidationSeverity.WARNING,
            message=f"{item_type}: '{subject}' and '{other}' appear in the chunk but not in one sentence (weak grounding)",
            item_type=item_type, item_id=item_id,
            details={"grounding": "weak"},
        )]

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
            # Source grounding is the core principle: an unsupported quote means
            # the item cannot be traced to the document, so it is rejected.
            return ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.EVIDENCE,
                severity=ValidationSeverity.ERROR,
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

        if actual_family == UnitFamily.TEXT and not (claim.unit or "").strip():
            # A missing unit is not an impossible unit: dimensionless quantities
            # (specific gravity, ratios) legitimately have none; others get a warning.
            if UnitFamily.DIMENSIONLESS in valid_families or UnitFamily.TEXT in valid_families:
                return issues
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4())[:8],
                category=ValidationCategory.NUMERICAL_UNIT,
                severity=ValidationSeverity.WARNING,
                message=f"Missing unit for {claim.predicate.value}",
                item_type="claim",
                item_id=claim.claim_id,
            ))
            return issues

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
                getattr(claim, "qualifier", "") or "",
                context_key=claim.context_key(),
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
