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
#: Share of the progress bar that reading the pages owns. It is where the time actually goes.
_PARSE_SHARE = 85


class SessionDocumentsBackend(IndexStore):
    name = "session_docs"

    def __init__(self, indexes: list[DocumentIndex], cfg) -> None:
        super().__init__(indexes, embeddings=None, use_vectors=False, use_reranker=False, rrf_k=cfg.retrieval.rrf_k)


def _scratch_pipeline(cfg, pdf_path: Path):
    """A pipeline config whose artefacts land in a scratch area, not in the knowledge layer.

    ``data/parsed`` is the knowledge layer's *input*: anything sitting there is a document somebody
    intends to ingest. A file dropped into a chat is not that, so its parse goes under
    ``data/workbench/uploads/_cache/<hash>/`` instead. The hash is over the file's bytes, so
    re-uploading the same document skips a three-minute parse, while two different files that
    happen to share a name cannot collide.
    """
    import hashlib

    digest = hashlib.sha256()
    with pdf_path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    root = cfg.paths.uploads_dir / "_cache" / digest.hexdigest()[:16]
    kl = cfg.knowledge_layer.model_copy(deep=True)
    kl.paths.parsed_dir = root / "parsed"
    kl.paths.normalized_dir = root / "normalized"
    kl.paths.knowledge_dir = root / "knowledge"
    for d in (kl.paths.parsed_dir, kl.paths.normalized_dir, kl.paths.knowledge_dir):
        d.mkdir(parents=True, exist_ok=True)
    return kl


def build_index_from_pdf(cfg, pdf_path: Path, on_progress=None, *, persist: bool = False) -> DocumentIndex:
    """Run the knowledge layer's deterministic pipeline on one PDF and return its DocumentIndex.

    ``persist`` decides whether the knowledge layer keeps anything. A session upload is somebody's
    working file: it is answered from, and then it is gone. Writing its chunks into
    ``data/knowledge/<doc>/`` would quietly add an unclassified document to the corpus that the
    next conversation — and the next person — would start finding in their answers. Promotion into
    the knowledge layer is a separate, deliberate act by someone senior enough to classify it.
    """
    from knowledge_layer.chunker import DocumentChunker
    from knowledge_layer.document_profile import DocumentProfiler
    from knowledge_layer.glossary_builder import GlossaryBuilder
    from knowledge_layer.normalizer import DocumentNormalizer
    from knowledge_layer.parser import DocumentParser, load_parsed_document
    from knowledge_layer.table_classifier import TableClassifier

    kl = cfg.knowledge_layer if persist else _scratch_pipeline(cfg, pdf_path)
    doc_id = pdf_path.stem
    t0 = time.time()
    parsed = load_parsed_document(kl, doc_id)
    if parsed is None:
        if on_progress:
            on_progress("parsing", f"Reading {pdf_path.name}")

        def pages(done: int, total: int) -> None:
            if on_progress:
                on_progress("parsing", f"read {done} of {total} pages", done=done, total=total, unit="pages")

        parsed = DocumentParser(kl).parse(pdf_path, on_progress=pages)
    if on_progress:
        on_progress("normalizing", f"parsed {getattr(parsed, 'total_pages', '?')} pages in {time.time() - t0:.0f}s")
    table_res = TableClassifier(kl).classify_all(parsed)
    normalized = DocumentNormalizer(kl).normalize(parsed, table_res)
    chunks = DocumentChunker(kl).chunk(normalized, table_res)
    profile = DocumentProfiler(kl).build_profile(normalized, table_res, parsed.tables)
    glossary = GlossaryBuilder(kl).build_glossary(normalized, profile)
    if persist:
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


def ingest_upload(orch, session_key: str, path: Path, note: str = "", *, owner: str = "",
                  owner_role: str = "", persist: bool = False) -> dict:
    """Entry point used by the API. Images are described synchronously; PDFs are indexed in a thread.

    ``session_key`` is the *namespaced* key (``<username>__<session_id>``) — the same one the ask
    path uses. Loading the raw session id here was the original bug: the upload was recorded
    against a conversation nobody was having, and the one the person was actually in never saw it.
    """
    session = orch.sessions.load(session_key, owner=owner, owner_role=owner_role)
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
    rs = orch.runs.create(session_key, f"[upload] {path.name}")
    rs.phase = "ingest"

    def work() -> None:
        try:
            def prog(stage: str, msg: str, *, done: int | None = None, total: int | None = None,
                     unit: str = "") -> None:
                rs.phase = f"ingest:{stage}"
                if done is not None and total:
                    # Reading the pages is nearly all of the wall clock — the sample document
                    # spends 210s of 213s in there — so it owns most of the bar, and the stages
                    # after it move it the rest of the way. A percentage that sat at 0 until the
                    # end and then jumped would be worse than none.
                    rs.progress = {"done": done, "total": total, "unit": unit or "pages",
                                   "percent": int(_PARSE_SHARE * done / total)}
                elif stage == "normalizing":
                    rs.progress = {"done": 0, "total": 0, "unit": "", "percent": _PARSE_SHARE}
                elif stage == "indexing":
                    rs.progress = {"done": 0, "total": 0, "unit": "", "percent": 95}
                rs.recent_events.append({"t": time.time() - rs.started, "event": "ingest", "agent": "ingest", "message": msg})

            idx = build_index_from_pdf(orch.cfg, path, prog, persist=persist)
            # Into this session's own shelf, not the shared corpus. `orch.knowledge.add(...)` —
            # what this used to do — made every upload visible to every session and every role,
            # for the lifetime of the process.
            existing = orch.session_uploads.get(session_key)
            indexes = [*(existing.indexes.values() if existing is not None else []), idx]
            orch.session_uploads[session_key] = SessionDocumentsBackend(list(indexes), orch.cfg)
            rs.progress = {"done": 0, "total": 0, "unit": "", "percent": 100}
            session.uploaded_documents.append({"document_id": idx.info.document_id, "name": path.name, "kind": "pdf",
                                               "added": time.time(), "note": note, "stats": idx.build_stats,
                                               # kept so the document can later be promoted into the
                                               # knowledge layer without being uploaded a second time
                                               "path": str(path)})
            orch.sessions.save(session)
            rs.final_status = "indexed"
        except Exception as exc:
            logger.exception("ingest failed")
            rs.error = f"{type(exc).__name__}: {exc}"
            rs.final_status = "failed"
        finally:
            rs.finished = time.time()

    threading.Thread(target=work, daemon=True).start()
    return {"status": "indexing", "kind": "pdf", "run_id": rs.run_id, "document_id": path.stem,
            "detail": "poll /runs/{run_id}; the document joins this conversation when final_status is 'indexed'"}
