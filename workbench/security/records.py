"""Record identity: the stable name a single piece of knowledge is known by.

An access grant opens *named records*, not documents, so every record the knowledge layer can
return needs an id that is the same each time it is computed — in the scoping pass that builds
the grant, and in the guard that later honours it. That is all this module does.

The ids are opaque to the person who receives them. ``claim:c1483`` says a claim exists; it does
not say what the claim is. That is what makes it safe to show a requester the scope of their own
pending request before it has been approved.
"""
from __future__ import annotations

import hashlib

from workbench.core.knowledge import (
    ChunkRecord,
    ClaimRecord,
    EntityRecord,
    ProcedureRecord,
    RelationRecord,
    SectionRecord,
)


def record_id(record) -> str | None:
    """The stable id of one knowledge record, or None for a shape that carries no identity."""
    if isinstance(record, ClaimRecord):
        return f"claim:{record.claim_id}"
    if isinstance(record, ChunkRecord):
        return f"chunk:{record.chunk_id}"
    if isinstance(record, ProcedureRecord):
        return f"procedure:{record.procedure_id}"
    if isinstance(record, SectionRecord):
        return f"section:{record.section_id}"
    if isinstance(record, EntityRecord):
        return f"entity:{record.entity_uid}"
    if isinstance(record, RelationRecord):
        # relationships have no id of their own: name them by what they assert and where
        body = f"{record.source_uid}|{record.rel_type}|{record.target_uid}|{record.document_id}|{record.page}"
        return f"relation:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]}"
    doc = getattr(record, "document_id", None)
    if doc is None:
        return None
    body = repr(sorted(record.model_dump().items())) if hasattr(record, "model_dump") else repr(record)
    return f"{type(record).__name__.lower()}:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]}"


def kind_of(rid: str) -> str:
    return rid.split(":", 1)[0] if ":" in rid else "record"


def fingerprint(record_ids) -> str:
    """A hash over a record set, so a grant cannot be widened after it was approved."""
    joined = "\n".join(sorted(set(record_ids)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def question_fingerprint(question: str) -> str:
    """A hash over the question, so a key issued for one question cannot answer another."""
    normalized = " ".join((question or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
