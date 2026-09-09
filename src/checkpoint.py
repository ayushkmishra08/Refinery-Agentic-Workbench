"""Checkpoint/resume system.

Checkpoints at each pipeline phase so interrupted processing can resume
exactly where it stopped. No expensive work is redone.

Tracks both completed AND failed chunks separately so failed chunks
can be retried on restart without reprocessing successful chunks.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum

from src.config import PipelineConfig

logger = logging.getLogger(__name__)


class PipelinePhase(str, Enum):
    """Pipeline processing phases."""
    PARSING = "parsing"
    NORMALIZING = "normalizing"
    TABLE_CLASSIFICATION = "table_classification"
    DOCUMENT_PROFILE = "document_profile"
    GLOSSARY = "glossary"
    NEO4J_SETUP = "neo4j_setup"
    CHUNKING = "chunking"
    EXTRACTION = "extraction"
    VALIDATION = "validation"
    GRAPH_INSERTION = "graph_insertion"
    REPORTING = "reporting"
    COMPLETE = "complete"


class PipelineCheckpoint:
    """Manages checkpoint state for a document processing pipeline."""

    def __init__(self, config: PipelineConfig, document_id: str):
        self.config = config
        self.document_id = document_id
        self._checkpoint_path = (
            config.paths.checkpoints_dir / f"{document_id}_pipeline.json"
        )
        self._state: dict = self._load()

    def _load(self) -> dict:
        """Load existing checkpoint or create new."""
        if self._checkpoint_path.exists():
            try:
                return json.loads(
                    self._checkpoint_path.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning(f"Could not load checkpoint: {e}")
        return {
            "document_id": self.document_id,
            "current_phase": PipelinePhase.PARSING.value,
            "completed_phases": [],
            "completed_chunks": [],
            "failed_chunks": [],
            "last_updated": "",
            "source_hash": "",
        }

    def _save(self) -> None:
        """Persist checkpoint to disk (atomic write)."""
        self._state["last_updated"] = datetime.now(timezone.utc).isoformat()
        temp_path = self._checkpoint_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._state, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._checkpoint_path)

    def is_phase_complete(self, phase: PipelinePhase) -> bool:
        """Check if a phase has been completed."""
        return phase.value in self._state.get("completed_phases", [])

    def mark_phase_complete(self, phase: PipelinePhase) -> None:
        """Mark a phase as completed."""
        if phase.value not in self._state["completed_phases"]:
            self._state["completed_phases"].append(phase.value)
        self._state["current_phase"] = phase.value
        self._save()
        logger.info(f"Checkpoint: {self.document_id} phase {phase.value} complete")

    def is_chunk_complete(self, chunk_id: str) -> bool:
        """Check if a specific chunk has been successfully processed."""
        return chunk_id in self._state.get("completed_chunks", [])

    def is_chunk_failed(self, chunk_id: str) -> bool:
        """Check if a specific chunk failed in a previous run."""
        failed = self._state.get("failed_chunks", [])
        return any(
            (f["chunk_id"] if isinstance(f, dict) else f) == chunk_id
            for f in failed
        )

    def mark_chunk_complete(self, chunk_id: str) -> None:
        """Mark a chunk as successfully processed.
        
        Also removes it from failed_chunks if it was previously failed
        (i.e., it was retried and succeeded).
        """
        if chunk_id not in self._state.get("completed_chunks", []):
            self._state.setdefault("completed_chunks", []).append(chunk_id)
        
        # Remove from failed list if it was there (retry succeeded)
        failed = self._state.get("failed_chunks", [])
        self._state["failed_chunks"] = [
            f for f in failed
            if (f["chunk_id"] if isinstance(f, dict) else f) != chunk_id
        ]
        
        # Save periodically (every 10 chunks) to avoid too many disk writes
        if len(self._state["completed_chunks"]) % 10 == 0:
            self._save()

    def mark_chunk_failed(self, chunk_id: str, reason: str = "") -> None:
        """Mark a chunk as failed.
        
        Failed chunks are NOT in completed_chunks. They will be retried
        on the next pipeline run.
        """
        failed = self._state.setdefault("failed_chunks", [])
        # Remove existing entry for this chunk (to update reason)
        self._state["failed_chunks"] = [
            f for f in failed
            if (f["chunk_id"] if isinstance(f, dict) else f) != chunk_id
        ]
        self._state["failed_chunks"].append({
            "chunk_id": chunk_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._save()  # Always save failures immediately

    def set_source_hash(self, source_hash: str) -> None:
        """Record the source file hash for integrity."""
        stored = self._state.get("source_hash", "")
        if stored and stored != source_hash:
            logger.warning(
                f"Source file changed! Old hash: {stored[:12]}, "
                f"new hash: {source_hash[:12]}. Resetting checkpoint."
            )
            self._state = {
                "document_id": self.document_id,
                "current_phase": PipelinePhase.PARSING.value,
                "completed_phases": [],
                "completed_chunks": [],
                "failed_chunks": [],
                "last_updated": "",
                "source_hash": source_hash,
            }
        else:
            self._state["source_hash"] = source_hash
        self._save()

    def reset_extraction_phase(self) -> None:
        """Reset only the extraction phase for a clean re-run.
        
        Preserves parsing/normalizing/chunking checkpoints but clears
        extraction progress so all chunks are reprocessed.
        """
        self._state["completed_chunks"] = []
        self._state["failed_chunks"] = []
        # Remove extraction-related phases from completed
        extraction_phases = {
            PipelinePhase.NEO4J_SETUP.value,
            PipelinePhase.EXTRACTION.value,
            PipelinePhase.VALIDATION.value,
            PipelinePhase.GRAPH_INSERTION.value,
            PipelinePhase.REPORTING.value,
            PipelinePhase.COMPLETE.value,
        }
        self._state["completed_phases"] = [
            p for p in self._state.get("completed_phases", [])
            if p not in extraction_phases
        ]
        self._state["current_phase"] = PipelinePhase.NEO4J_SETUP.value
        self._save()
        logger.info(f"Checkpoint: extraction phase reset for {self.document_id}")

    def flush(self) -> None:
        """Force save current state."""
        self._save()

    def get_completed_chunks(self) -> list[str]:
        """Get list of completed chunk IDs."""
        return self._state.get("completed_chunks", [])

    def get_failed_chunks(self) -> list[dict]:
        """Get list of failed chunk entries."""
        return self._state.get("failed_chunks", [])

    def get_status(self) -> dict:
        """Get current checkpoint status."""
        failed = self._state.get("failed_chunks", [])
        return {
            "document_id": self.document_id,
            "current_phase": self._state.get("current_phase", "unknown"),
            "completed_phases": self._state.get("completed_phases", []),
            "completed_chunks_count": len(self._state.get("completed_chunks", [])),
            "failed_chunks_count": len(failed),
            "last_updated": self._state.get("last_updated", ""),
        }
