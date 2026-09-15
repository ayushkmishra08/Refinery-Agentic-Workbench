"""FilesKnowledgeBackend: the knowledge layer's on-disk artefacts, no Neo4j.

Loads data/normalized/<doc>_normalized.json, data/knowledge/<doc>/{chunks,document_profile,
glossary,chunk_embeddings}.json and data/parsed/<doc>/parsed_document.json, rebuilds the
deterministic claims/relations with the knowledge layer's own modules, and caches the
resulting DocumentIndex under data/workbench/cache/<doc>/index.json (invalidated when any
source artefact changes or the builder version bumps). Cold build ~15 s per 560-page
manual; warm load ~3 s.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from knowledge_layer.config import PipelineConfig
from knowledge_layer.schemas.document_profile import DocumentProfile
from knowledge_layer.schemas.glossary import DocumentGlossary
from knowledge_layer.schemas.normalized_document import NormalizedDocument
from workbench.config import WorkbenchConfig
from workbench.services.index.builder import DocumentIndex, build_document_index
from workbench.services.kl_io import load_chunks
from workbench.services.index.store import IndexStore

logger = logging.getLogger(__name__)
BUILDER_VERSION = "2026-09-15.8"


def _artefact_paths(kl: PipelineConfig, doc_id: str) -> dict[str, Path]:
    return {
        "normalized": kl.paths.normalized_dir / f"{doc_id}_normalized.json",
        "chunks": kl.paths.knowledge_dir / doc_id / "chunks.json",
        "profile": kl.paths.knowledge_dir / doc_id / "document_profile.json",
        "glossary": kl.paths.knowledge_dir / doc_id / "glossary.json",
        "embeddings": kl.paths.knowledge_dir / doc_id / "chunk_embeddings.json",
        "parsed": kl.paths.parsed_dir / doc_id / "parsed_document.json",
    }


def _fingerprint(paths: dict[str, Path]) -> str:
    h = hashlib.md5(BUILDER_VERSION.encode())
    for k in ("normalized", "chunks", "profile", "glossary", "parsed"):
        p = paths[k]
        h.update(f"{k}:{p.stat().st_mtime_ns if p.exists() else 0}:{p.stat().st_size if p.exists() else 0}".encode())
    return h.hexdigest()[:16]


def load_document_index(cfg: WorkbenchConfig, doc_id: str, use_cache: bool = True) -> DocumentIndex:
    kl = cfg.knowledge_layer
    paths = _artefact_paths(kl, doc_id)
    if not paths["normalized"].exists() or not paths["chunks"].exists():
        raise FileNotFoundError(f"knowledge-layer artefacts for {doc_id!r} not found (normalized + chunks required)")
    cache_dir = cfg.paths.cache_dir / doc_id
    cache_file = cache_dir / "index.json"
    fp = _fingerprint(paths)
    if use_cache and cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            if raw.get("fingerprint") == fp:
                t0 = time.time()
                idx = DocumentIndex.model_validate(raw["index"])
                logger.info("loaded cached index for %s in %.1fs", doc_id, time.time() - t0)
                return idx
        except Exception as exc:
            logger.warning("index cache for %s unreadable (%s); rebuilding", doc_id, exc)
    t0 = time.time()
    normalized = NormalizedDocument.model_validate_json(paths["normalized"].read_text(encoding="utf-8"))
    chunks = load_chunks(paths["chunks"], normalized)
    profile = DocumentProfile.model_validate_json(paths["profile"].read_text(encoding="utf-8")) if paths["profile"].exists() else None
    glossary = DocumentGlossary.model_validate_json(paths["glossary"].read_text(encoding="utf-8")) if paths["glossary"].exists() else None
    tables = None
    table_classes: dict[str, str] = {}
    if paths["parsed"].exists():
        try:
            from knowledge_layer.parser import load_parsed_document
            from knowledge_layer.table_classifier import TableClassifier

            parsed = load_parsed_document(kl, doc_id)
            if parsed is not None:
                tables = parsed.tables
                table_res = TableClassifier(kl).classify_all(parsed)
                table_classes = {t.table_id: t.classification.value for t in table_res.classified_tables}
        except Exception as exc:
            logger.warning("parsed document for %s not usable (%s); table claims skipped", doc_id, exc)
    idx = build_document_index(doc_id, normalized, chunks, tables, profile, glossary, table_classes)
    logger.info("built index for %s in %.1fs: %s", doc_id, time.time() - t0, idx.build_stats)
    if use_cache:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"fingerprint": fp, "built_at": time.time(), "index": idx.model_dump(mode="json")}), encoding="utf-8")
    return idx


def load_embeddings(cfg: WorkbenchConfig, doc_id: str) -> dict[str, list[float]] | None:
    p = _artefact_paths(cfg.knowledge_layer, doc_id)["embeddings"]
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if raw.get("model") and raw["model"] != cfg.retrieval.embedding_model:
            logger.warning("embeddings for %s were built with %s, workbench uses %s; vector search disabled for this document",
                           doc_id, raw["model"], cfg.retrieval.embedding_model)
            return None
        return raw.get("embeddings") or None
    except Exception as exc:
        logger.warning("embeddings for %s unreadable: %s", doc_id, exc)
        return None


class FilesKnowledgeBackend(IndexStore):
    """IndexStore over every discovered knowledge-layer document."""

    name = "files"

    def __init__(self, cfg: WorkbenchConfig, document_ids: list[str] | None = None) -> None:
        ids = document_ids or cfg.document_ids or cfg.discovered_documents()
        if not ids:
            raise FileNotFoundError("no knowledge-layer documents found under data/knowledge")
        indexes = [load_document_index(cfg, d) for d in ids]
        embeddings = {d: e for d in ids if (e := load_embeddings(cfg, d)) is not None} if cfg.retrieval.use_vectors else None
        super().__init__(
            indexes, embeddings, embedding_model=cfg.retrieval.embedding_model, embedding_device=cfg.retrieval.embedding_device,
            reranker_model=cfg.retrieval.reranker_model if cfg.retrieval.use_reranker else None,
            reranker_device=cfg.retrieval.reranker_device, use_vectors=cfg.retrieval.use_vectors,
            use_reranker=cfg.retrieval.use_reranker, rrf_k=cfg.retrieval.rrf_k,
        )
        self.document_ids = ids
