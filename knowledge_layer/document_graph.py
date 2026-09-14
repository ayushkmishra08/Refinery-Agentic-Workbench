"""Document graph: structure, procedures and references written to Neo4j deterministically.

Everything here comes from the normalized document and the profile, so the
graph has a spine before any LLM runs:

    (:Document)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION]->(:Section)
    (:Chunk)-[:IN_SECTION]->(:Section)
    (:Document)-[:HAS_PROCEDURE]->(:Procedure)-[:HAS_STEP {sequence}]->(:ProcedureStep)-[:NEXT]->(:ProcedureStep)
    (:Procedure)-[:IN_SECTION]->(:Section), (:Procedure)-[:APPLIES_TO]->(:Entity), (:ProcedureStep)-[:MENTIONS]->(:Entity)
    (:Section)-[:REFERENCES {page}]->(:Chapter|:Section)
    (:Document)-[:HAS_STANDING_INSTRUCTION]->(:StandingInstruction)-[:INCORPORATED_IN]->(:Chapter)
    (:Document)-[:REFERENCES_DOCUMENT {page}]->(:DocumentReference)

Asset tags named in procedure steps become (stub) Entity nodes with the same
global UID the extractor uses, so the LLM/rule passes later enrich the very
same nodes.
"""

from __future__ import annotations

import hashlib
import logging

from knowledge_layer.schemas.document_profile import DocumentProfile
from knowledge_layer.schemas.normalized_document import NormalizedDocument
from knowledge_layer.entity_identity import entity_uid, identify_tag
from knowledge_layer.memory import Neo4jMemory

logger = logging.getLogger(__name__)


def chapter_id(document_id: str, number: int | str) -> str:
    return f"{document_id}#ch{number}"


def _tag_uid(tag: str, plant: str | None, unit: str | None, unit_scoping: bool) -> tuple[str, str] | None:
    ident = identify_tag(tag, plant=plant, unit=unit, unit_scoping=unit_scoping)
    if ident is None:
        return None
    return entity_uid(ident.canonical), ident.canonical


def write_document_graph(
    memory: Neo4jMemory,
    normalized: NormalizedDocument,
    profile: DocumentProfile | None,
    plant: str | None = None,
    unit: str | None = None,
    unit_scoping: bool = True,
) -> dict[str, int]:
    """Write chapters, sections, procedures, cross references and registers. Returns counts."""
    doc_id = normalized.document_id
    counts = {"chapters": 0, "sections": 0, "procedures": 0, "steps": 0, "cross_references": 0,
              "standing_instructions": 0, "document_references": 0}

    # -- chapters and sections ----------------------------------------------------
    chapters = [{
        "chapter_id": chapter_id(doc_id, ch.number), "number": ch.number, "title": ch.title,
        "page_start": ch.page_start, "page_end": ch.page_end, "revision": ch.revision,
        "revision_date": ch.revision_date, "source": ch.source, "is_administrative": ch.is_administrative,
    } for ch in normalized.chapters]
    sections = [{
        "section_id": n.section_id, "title": n.title, "level": n.level, "number": n.number,
        "page_start": n.page_start, "page_end": n.page_end, "path": n.path, "chapter_number": n.chapter_number,
        "element_count": n.element_count, "parent_section_id": n.parent_section_id,
        "chapter_id": chapter_id(doc_id, n.chapter_number) if n.chapter_number is not None else None,
    } for n in normalized.sections]
    memory.upsert_structure(doc_id, chapters, sections)
    counts["chapters"], counts["sections"] = len(chapters), len(sections)

    # -- procedures -----------------------------------------------------------------
    for proc in normalized.procedures:
        tag_nodes: dict[str, dict] = {}
        steps = []
        for st in proc.steps:
            step_tags = []
            for tag in st.tags:
                resolved = _tag_uid(tag, plant, unit, unit_scoping)
                if resolved is None:
                    continue
                uid, canonical = resolved
                tag_nodes.setdefault(uid, {"uid": uid, "tag": canonical, "name": tag,
                                           "evidence": st.text[:500], "page": st.page})
                step_tags.append({"uid": uid, "tag": canonical})
            steps.append({
                "step_id": f"{proc.procedure_id}#s{st.sequence:03d}", "sequence": st.sequence,
                "text": st.text, "page": st.page, "tags": step_tags,
            })
        memory.upsert_procedure({
            "procedure_id": proc.procedure_id, "document_id": doc_id, "title": proc.title,
            "procedure_type": proc.procedure_type.value, "section_id": proc.section_id,
            "section_path": proc.section_path, "chapter_number": proc.chapter_number,
            "page_start": proc.page_start, "page_end": proc.page_end, "applies_to": proc.applies_to,
            "steps": steps, "tag_nodes": list(tag_nodes.values()),
        })
        counts["procedures"] += 1
        counts["steps"] += len(steps)

    if profile is None:
        return counts

    # -- cross references -------------------------------------------------------------
    path_to_section = {n.path: n.section_id for n in normalized.sections}
    refs = []
    for x in profile.cross_references:
        src = path_to_section.get(x.source_section)
        if not src:
            continue
        refs.append({
            "source_section_id": src, "target_kind": x.target_kind, "target_number": x.target_number,
            "target_chapter_id": chapter_id(doc_id, x.target_number.split(".")[0]),
            "page": x.source_page, "evidence": x.evidence,
        })
    if refs:
        counts["cross_references"] = memory.upsert_cross_references(doc_id, refs)

    # -- standing instructions ----------------------------------------------------------
    items = [{
        "si_id": f"{doc_id}#si:{si.number}", "number": si.number, "title": si.title, "issue_date": si.issue_date,
        "status": si.status, "remark": si.remark, "page": si.page,
        "chapter_id": chapter_id(doc_id, si.incorporated_in_chapter) if si.incorporated_in_chapter else None,
    } for si in profile.standing_instructions]
    memory.upsert_standing_instructions(doc_id, items)
    counts["standing_instructions"] = len(items)

    # -- referenced documents -------------------------------------------------------------
    doc_refs = [{
        "ref_id": hashlib.md5(f"{r.document_type}|{r.document_number or r.reference_text}".lower().encode()).hexdigest()[:16],
        "number": r.document_number or r.reference_text, "document_type": r.document_type,
        "reference_text": r.reference_text, "present_in_corpus": r.present_in_corpus,
        "page": r.page or 0, "evidence": r.evidence,
    } for r in profile.referenced_documents]
    memory.upsert_document_references(doc_id, doc_refs)
    counts["document_references"] = len(doc_refs)

    logger.info(f"Document graph for {doc_id}: {counts}")
    return counts
