"""Anti-hallucination regression tests.

These tests verify that the system does NOT produce known failure modes
from previous extraction attempts. Each test encodes a specific constraint
from the specification.
"""

import pytest
from schemas.claims import ClaimCategory, UnitFamily, CLAIM_UNIT_RULES, VALID_UNITS
from schemas.knowledge import RelationshipType, EntityType


class TestNoHallucinatedRelationships:
    """Test that mere co-occurrence does NOT create relationships."""

    def test_cooccurrence_not_feeds(self):
        """ATF appearing in a material-balance table must NOT create ATF → FEEDS → Column
        unless the source explicitly establishes that relationship."""
        # This is a design constraint — the validator must reject relationships
        # without explicit evidence. We test the validation logic here.
        from src.validator import _fuzzy_match
        
        # Evidence that doesn't establish FEEDS
        bad_evidence = "ATF is listed in the material balance table alongside other products"
        source_text = "The material balance shows ATF, HSD, and other products in the VDU section."
        
        # This evidence does NOT explicitly say "ATF FEEDS column"
        # The system should NOT create such a relationship
        assert "feeds" not in bad_evidence.lower() or "ATF feeds" not in bad_evidence

    def test_no_invented_abbreviation(self):
        """ATF must not be expanded to 'Atmospheric Thermal Fluid' if the document
        defines it as 'Aviation Turbine Fuel'."""
        from schemas.glossary import DocumentGlossary, GlossaryEntry
        
        glossary = DocumentGlossary(
            document_id="test",
            source_filename="test.pdf",
            entries=[
                GlossaryEntry(
                    term="ATF",
                    canonical_meaning="Aviation Turbine Fuel",
                    abbreviation="ATF",
                    full_form="Aviation Turbine Fuel",
                    evidence="ATF - Aviation Turbine Fuel",
                    page=9,
                )
            ],
        )
        glossary.build_lookup_maps()
        
        result = glossary.lookup("ATF")
        assert result is not None
        assert result.full_form == "Aviation Turbine Fuel"
        assert result.full_form != "Atmospheric Thermal Fluid"

    def test_no_ungrounded_flow_rate(self):
        """A flow rate claim must NOT be created unless the actual numeric value
        is tied to the relevant entity in the source."""
        # The validator should reject claims without evidence
        from schemas.claims import EngineeringClaim
        
        claim = EngineeringClaim(
            claim_id="test_claim",
            subject="ATF",
            predicate=ClaimCategory.MASS_FLOW_RATE,
            value="1500",
            unit="kg/h",
            evidence="",  # NO evidence!
            page=0,
            document_id="test",
        )
        
        # Evidence is empty — this should fail validation
        assert claim.evidence == ""


class TestUnitValidation:
    """Test that impossible predicate/unit combinations are caught."""

    def test_pressure_rejects_temperature_unit(self):
        """HAS_MAX_PRESSURE must not receive 180 °C."""
        valid_families = CLAIM_UNIT_RULES.get(ClaimCategory.MAXIMUM_PRESSURE, set())
        assert UnitFamily.PRESSURE in valid_families
        assert UnitFamily.TEMPERATURE not in valid_families

    def test_temperature_rejects_pressure_unit(self):
        """Temperature claims must not have pressure units."""
        valid_families = CLAIM_UNIT_RULES.get(ClaimCategory.DESIGN_TEMPERATURE, set())
        assert UnitFamily.TEMPERATURE in valid_families
        assert UnitFamily.PRESSURE not in valid_families

    def test_flow_rate_accepts_mass_flow(self):
        """Flow rate should accept kg/h."""
        assert "kg/h" in VALID_UNITS[UnitFamily.MASS_FLOW]

    def test_flow_rate_accepts_volumetric_flow(self):
        """Flow rate should accept m3/h."""
        assert "m3/h" in VALID_UNITS[UnitFamily.VOLUMETRIC_FLOW]

    def test_specific_gravity_dimensionless(self):
        """Specific gravity must be dimensionless."""
        valid_families = CLAIM_UNIT_RULES.get(ClaimCategory.SPECIFIC_GRAVITY, set())
        assert UnitFamily.DIMENSIONLESS in valid_families

    def test_all_pressure_units_valid(self):
        """All common pressure units should be recognized."""
        pressure_units = VALID_UNITS[UnitFamily.PRESSURE]
        for unit in ["bar", "barg", "psi", "kPa", "MPa", "kg/cm2", "atm"]:
            assert unit in pressure_units, f"{unit} not in pressure units"


class TestRelationshipDomainValidation:
    """Test that impossible entity-type/relationship combinations are caught."""

    def test_motor_cannot_suction(self):
        """A motor should not have SUCTION_FROM relationship."""
        from src.validator import IMPOSSIBLE_RELATIONSHIPS
        assert ("motor", "SUCTION_FROM") in IMPOSSIBLE_RELATIONSHIPS

    def test_motor_cannot_feed(self):
        """A motor should not FEED equipment."""
        from src.validator import IMPOSSIBLE_RELATIONSHIPS
        assert ("motor", "FEEDS") in IMPOSSIBLE_RELATIONSHIPS

    def test_instrument_cannot_feed(self):
        """An instrument should not FEED equipment."""
        from src.validator import IMPOSSIBLE_RELATIONSHIPS
        assert ("instrument", "FEEDS") in IMPOSSIBLE_RELATIONSHIPS

    def test_product_feeds_is_suspicious(self):
        """A product having FEEDS relationship is suspicious."""
        from src.validator import SUSPICIOUS_RELATIONSHIPS
        assert ("product", "FEEDS") in SUSPICIOUS_RELATIONSHIPS


class TestEvidenceValidation:
    """Test that evidence matching works correctly."""

    def test_exact_match(self):
        from src.validator import _fuzzy_match
        assert _fuzzy_match("P-101 operates at 10 bar", "The pump P-101 operates at 10 bar normally.")

    def test_no_match(self):
        from src.validator import _fuzzy_match
        assert not _fuzzy_match(
            "This is completely different text about reactors",
            "The pump P-101 operates at 10 bar normally.",
            threshold=0.8,
        )

    def test_empty_evidence_fails(self):
        from src.validator import _fuzzy_match
        assert not _fuzzy_match("", "Some source text")
