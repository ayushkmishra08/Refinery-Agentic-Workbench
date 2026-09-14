"""Cross-document reference tracker.

Tracks documents explicitly referenced but not yet in the corpus.
Generates the "What should I download next?" report.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from knowledge_layer.schemas.document_profile import DocumentProfile, ReferencedDocument
from knowledge_layer.config import PipelineConfig

logger = logging.getLogger(__name__)


@dataclass
class ReferenceReport:
    """Report on referenced vs. present documents."""
    referenced_documents: list[ReferencedDocument] = field(default_factory=list)
    present_documents: list[str] = field(default_factory=list)
    missing_documents: list[ReferencedDocument] = field(default_factory=list)
    priority_downloads: list[dict] = field(default_factory=list)


class ReferenceTracker:
    """Tracks cross-document references across the corpus."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._all_references: list[ReferencedDocument] = []

    def add_profile_references(self, profile: DocumentProfile) -> None:
        """Add references from a document profile."""
        for ref in profile.referenced_documents:
            self._all_references.append(ref)

    def generate_report(self) -> ReferenceReport:
        """Generate the missing documents report."""
        # Find which PDFs are actually in data/raw/
        raw_dir = self.config.paths.raw_dir
        present = set()
        if raw_dir.exists():
            for f in raw_dir.iterdir():
                if f.suffix.lower() == ".pdf":
                    present.add(f.stem.lower())
                    present.add(f.name.lower())

        report = ReferenceReport(
            present_documents=list(present),
        )

        seen_refs: set[str] = set()
        for ref in self._all_references:
            ref_key = ref.reference_text.lower().strip()
            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)

            # Check if present
            ref.present_in_corpus = any(
                p in ref_key or ref_key in p
                for p in present
            )

            report.referenced_documents.append(ref)
            if not ref.present_in_corpus:
                report.missing_documents.append(ref)

        # Prioritize missing documents
        type_priority = {
            "P&ID": 1,
            "PFD": 2,
            "SOP": 3,
            "Document": 4,
            "Drawing": 5,
            "unknown": 6,
        }

        report.priority_downloads = sorted(
            [
                {
                    "reference": ref.reference_text,
                    "type": ref.document_type,
                    "reason": self._priority_reason(ref),
                    "priority": type_priority.get(ref.document_type, 6),
                }
                for ref in report.missing_documents
            ],
            key=lambda x: x["priority"],
        )

        return report

    def _priority_reason(self, ref: ReferencedDocument) -> str:
        """Explain why this document should be downloaded."""
        reasons = {
            "P&ID": "Needed to validate process connectivity and instrument tags",
            "PFD": "Needed to validate process flow and material balance",
            "SOP": "Needed for operational procedure cross-references",
            "Drawing": "Needed for equipment layout and piping details",
        }
        return reasons.get(ref.document_type, "Referenced by corpus documents")
