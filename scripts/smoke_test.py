"""Smoke test -- quick end-to-end test without external services.

Tests the non-LLM, non-Neo4j pipeline phases with synthetic data.
Run: python scripts/smoke_test.py
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OK = "[OK]"
FAIL = "[FAIL]"
DASH = "--"


def main():
    print("=" * 60)
    print("Refinery Knowledge Layer " + DASH + " Smoke Test")
    print("=" * 60)
    start = time.time()
    errors = 0

    # Test 1: Schema validation
    print("\n[1] Schema imports and validation...")
    try:
        from schemas.parsed_document import ParsedDocument, ParsedPage, ParsedElement, ParsedTable, ParsedTableCell, ElementType
        from schemas.normalized_document import NormalizedDocument, NormalizedElement, ContentType
        from schemas.knowledge import DocumentKnowledge, ExtractedEntity, EntityType, EntityDomain
        from schemas.claims import EngineeringClaim, ClaimCategory, CLAIM_UNIT_RULES
        from schemas.table_schema import ClassifiedTable, TableClassification
        from schemas.document_profile import DocumentProfile, DocumentType
        from schemas.glossary import DocumentGlossary, GlossaryEntry
        from schemas.validation import ValidationResult
        from schemas.ontology import RefineryOntology
        print(f"  {OK} All schemas import and validate")
    except Exception as e:
        print(f"  {FAIL} Schema error: {e}")
        errors += 1

    # Test 2: Create synthetic parsed document
    print("\n[2] Synthetic parsed document...")
    try:
        parsed = ParsedDocument(
            document_id="smoke_test",
            source_filename="smoke_test.pdf",
            source_path="data/raw/smoke_test.pdf",
            source_hash="abc123def456",
            total_pages=3,
            pages=[
                ParsedPage(page_number=1, elements=[
                    ParsedElement(element_id="e1_1", element_type=ElementType.HEADING,
                                  content="OPERATING MANUAL", page=1, position_in_page=0, heading_level=1),
                    ParsedElement(element_id="e1_2", element_type=ElementType.TEXT,
                                  content="Crude Distillation Unit", page=1, position_in_page=1),
                ]),
                ParsedPage(page_number=2, elements=[
                    ParsedElement(element_id="e2_1", element_type=ElementType.HEADING,
                                  content="Abbreviations", page=2, position_in_page=0, heading_level=2),
                    ParsedElement(element_id="e2_2", element_type=ElementType.TABLE,
                                  content="Abbreviation table", page=2, position_in_page=1,
                                  table=ParsedTable(
                                      table_id="t1", page=2, num_rows=3, num_cols=2,
                                      cells=[
                                          ParsedTableCell(row=0, col=0, content="Abbreviation", is_header=True),
                                          ParsedTableCell(row=0, col=1, content="Full Form", is_header=True),
                                          ParsedTableCell(row=1, col=0, content="CDU", is_header=False),
                                          ParsedTableCell(row=1, col=1, content="Crude Distillation Unit", is_header=False),
                                          ParsedTableCell(row=2, col=0, content="ATF", is_header=False),
                                          ParsedTableCell(row=2, col=1, content="Aviation Turbine Fuel", is_header=False),
                                      ],
                                  )),
                ]),
                ParsedPage(page_number=3, elements=[
                    ParsedElement(element_id="e3_1", element_type=ElementType.HEADING,
                                  content="Process Description", page=3, position_in_page=0, heading_level=2),
                    ParsedElement(element_id="e3_2", element_type=ElementType.TEXT,
                                  content="The CDU (Crude Distillation Unit) processes crude oil. "
                                          "Pump P-101 takes suction from crude storage tank T-101. "
                                          "Design pressure of P-101 is 15 barg.",
                                  page=3, position_in_page=1),
                ]),
            ],
            tables=[
                ParsedTable(
                    table_id="t1", page=2, num_rows=3, num_cols=2,
                    cells=[
                        ParsedTableCell(row=0, col=0, content="Abbreviation", is_header=True),
                        ParsedTableCell(row=0, col=1, content="Full Form", is_header=True),
                        ParsedTableCell(row=1, col=0, content="CDU", is_header=False),
                        ParsedTableCell(row=1, col=1, content="Crude Distillation Unit", is_header=False),
                        ParsedTableCell(row=2, col=0, content="ATF", is_header=False),
                        ParsedTableCell(row=2, col=1, content="Aviation Turbine Fuel", is_header=False),
                    ],
                ),
            ],
        )
        print(f"  {OK} Created: {parsed.total_pages} pages, {len(parsed.tables)} tables")
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1
        return errors

    # Test 3: Normalize
    print("\n[3] Normalization...")
    try:
        from src.config import PipelineConfig
        from src.normalizer import DocumentNormalizer
        config = PipelineConfig()
        normalizer = DocumentNormalizer(config)
        normalized = normalizer.normalize(parsed)
        print(f"  {OK} Kept {normalized.stats.kept_elements}/{normalized.stats.total_elements} elements")
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1
        return errors

    # Test 4: Table classification
    print("\n[4] Table classification...")
    try:
        from src.table_classifier import TableClassifier
        classifier = TableClassifier(config)
        table_results = classifier.classify_all(parsed)
        for t in table_results.classified_tables:
            print(f"  {OK} {t.table_id}: {t.classification.value} (confidence: {t.classification_confidence:.2f})")
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1

    # Test 5: Document profile
    print("\n[5] Document profile...")
    try:
        from src.document_profile import DocumentProfiler
        profiler = DocumentProfiler(config)
        profile = profiler.build_profile(normalized, table_results)
        print(f"  {OK} Type: {profile.document_type.value}")
        print(f"  {OK} Abbreviations: {len(profile.abbreviations)}")
        for a in profile.abbreviations:
            print(f"    - {a.abbreviation} = {a.full_form}")
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1

    # Test 6: Glossary
    print("\n[6] Glossary...")
    try:
        from src.glossary_builder import GlossaryBuilder
        builder = GlossaryBuilder(config)
        glossary = builder.build_glossary(normalized, profile)
        print(f"  {OK} Entries: {glossary.total_entries}")
        for entry in glossary.entries:
            print(f"    - {entry.abbreviation}: {entry.full_form} (from {entry.source})")

        # Regression check: ATF must be Aviation Turbine Fuel
        atf = glossary.lookup("ATF")
        if atf and atf.full_form == "Aviation Turbine Fuel":
            print(f"  {OK} REGRESSION: ATF = Aviation Turbine Fuel (correct!)")
        else:
            print(f"  {FAIL} REGRESSION FAILURE: ATF expansion incorrect!")
            errors += 1
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1

    # Test 7: Chunking
    print("\n[7] Chunking...")
    try:
        from src.chunker import DocumentChunker
        chunker = DocumentChunker(config)
        chunks = chunker.chunk(normalized)
        print(f"  {OK} Created {len(chunks)} chunks")
        for c in chunks:
            print(f"    - {c.chunk_id}: pages {c.page_start}-{c.page_end}, ~{c.token_estimate} tokens")
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1

    # Test 8: Validation (unit checks)
    print("\n[8] Validation unit checks...")
    try:
        from src.validator import ExtractionValidator, _classify_unit
        from schemas.claims import UnitFamily

        deg_c = "\u00b0C"  # degree symbol + C
        assert _classify_unit("barg") == UnitFamily.PRESSURE
        assert _classify_unit(deg_c) == UnitFamily.TEMPERATURE
        assert _classify_unit("kg/h") == UnitFamily.MASS_FLOW
        print(f"  {OK} Unit classification working")

        # Test impossible combination rejection
        validator = ExtractionValidator(config)
        from schemas.knowledge import ChunkExtraction

        bad_extraction = ChunkExtraction(
            chunk_id="test", document_id="test", page_start=1, page_end=1,
            claims=[
                EngineeringClaim(
                    claim_id="bad_claim", subject="P-101",
                    predicate=ClaimCategory.MAXIMUM_PRESSURE,
                    value="180", unit=deg_c,
                    evidence="max pressure is 180 " + deg_c,
                    page=1, document_id="test",
                ),
            ],
        )
        from src.chunker import Chunk
        test_chunk = Chunk(
            chunk_id="test", document_id="test", sequence=0,
            page_start=1, page_end=1, section_path="", parent_heading="",
            text="max pressure is 180 " + deg_c,
        )
        result = validator.validate(bad_extraction, test_chunk)
        if result.unit_errors > 0:
            print(f"  {OK} REGRESSION: Pressure claim with degC unit correctly rejected!")
        else:
            print(f"  {FAIL} REGRESSION FAILURE: Should have rejected degC for pressure!")
            errors += 1
    except Exception as e:
        print(f"  {FAIL} Error: {e}")
        errors += 1

    # Summary
    elapsed = time.time() - start
    print("\n" + "=" * 60)
    if errors == 0:
        print(f"ALL SMOKE TESTS PASSED ({elapsed:.1f}s)")
    else:
        print(f"{errors} ERROR(S) FOUND ({elapsed:.1f}s)")
    print("=" * 60)

    return errors


if __name__ == "__main__":
    sys.exit(main())
