"""Test script for the updated OllamaExtractor and Neo4j query."""
import sys
import asyncio
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.extractor import OllamaExtractor
from src.chunker import Chunk
from schemas.document_profile import DocumentProfile, DocumentType
from src.retriever import RetrievalContext
from src.memory import Neo4jMemory

def test_extractor():
    print("Testing Extractor...")
    config = load_config()
    
    extractor = OllamaExtractor(config)
    if not extractor.check_model_available():
        print("Model not available. Skipping extraction test.")
        return

    # Test 1: Equipment Description
    chunk1 = Chunk(
        chunk_id="test_chunk_eq",
        document_id="CDU operating manual",
        page_start=15,
        page_end=15,
        text="3.1 Crude Feed Pump P-101A/B\nThe crude feed pump P-101A supplies crude oil from the storage tanks to the pre-heat train. It has a discharge pressure of 15 kg/cm2. In case of failure, standby pump P-101B auto-starts.",
        section_path="3.1 Crude Feed Pump",
        sequence=1,
        parent_heading="3. Equipment Description"
    )

    # Test 2: Safety Instruction
    chunk2 = Chunk(
        chunk_id="test_chunk_safety",
        document_id="CDU operating manual",
        page_start=45,
        page_end=45,
        text="WARNING: H2S gas may be present near the desalter drain. Operators must wear full face SCBA before opening valve V-205. Maximum allowable H2S concentration is 10 ppm.",
        section_path="5.2 Safety Precautions",
        sequence=2,
        parent_heading="5. Safety"
    )

    # Test 3: Table Data
    chunk3 = Chunk(
        chunk_id="test_chunk_table",
        document_id="CDU operating manual",
        page_start=60,
        page_end=60,
        text="Operating Limits:\nParameter | Normal | Maximum\nFurnace Inlet Temp | 240 C | 260 C\nColumn Top Pressure | 1.2 kg/cm2 | 1.5 kg/cm2",
        section_path="8.1 Operating Limits",
        sequence=3,
        parent_heading="8. Operations"
    )

    chunks = [chunk1, chunk2, chunk3]
    
    profile = DocumentProfile(
        document_id="CDU operating manual",
        source_filename="CDU operating manual.pdf",
        document_type=DocumentType.OPERATING_MANUAL,
        title="CDU II Operating Manual"
    )

    try:
        for i, c in enumerate(chunks):
            print(f"\n--- Testing Chunk {i+1}: {c.section_path} ---")
            context = RetrievalContext()
            extraction = extractor.extract_chunk(c, context, profile)
            
            print(f"Entities: {len(extraction.entities)}")
            for e in extraction.entities:
                print(f"  - {e.name} ({e.entity_type})")
                
            print(f"Relationships: {len(extraction.relationships)}")
            for r in extraction.relationships:
                print(f"  - {r.subject} -> {r.predicate} -> {r.object}")
                
            print(f"Claims: {len(extraction.claims)}")
            for cl in extraction.claims:
                print(f"  - {cl.subject} -> {cl.predicate} -> {cl.value} {cl.unit}")
                
    finally:
        extractor.close()

def test_neo4j():
    print("\nTesting Neo4j get_procedures...")
    config = load_config()
    memory = Neo4jMemory(config)
    try:
        memory.connect()
        # Test the updated get_procedures query
        procedures = memory.get_procedures("Startup procedure for CDU")
        print(f"Found procedures: {len(procedures)}")
        for p in procedures:
            print(f"  - {p}")
    except Exception as e:
        print(f"Neo4j test failed: {e}")
    finally:
        memory.close()

if __name__ == "__main__":
    test_extractor()
    test_neo4j()
