"""Ingestion of documents and images uploaded during a session.

PDF  -> knowledge_layer.parser.DocumentParser (Docling; GPU if free, CPU otherwise) -> TableClassifier ->
        DocumentNormalizer -> DocumentChunker -> DocumentProfiler -> GlossaryBuilder -> build_document_index
        -> SessionDocumentsBackend added to the CompositeKnowledgeService for that session.
        The knowledge layer's own artefacts are written under data/parsed|normalized|knowledge/<doc>/, so
        `python -m knowledge_layer` can later ingest the same document into Neo4j without re-parsing.
Image -> the vision model describes it (loaded on demand, released immediately) and the description
        becomes a session note the Context Resolver can use; nothing is written to the knowledge layer.
Parsing a large PDF is slow on a 4 GB card (minutes per 10-15 pages); the API runs it in a thread and
reports progress through the run registry.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from workbench.services.index.builder import DocumentIndex, build_document_index
from workbench.services.index.store import IndexStore

logger = logging.getLogger(__name__)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


class SessionDocumentsBackend(IndexStore):
    name = "session_docs"

    def __init__(self, indexes: list[DocumentIndex], cfg) -> None:
        super().__init__(indexes, embeddings=None, use_vectors=False, use_reranker=False, rrf_k=cfg.retrieval.rrf_k)


def build_index_from_pdf(cfg, pdf_path: Path, on_progress=None) -> DocumentIndex:
    """Run the knowledge layer's deterministic pipeline on one PDF and return its DocumentIndex."""
    from knowledge_layer.chunker import DocumentChunker
    from knowledge_layer.document_profile import DocumentProfiler
    from knowledge_layer.glossary_builder import GlossaryBuilder
    from knowledge_layer.normalizer import DocumentNormalizer
    from knowledge_layer.parser import DocumentParser, load_parsed_document
    from knowledge_layer.table_classifier import TableClassifier

    kl = cfg.knowledge_layer
    doc_id = pdf_path.stem
    t0 = time.time()
    parsed = load_parsed_document(kl, doc_id)
    if parsed is None:
        if on_progress:
            on_progress("parsing", f"Docling parse of {pdf_path.name} (this can take several minutes)")
        parsed = DocumentParser(kl).parse(pdf_path)
    if on_progress:
        on_progress("normalizing", f"parsed {getattr(parsed, 'total_pages', '?')} pages in {time.time() - t0:.0f}s")
    table_res = TableClassifier(kl).classify_all(parsed)
    normalized = DocumentNormalizer(kl).normalize(parsed, table_res)
    chunks = DocumentChunker(kl).chunk(normalized, table_res)
    profile = DocumentProfiler(kl).build_profile(normalized, table_res, parsed.tables)
    glossary = GlossaryBuilder(kl).build_glossary(normalized, profile)
    try:
        from workbench.services.kl_io import save_chunks

        (kl.paths.knowledge_dir / doc_id).mkdir(parents=True, exist_ok=True)
        save_chunks(chunks, kl.paths.knowledge_dir / doc_id / "chunks.json")
    except Exception as exc:
        logger.warning("chunks.json not saved for %s: %s", doc_id, exc)
    classes = {t.table_id: t.classification.value for t in table_res.classified_tables}
    if on_progress:
        on_progress("indexing", f"{len(chunks)} chunks, {len(normalized.procedures)} procedures")
    idx = build_document_index(doc_id, normalized, chunks, parsed.tables, profile, glossary, classes, origin="upload", authority_rank=40)
    logger.info("upload %s indexed in %.0fs: %s", doc_id, time.time() - t0, idx.build_stats)
    return idx


def ingest_upload(orch, session_id: str, path: Path, note: str = "") -> dict:
    """Entry point used by the API. Images are described synchronously; PDFs are indexed in a thread."""
    session = orch.sessions.load(session_id)
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        try:
            desc = orch.resources.describe_image(str(path), "Describe this image for a refinery engineer: equipment, tags, readings, warnings. Be literal; do not guess values that are not visible.")
        except Exception as exc:
            desc = f"(image could not be described: {exc})"
        session.notes.append(f"Image {path.name}: {desc}")
        session.uploaded_documents.append({"document_id": path.name, "name": path.name, "kind": "image", "added": time.time(), "note": note})
        orch.sessions.save(session)
        return {"status": "described", "kind": "image", "description": desc}
    if suffix != ".pdf":
        return {"status": "unsupported", "detail": f"{suffix} is not supported; upload a PDF or an image"}
    rs = orch.runs.create(session_id, f"[upload] {path.name}")
    rs.phase = "ingest"

    def work() -> None:
        try:
            def prog(stage: str, msg: str) -> None:
                rs.phase = f"ingest:{stage}"
                rs.recent_events.append({"t": time.time() - rs.started, "event": "ingest", "agent": "ingest", "message": msg})

            idx = build_index_from_pdf(orch.cfg, path, prog)
            backend = SessionDocumentsBackend([idx], orch.cfg)
            orch.knowledge.add(backend)
            session.uploaded_documents.append({"document_id": idx.info.document_id, "name": path.name, "kind": "pdf", "added": time.time(), "note": note, "stats": idx.build_stats})
            orch.sessions.save(session)
            rs.final_status = "indexed"
        except Exception as exc:
            logger.exception("ingest failed")
            rs.error = f"{type(exc).__name__}: {exc}"
            rs.final_status = "failed"
        finally:
            rs.finished = time.time()

    threading.Thread(target=work, daemon=True).start()
    return {"status": "indexing", "kind": "pdf", "run_id": rs.run_id, "detail": "poll /runs/{run_id}; the document joins this session's knowledge when final_status is 'indexed'"}
