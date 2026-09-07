"""Report generation for processed documents.

Generates:
  - Per-document report (report.md)
  - Missing documents report
  - Ontology discovery report
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from schemas.document_profile import DocumentProfile
from schemas.glossary import DocumentGlossary
from schemas.normalized_document import NormalizedDocument
from schemas.table_schema import TableNormalizationResult
from schemas.validation import DocumentValidationSummary
from src.config import PipelineConfig
from src.reference_tracker import ReferenceReport

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates processing reports."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def generate_document_report(
        self,
        doc_id: str,
        profile: DocumentProfile | None = None,
        glossary: DocumentGlossary | None = None,
        normalized: NormalizedDocument | None = None,
        table_results: TableNormalizationResult | None = None,
        validation_summary: DocumentValidationSummary | None = None,
        ref_report: ReferenceReport | None = None,
        processing_time: float = 0.0,
        extraction_counts: dict | None = None,
    ) -> Path:
        """Generate a comprehensive document processing report.

        Returns:
            Path to the generated report.
        """
        report_dir = self.config.paths.reports_dir / doc_id
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "report.md"

        lines: list[str] = []
        lines.append(f"# Processing Report: {doc_id}")
        lines.append(f"\nGenerated: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"Processing Time: {processing_time:.1f}s\n")

        # Document Overview
        lines.append("## Document Overview\n")
        if profile:
            lines.append(f"- **Title**: {profile.title or 'Unknown'}")
            lines.append(f"- **Type**: {profile.document_type.value}")
            lines.append(f"- **Revision**: {profile.revision or 'Unknown'}")
            lines.append(f"- **Plant/Unit**: {profile.plant or '?'} / {profile.unit or '?'}")
            lines.append(f"- **Pages**: {profile.total_pages}")
            lines.append(f"- **Chapters**: {len(profile.chapters)}")
            lines.append(f"- **Sections**: {len(profile.sections)}")
        else:
            lines.append("*(Profile not available)*\n")

        # Normalization
        lines.append("\n## Normalization\n")
        if normalized:
            s = normalized.stats
            lines.append(f"- **Total elements**: {s.total_elements}")
            lines.append(f"- **Kept**: {s.kept_elements}")
            lines.append(f"- **Filtered**: {s.filtered_elements}")
            lines.append(f"  - Boilerplate: {s.boilerplate_patterns_found}")
            lines.append(f"  - Repeated headers: {s.repeated_headers_found}")
            lines.append(f"  - Repeated footers: {s.repeated_footers_found}")
            lines.append(f"  - Approval blocks: {s.approval_blocks_found}")
            lines.append(f"  - TOC entries: {s.toc_entries_found}")
            lines.append(f"  - Decorative: {s.decorative_elements_found}")
            lines.append(f"  - Empty: {s.empty_elements_found}")
        else:
            lines.append("*(Normalization not available)*\n")

        # Tables
        lines.append("\n## Tables\n")
        if table_results:
            lines.append(f"- **Total tables**: {table_results.total_tables}")
            lines.append(f"- **Repeated patterns**: {len(table_results.repeated_table_patterns)}")
            lines.append(f"- **Duplicates**: {len(table_results.duplicate_pairs)}")
            lines.append("\n### Classification Summary\n")
            lines.append("| Type | Count |")
            lines.append("|------|-------|")
            for cls, count in sorted(table_results.classification_summary.items()):
                lines.append(f"| {cls} | {count} |")
        else:
            lines.append("*(Table results not available)*\n")

        # Glossary
        lines.append("\n## Glossary\n")
        if glossary and glossary.entries:
            lines.append(f"- **Total entries**: {glossary.total_entries}")
            lines.append("\n| Abbreviation | Full Form | Source | Page |")
            lines.append("|-------------|-----------|--------|------|")
            for entry in glossary.entries[:50]:
                lines.append(
                    f"| {entry.abbreviation} | {entry.full_form} "
                    f"| {entry.source} | {entry.page or '?'} |"
                )
            if len(glossary.entries) > 50:
                lines.append(f"\n*...and {len(glossary.entries) - 50} more*")
        else:
            lines.append("*(No glossary entries)*\n")

        # Extraction Summary
        lines.append("\n## Extraction Summary\n")
        if extraction_counts:
            for key, value in extraction_counts.items():
                lines.append(f"- **{key}**: {value}")
        else:
            lines.append("*(No extraction data)*\n")

        # Validation
        lines.append("\n## Validation\n")
        if validation_summary:
            lines.append(f"- **Chunks validated**: {validation_summary.total_chunks_validated}")
            lines.append(f"- **Passed**: {validation_summary.chunks_passed}")
            lines.append(f"- **With errors**: {validation_summary.chunks_with_errors}")
            lines.append(f"- **With warnings**: {validation_summary.chunks_with_warnings}")
            lines.append(f"- **Total contradictions**: {validation_summary.total_contradictions}")
            lines.append(f"- **Valid entities**: {validation_summary.total_valid_entities}")
            lines.append(f"- **Valid relationships**: {validation_summary.total_valid_relationships}")
            lines.append(f"- **Valid claims**: {validation_summary.total_valid_claims}")
            lines.append(f"- **Rejected entities**: {validation_summary.total_rejected_entities}")
            lines.append(f"- **Rejected relationships**: {validation_summary.total_rejected_relationships}")
            lines.append(f"- **Rejected claims**: {validation_summary.total_rejected_claims}")

            if validation_summary.all_issues:
                lines.append("\n### Issues\n")
                for issue in validation_summary.all_issues[:30]:
                    emoji = "❌" if issue.severity.value == "error" else "⚠️"
                    lines.append(f"- {emoji} [{issue.category.value}] {issue.message}")
        else:
            lines.append("*(No validation data)*\n")

        # Referenced Documents
        lines.append("\n## Referenced Documents\n")
        if ref_report:
            if ref_report.missing_documents:
                lines.append(f"### Missing ({len(ref_report.missing_documents)})\n")
                for doc in ref_report.priority_downloads[:20]:
                    lines.append(
                        f"- **{doc['reference']}** ({doc['type']}): {doc['reason']}"
                    )
            if ref_report.present_documents:
                lines.append(f"\n### Present ({len(ref_report.present_documents)})\n")
                for name in ref_report.present_documents[:10]:
                    lines.append(f"- {name}")
        else:
            lines.append("*(No reference data)*\n")

        # Write report
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Report generated: {report_path}")

        return report_path
