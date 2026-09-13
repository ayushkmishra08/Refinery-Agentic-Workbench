"""Chunk embedding step.

Computes sentence embeddings for every chunk (all-MiniLM-L6-v2, 384-dim, CPU
by default so the GPU stays free for Ollama) and

  * caches them on disk under data/knowledge/<doc>/chunk_embeddings.json so a
    restart does not recompute them, and
  * writes them onto Chunk nodes in Neo4j so the vector index
    ``chunk_embeddings`` can serve similarity retrieval during extraction.

The embedding text is the chunk body prefixed with its section path so that
retrieval is structure-aware.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src.chunker import Chunk
from src.config import PipelineConfig

logger = logging.getLogger(__name__)


class ChunkEmbedder:
    """Embeds chunks with a sentence-transformers model."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer
        t0 = time.time()
        self._model = SentenceTransformer(
            self.config.embedding.model_name, device=self.config.embedding.device,
        )
        dim = self._model.get_sentence_embedding_dimension()
        if dim != self.config.embedding.dimensions:
            raise RuntimeError(
                f"Embedding model {self.config.embedding.model_name} produces {dim} dims but "
                f"config expects {self.config.embedding.dimensions}"
            )
        logger.info(
            f"Embedding model {self.config.embedding.model_name} loaded on "
            f"{self.config.embedding.device} in {time.time() - t0:.1f}s ({dim} dims)"
        )
        return self._model

    @staticmethod
    def _embedding_text(chunk: Chunk) -> str:
        """Retrieval representation: section path + short bridge + canonical text (never used for extraction)."""
        return chunk.retrieval_text

    def cache_path(self, doc_id: str) -> Path:
        return self.config.paths.knowledge_dir / doc_id / "chunk_embeddings.json"

    def embed_chunks(self, doc_id: str, chunks: list[Chunk]) -> dict[str, list[float]]:
        """Return {chunk_id: embedding}; populates ``chunk.embedding`` in place."""
        cache_file = self.cache_path(doc_id)
        cached: dict[str, list[float]] = {}
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if data.get("model") == self.config.embedding.model_name:
                    cached = data.get("embeddings", {})
            except Exception as e:
                logger.warning(f"Could not read embedding cache: {e}")

        todo = [c for c in chunks if c.chunk_id not in cached]
        if todo:
            model = self._load_model()
            texts = [self._embedding_text(c) for c in todo]
            t0 = time.time()
            vectors = model.encode(
                texts,
                batch_size=self.config.embedding.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            for c, v in zip(todo, vectors):
                cached[c.chunk_id] = [float(x) for x in v.tolist()]
            logger.info(f"Embedded {len(todo)} chunks in {time.time() - t0:.1f}s")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "model": self.config.embedding.model_name,
                "dimensions": self.config.embedding.dimensions,
                "embeddings": cached,
            }), encoding="utf-8")
        else:
            logger.info(f"All {len(chunks)} chunk embeddings loaded from cache")

        for c in chunks:
            c.embedding = cached.get(c.chunk_id)
        return {c.chunk_id: cached[c.chunk_id] for c in chunks if c.chunk_id in cached}

    def embed_query(self, text: str) -> list[float]:
        model = self._load_model()
        v = model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
        return [float(x) for x in v.tolist()]


def store_chunks_in_memory(memory, doc_id: str, chunks: list[Chunk]) -> int:
    """Upsert chunk nodes (with embeddings) into Neo4j. Returns count written."""
    written = 0
    for c in chunks:
        memory.upsert_chunk({
            "chunk_id": c.chunk_id,
            "document_id": doc_id,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "section": c.section_path,
            "section_id": c.section_id,
            "chapter_number": c.chapter_number,
            "sequence": c.sequence,
            "embedding": c.embedding,
            "text": c.text[:4000],
            "chunk_type": c.chunk_type,
            "is_engineering": c.is_engineering,
            "contains_table": c.contains_table,
            "contains_procedure": c.contains_procedure,
            "table_ids": c.table_ids,
            "procedure_ids": c.procedure_ids,
        })
        written += 1
    return written
