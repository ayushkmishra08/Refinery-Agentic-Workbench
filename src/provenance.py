"""Source provenance tracking.

Every engineering claim and relationship must be traceable to:
  - document
  - page
  - section
  - chunk
  - evidence text/table cells

Never produces orphan facts.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProvenanceRecord:
    """Complete provenance for an extracted fact."""
    document_id: str
    source_filename: str
    page: int
    section: str
    chunk_id: str
    evidence_text: str
    evidence_hash: str  # For dedup and integrity
    table_id: str | None = None
    table_cell: str | None = None  # "row:col" if from table


def create_provenance(
    document_id: str,
    source_filename: str,
    page: int,
    section: str,
    chunk_id: str,
    evidence_text: str,
    table_id: str | None = None,
    table_cell: str | None = None,
) -> ProvenanceRecord:
    """Create a provenance record with integrity hash."""
    evidence_hash = hashlib.sha256(
        f"{document_id}|{page}|{evidence_text}".encode()
    ).hexdigest()[:16]

    return ProvenanceRecord(
        document_id=document_id,
        source_filename=source_filename,
        page=page,
        section=section,
        chunk_id=chunk_id,
        evidence_text=evidence_text,
        evidence_hash=evidence_hash,
        table_id=table_id,
        table_cell=table_cell,
    )


def validate_provenance(record: ProvenanceRecord) -> bool:
    """Check that a provenance record is complete (no orphan facts)."""
    if not record.document_id:
        return False
    if not record.evidence_text:
        return False
    if record.page <= 0:
        return False
    return True
