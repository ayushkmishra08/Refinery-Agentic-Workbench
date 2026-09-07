"""Tests for the glossary builder."""

import pytest
from schemas.document_profile import DocumentProfile, AbbreviationEntry
from schemas.normalized_document import NormalizedDocument, NormalizedElement, ContentType
from src.config import PipelineConfig
from src.glossary_builder import GlossaryBuilder


class TestGlossaryBuilder:
    def setup_method(self):
        self.config = PipelineConfig()
        self.builder = GlossaryBuilder(self.config)

    def test_inline_abbreviation_detected(self):
        """Pattern: CDU (Crude Distillation Unit)"""
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=[
                NormalizedElement(
                    element_id="e1", content_type=ContentType.PARAGRAPH,
                    content="The CDU (Crude Distillation Unit) is the primary unit.",
                    page=5, position_in_page=0,
                ),
            ],
        )
        profile = DocumentProfile(document_id="test", source_filename="test.pdf")
        glossary = self.builder.build_glossary(doc, profile)
        
        entry = glossary.lookup("CDU")
        assert entry is not None
        assert entry.full_form == "Crude Distillation Unit"
        assert entry.source == "inline_definition"

    def test_reverse_inline_detected(self):
        """Pattern: Crude Distillation Unit (CDU)"""
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=[
                NormalizedElement(
                    element_id="e1", content_type=ContentType.PARAGRAPH,
                    content="The Vacuum Distillation Unit (VDU) processes atmospheric residue.",
                    page=5, position_in_page=0,
                ),
            ],
        )
        profile = DocumentProfile(document_id="test", source_filename="test.pdf")
        glossary = self.builder.build_glossary(doc, profile)
        
        entry = glossary.lookup("VDU")
        assert entry is not None
        assert entry.full_form == "Vacuum Distillation Unit"

    def test_abbreviation_table_takes_priority(self):
        """Abbreviation table entries should be included with high confidence."""
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=[],
        )
        profile = DocumentProfile(
            document_id="test", source_filename="test.pdf",
            abbreviations=[
                AbbreviationEntry(
                    abbreviation="ATF",
                    full_form="Aviation Turbine Fuel",
                    evidence="ATF = Aviation Turbine Fuel",
                    page=9,
                    source="abbreviation_table",
                ),
            ],
        )
        glossary = self.builder.build_glossary(doc, profile)
        
        entry = glossary.lookup("ATF")
        assert entry is not None
        assert entry.full_form == "Aviation Turbine Fuel"
        assert entry.confidence == 0.95

    def test_lookup_maps_built(self):
        """Lookup maps should be populated after building."""
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=[],
        )
        profile = DocumentProfile(
            document_id="test", source_filename="test.pdf",
            abbreviations=[
                AbbreviationEntry(
                    abbreviation="CDU", full_form="Crude Distillation Unit",
                    evidence="CDU = Crude Distillation Unit", page=9,
                ),
            ],
        )
        glossary = self.builder.build_glossary(doc, profile)
        
        assert "CDU" in glossary.abbreviation_map
        assert glossary.abbreviation_map["CDU"] == "Crude Distillation Unit"
