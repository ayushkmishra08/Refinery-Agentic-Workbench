"""Chunk (de)serialisation identical to knowledge_layer.pipeline.save_chunks / load_chunks.

Copied here so the workbench never imports ``knowledge_layer.pipeline``: that module replaces
``sys.stdout``/``sys.stderr`` at import time (a UTF-8 wrapper for the CLI), which breaks pytest
capture and log capture in uvicorn. Keep this in sync with CHUNK_FIELDS in the pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path

CHUNK_FIELDS = (
    "chunk_id", "document_id", "sequence", "page_start", "page_end", "section_path", "parent_heading", "text",
    "contains_table", "table_ids", "contains_procedure", "procedure_ids", "element_types", "token_estimate",
    "overlap_text", "chunk_type", "is_engineering", "chapter_number", "section_id",
)


def save_chunks(chunks, path: Path) -> None:
    data = []
    for c in chunks:
        row = {k: getattr(c, k) for k in CHUNK_FIELDS}
        row["element_ids"] = [e.element_id for e in c.elements]
        data.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_chunks(path: Path, normalized=None) -> list:
    """Reload chunks; elements are re-attached from the normalized document when available."""
    from knowledge_layer.chunker import Chunk

    by_id = {e.element_id: e for e in normalized.elements} if normalized is not None else {}
    chunks = []
    for cd in json.loads(path.read_text(encoding="utf-8")):
        kwargs = {k: cd.get(k) for k in CHUNK_FIELDS if k in cd}
        kwargs.setdefault("contains_table", False)
        kwargs.setdefault("table_ids", [])
        kwargs.setdefault("contains_procedure", False)
        kwargs.setdefault("procedure_ids", [])
        kwargs.setdefault("element_types", [])
        kwargs.setdefault("token_estimate", 0)
        kwargs.setdefault("overlap_text", "")
        kwargs.setdefault("chunk_type", "narrative")
        kwargs.setdefault("is_engineering", True)
        chunk = Chunk(**kwargs)
        chunk.elements = [by_id[i] for i in cd.get("element_ids", []) if i in by_id]
        chunks.append(chunk)
    return chunks
