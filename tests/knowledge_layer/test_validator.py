"""Tests for the validator module."""

import pytest
from knowledge_layer.schemas.claims import ClaimCategory, EngineeringClaim
from knowledge_layer.schemas.knowledge import (
    ChunkExtraction, ExtractedEntity, ExtractedRelationship,
    EntityType, EntityDomain, RelationshipType,
)
from knowledge_layer.chunker import Chunk
from knowledge_layer.config import PipelineConfig
from knowledge_layer.validator import ExtractionValidator, _fuzzy_match, _classify_unit
from knowledge_layer.schemas.claims import UnitFamily


class TestUnitClassification:
    def test_pressure_units(self):
        assert _classify_unit("bar") == UnitFamily.PRESSURE
        assert _classify_unit("barg") == UnitFamily.PRESSURE
        assert _classify_unit("psi") == UnitFamily.PRESSURE
        assert _classify_unit("kPa") == UnitFamily.PRESSURE

    def test_temperature_units(self):
        assert _classify_unit("°C") == UnitFamily.TEMPERATURE
        assert _classify_unit("°F") == UnitFamily.TEMPERATURE
        assert _classify_unit("K") == UnitFamily.TEMPERATURE

    def test_flow_units(self):
        assert _classify_unit("kg/h") == UnitFamily.MASS_FLOW
        assert _classify_unit("m3/h") == UnitFamily.VOLUMETRIC_FLOW

    def test_unknown_unit(self):
        assert _classify_unit("foobar") == UnitFamily.UNKNOWN

    def test_empty_unit(self):
        assert _classify_unit("") == UnitFamily.TEXT


class TestFuzzyMatch:
    def test_exact_substring(self):
        assert _fuzzy_match("pump P-101", "The pump P-101 is installed")

    def test_no_match(self):
        assert not _fuzzy_match("reactor R-201", "The pump P-101 is installed")

    def test_empty_needle(self):
        assert not _fuzzy_match("", "some text")

    def test_empty_haystack(self):
        assert not _fuzzy_match("test", "")


class TestValidator:
    def setup_method(self):
        self.config = PipelineConfig()
        self.validator = ExtractionValidator(self.config)

    def _make_chunk(self, text: str) -> Chunk:
        return Chunk(
            chunk_id="test_chunk",
            document_id="test_doc",
            sequence=0,
            page_start=1,
            page_end=1,
            section_path="Test Section",
            parent_heading="Test",
            text=text,
        )

    def test_entity_without_name_rejected(self):
        extraction = ChunkExtraction(
            chunk_id="test_chunk", document_id="test_doc",
            page_start=1, page_end=1,
            entities=[
                ExtractedEntity(
                    entity_id="e1", name="",  # EMPTY NAME
                    entity_type=EntityType.PUMP, evidence="some evidence",
                    page=1, confidence=0.9,
                )
            ],
        )
        chunk = self._make_chunk("some evidence about pump")
        result = self.validator.validate(extraction, chunk)
        assert result.rejected_entities > 0

    def test_entity_without_evidence_and_absent_from_chunk_rejected(self):
        extraction = ChunkExtraction(
            chunk_id="test_chunk", document_id="test_doc",
            page_start=1, page_end=1,
            entities=[
                ExtractedEntity(
                    entity_id="e1", name="P-101",
                    entity_type=EntityType.PUMP, evidence="",  # NO EVIDENCE
                    page=1, confidence=0.9,
                )
            ],
        )
        chunk = self._make_chunk("The charge pump operates at 10 bar")   # P-101 never named
        result = self.validator.validate(extraction, chunk)
        assert result.rejected_entities > 0

    def test_entity_without_evidence_but_named_in_chunk_accepted_with_anchor(self):
        entity = ExtractedEntity(
            entity_id="e1", name="P-101",
            entity_type=EntityType.PUMP, evidence="",  # NO EVIDENCE
            page=1, confidence=0.9,
        )
        extraction = ChunkExtraction(
            chunk_id="test_chunk", document_id="test_doc",
            page_start=1, page_end=1, entities=[entity],
        )
        chunk = self._make_chunk("Crude is charged by P-101. P-101 operates at 10 bar.")
        result = self.validator.validate(extraction, chunk)
        assert result.rejected_entities == 0 and result.valid_entities == 1
        assert result.warning_checks >= 1
        assert "P-101" in entity.evidence          # anchored to the sentence naming it

    def test_claim_with_value_like_subject_rejected(self):
        for bad in ("10%", "IBP", "6.5-8.0", "Specifications"):
            extraction = ChunkExtraction(
                chunk_id="test_chunk", document_id="test_doc", page_start=1, page_end=1,
                claims=[EngineeringClaim(
                    claim_id="c1", subject=bad, predicate=ClaimCategory.OPERATING_TEMPERATURE,
                    value="175", unit="°C", evidence=f"{bad} | 213.5 | 175", page=1, document_id="test_doc",
                )],
            )
            chunk = self._make_chunk(f"Property | Basrah | Bombay high\n{bad} | 213.5 | 175")
            result = self.validator.validate(extraction, chunk)
            assert result.rejected_claims == 1, bad

    def test_pressure_with_temperature_unit_rejected(self):
        extraction = ChunkExtraction(
            chunk_id="test_chunk", document_id="test_doc",
            page_start=1, page_end=1,
            claims=[
                EngineeringClaim(
                    claim_id="c1", subject="P-101",
                    predicate=ClaimCategory.MAXIMUM_PRESSURE,
                    value="180", unit="°C",  # WRONG UNIT
                    evidence="max pressure is 180 °C",
                    page=1, document_id="test_doc",
                )
            ],
        )
        chunk = self._make_chunk("max pressure is 180 °C")
        result = self.validator.validate(extraction, chunk)
        assert result.unit_errors > 0

    def test_valid_extraction_passes(self):
        extraction = ChunkExtraction(
            chunk_id="test_chunk", document_id="test_doc",
            page_start=1, page_end=1,
            entities=[
                ExtractedEntity(
                    entity_id="e1", name="P-101",
                    entity_type=EntityType.PUMP,
                    domain=EntityDomain.EQUIPMENT,
                    evidence="P-101 Crude Charge Pump",
                    page=1, confidence=0.9,
                )
            ],
            claims=[
                EngineeringClaim(
                    claim_id="c1", subject="P-101",
                    predicate=ClaimCategory.DESIGN_PRESSURE,
                    value="15", unit="barg",
                    evidence="Design pressure of P-101 is 15 barg",
                    page=1, document_id="test_doc",
                )
            ],
        )
        chunk = self._make_chunk("P-101 Crude Charge Pump. Design pressure of P-101 is 15 barg.")
        result = self.validator.validate(extraction, chunk)
        assert result.passed
        assert result.valid_entities == 1
        assert result.valid_claims == 1
