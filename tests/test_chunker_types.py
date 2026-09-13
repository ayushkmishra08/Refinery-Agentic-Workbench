"""Tests for the canonical-text chunker: no overlap in extraction text, atomic procedures, typed chunks."""

from schemas.normalized_document import ContentType, NormalizedDocument, NormalizedElement
from src.chunker import DocumentChunker
from src.config import PipelineConfig


def _el(i, ctype, content, page=1, path="", level=None, proc=None, step=None, eng=True, chapter=None):
    return NormalizedElement(element_id=f"e{i}", content_type=ctype, content=content, page=page, position_in_page=i,
                             section_path=path, heading_level=level, procedure_id=proc, step_number=step,
                             is_engineering_content=eng, chapter_number=chapter)


def _doc(elements):
    return NormalizedDocument(document_id="d", source_filename="d.pdf", total_pages=9, elements=elements)


def test_extraction_text_has_no_overlap_but_retrieval_text_does():
    els = [
        _el(0, ContentType.HEADING, "Chapter 2: Introduction", path="Chapter 2: Introduction", level=1, chapter=2),
        _el(1, ContentType.PARAGRAPH, "Design capacity is 3.0 MMTPA. " * 60, path="Chapter 2: Introduction", chapter=2),
        _el(2, ContentType.HEADING, "Chapter 3: Basis of Design", path="Chapter 3: Basis of Design", level=1, page=2, chapter=3),
        _el(3, ContentType.PARAGRAPH, "Enhanced capacity is 3.2 MMTPA. " * 60, path="Chapter 3: Basis of Design", page=2, chapter=3),
    ]
    chunks = DocumentChunker(PipelineConfig()).chunk(_doc(els))
    assert len(chunks) == 2
    assert "[Context from previous" not in chunks[1].text
    assert "3.0 MMTPA" not in chunks[1].text            # the previous section's number never leaks in
    assert chunks[1].overlap_text and "3.0 MMTPA" in chunks[1].retrieval_text
    assert chunks[1].section_path.startswith("Chapter 3") and chunks[1].chapter_number == 3


def test_no_heading_only_chunks():
    els = [
        _el(0, ContentType.HEADING, "Chapter 2: Introduction", level=1),
        _el(1, ContentType.HEADING, "INTRODUCTION", level=2),
        _el(2, ContentType.PARAGRAPH, "Body text " * 50),
    ]
    chunks = DocumentChunker(PipelineConfig()).chunk(_doc(els))
    assert len(chunks) == 1 and chunks[0].text.startswith("# Chapter 2")


def test_procedure_is_atomic_and_typed():
    cfg = PipelineConfig()
    cfg.chunker.target_tokens = 200
    cfg.chunker.max_tokens = 300
    els = [
        _el(0, ContentType.HEADING, "16.1 CRUDE CHARGE PUMP 11-PM- 01A/B:", level=3, path="Ch 16 > 16.1"),
        _el(1, ContentType.PARAGRAPH, "It is a motor and turbine driven centrifugal pump. " * 12, path="Ch 16 > 16.1"),
        _el(2, ContentType.PARAGRAPH, "Pump Change over Procedure: (motor to turbine)", path="Ch 16 > 16.1", proc="p1", step=0),
    ] + [
        _el(3 + i, ContentType.PROCEDURE_STEP, f"Step instruction number {i} about the pump and its valves. " * 6,
            path="Ch 16 > 16.1", proc="p1", step=i + 1)
        for i in range(6)
    ]
    chunks = DocumentChunker(cfg).chunk(_doc(els))
    proc_chunks = [c for c in chunks if "p1" in c.procedure_ids]
    assert len(proc_chunks) == 1                        # never split, even though it exceeds max_tokens (< 2x max)
    pc = proc_chunks[0]
    assert pc.token_estimate > cfg.chunker.max_tokens
    assert pc.chunk_type == "procedure" and pc.contains_procedure
    assert pc.text.count("\n\n1. Step instruction number 0") == 1 and "6. Step instruction number 5" in pc.text
    assert "Pump Change over Procedure" in pc.text      # the label travels with its steps
    assert all(not c.contains_procedure for c in chunks if c is not pc)


def test_very_long_procedure_splits_only_at_step_boundaries():
    cfg = PipelineConfig()
    cfg.chunker.target_tokens = 100
    cfg.chunker.max_tokens = 150
    els = [_el(0, ContentType.PARAGRAPH, "Procedure:", proc="p1", step=0)] + [
        _el(1 + i, ContentType.PROCEDURE_STEP, f"Step {i} text here with words. " * 8, proc="p1", step=i + 1) for i in range(12)
    ]
    chunks = DocumentChunker(cfg).chunk(_doc(els))
    assert len(chunks) >= 2 and all("p1" in c.procedure_ids for c in chunks)
    for c in chunks:                                    # every chunk starts at a step (or the label), never mid-step
        assert c.elements[0].step_number is not None


def test_document_control_chunks_are_not_engineering():
    els = [
        _el(0, ContentType.HEADING, "Chapter 1: Administrative Requirements", level=1, eng=False, chapter=1),
        _el(1, ContentType.PARAGRAPH, "The controlled copies are held by the unit manager. " * 20, eng=False, chapter=1),
    ]
    chunks = DocumentChunker(PipelineConfig()).chunk(_doc(els))
    assert chunks[0].chunk_type == "document_control" and not chunks[0].is_engineering


def test_section_path_keywords_type_chunks():
    cfg = PipelineConfig()
    els = [
        _el(0, ContentType.HEADING, "17.3 Feed pump losing suction", level=3, path="Chapter 17: Upset Conditions > 17.3 Feed pump losing suction"),
        _el(1, ContentType.PARAGRAPH, "Check pump suction pressure and strainer DP. " * 20, path="Chapter 17: Upset Conditions > 17.3"),
    ]
    assert DocumentChunker(cfg).chunk(_doc(els))[0].chunk_type == "upset"
