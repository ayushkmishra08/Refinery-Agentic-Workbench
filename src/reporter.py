"""Report generation for processed documents.

Generates the per-document report (report.md): document overview and
structure recovered from the table of contents, normalization (what page
furniture was removed), chunk types, procedures, deterministic-vs-LLM
recall by source, graph insertions (potential conflicts / corroborations),
glossary, in-document cross references, standing instructions and
referenced documents that are not yet in the corpus.
"""

from __future__ import annotations

import logging
from collections import Counter
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
        chunks: list | None = None,
        source_counts: dict | None = None,
        document_graph_counts: dict | None = None,
        extraction_stats: dict | None = None,
    ) -> Path:
        """Generate a comprehensive document processing report."""
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
            lines.append(f"- **Revision**: {profile.revision or 'Unknown'}"
                         + (f" ({profile.effective_date})" if profile.effective_date else ""))
            lines.append(f"- **Plant/Unit**: {profile.plant or '?'} / {profile.unit or '?'}")
            lines.append(f"- **Pages**: {profile.total_pages}")
            lines.append(f"- **Chapters**: {len(profile.chapters)}"
                         + (f" ({sum(1 for c in profile.chapters if c.source != 'heading')} from the table of contents)"
                            if profile.chapters else ""))
            lines.append(f"- **Sections**: {len(profile.sections)}")
        else:
            lines.append("*(Profile not available)*\n")

        # Structure
        if normalized and normalized.chapters:
            lines.append("\n## Structure (from table of contents + page headers)\n")
            lines.append("| Ch | Title | Pages | Rev | Sections | Procedures | Admin |")
            lines.append("|----|-------|-------|-----|----------|------------|-------|")
            procs_by_ch = Counter(p.chapter_number for p in normalized.procedures)
            for ch in normalized.chapters:
                lines.append(
                    f"| {ch.number} | {ch.title} | {ch.page_start}-{ch.page_end} | {ch.revision or ''} | "
                    f"{len(ch.section_ids)} | {procs_by_ch.get(ch.number, 0)} | {'yes' if ch.is_administrative else ''} |"
                )

        # Normalization
        lines.append("\n## Normalization\n")
        if normalized:
            s = normalized.stats
            lines.append(f"- **Total elements**: {s.total_elements}")
            lines.append(f"- **Kept**: {s.kept_elements}")
            lines.append(f"- **Filtered**: {s.filtered_elements}")
            lines.append(f"  - Figure placeholders: {s.figure_placeholders_found}")
            lines.append(f"  - Page header/footer boxes: {s.repeated_headers_found + s.repeated_footers_found}")
            lines.append(f"  - Running chapter titles: {s.running_titles_found}")
            lines.append(f"  - Boilerplate lines: {s.boilerplate_patterns_found}")
            lines.append(f"  - Approval blocks: {s.approval_blocks_found}")
            lines.append(f"  - TOC entries/tables: {s.toc_entries_found}")
            lines.append(f"  - Administrative: {s.administrative_found}")
            lines.append(f"  - Empty: {s.empty_elements_found}")
            lines.append(f"- **Procedures detected**: {s.procedures_found} ({s.procedure_steps_found} steps)")
        else:
            lines.append("*(Normalization not available)*\n")

        # Chunks
        if chunks:
            lines.append("\n## Chunks\n")
            types = Counter(getattr(c, "chunk_type", "narrative") for c in chunks)
            toks = [c.token_estimate for c in chunks]
            lines.append(f"- **Count**: {len(chunks)} (engineering: {sum(1 for c in chunks if getattr(c, 'is_engineering', True))})")
            lines.append(f"- **Tokens**: min {min(toks)}, median {sorted(toks)[len(toks) // 2]}, max {max(toks)}")
            lines.append("- **Extraction text**: canonical source span once (no context overlap, no figure placeholders)")
            lines.append("\n| Chunk type | Count |")
            lines.append("|------------|-------|")
            for t, n in sorted(types.items(), key=lambda x: -x[1]):
                lines.append(f"| {t} | {n} |")

        # Procedures
        if normalized and normalized.procedures:
            lines.append("\n## Procedures\n")
            by_type = Counter(p.procedure_type.value for p in normalized.procedures)
            lines.append("| Type | Count |")
            lines.append("|------|-------|")
            for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
                lines.append(f"| {t} | {n} |")
            lines.append("\n### Examples\n")
            for p in sorted(normalized.procedures, key=lambda p: -len(p.steps))[:8]:
                tags = ", ".join(p.applies_to[:5])
                lines.append(f"- **{p.title[:80]}** [{p.procedure_type.value}] p.{p.page_start}-{p.page_end}, "
                             f"{len(p.steps)} steps" + (f", applies to {tags}" if tags else ""))

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

        # Extraction Summary
        lines.append("\n## Extraction Summary\n")
        if extraction_stats:
            lines.append(f"- **Chunks processed**: {extraction_stats.get('total', 0)} "
                         f"(LLM ok {extraction_stats.get('success', 0) + extraction_stats.get('retry_succeeded', 0)}, "
                         f"LLM empty {extraction_stats.get('empty', 0)}, LLM failed "
                         f"{extraction_stats.get('json_failed', 0) + extraction_stats.get('error', 0)}, "
                         f"deterministic only {extraction_stats.get('deterministic_only', 0)}, "
                         f"skipped {extraction_stats.get('skipped', 0)})")
        if extraction_counts:
            for key, value in extraction_counts.items():
                lines.append(f"- **{key}**: {value}")
            lines.append("\nPotential conflicts link two claims on one subject that share the full context key "
                         "(predicate + parameter role + location + operating mode + scenario + pressure basis + "
                         "table qualifier) and hold different values. Corroborations share the key and the value.")
        else:
            lines.append("*(No extraction data)*\n")
        if document_graph_counts:
            lines.append("\n### Document graph\n")
            for k, v in document_graph_counts.items():
                lines.append(f"- **{k}**: {v}")
        if source_counts:
            lines.append("\n### Recall by source (accepted / rejected / weak-grounded)\n")
            lines.append("| Source | Relationships | Claims |")
            lines.append("|--------|---------------|--------|")
            for src in ("llm_pass1", "llm_pass2", "rule", "table"):
                sc = source_counts.get(src)
                if not sc:
                    continue
                r, c = sc["relationships"], sc["claims"]
                lines.append(f"| {src} | {r['accepted']} / {r['rejected']} / {r['weak']} | "
                             f"{c['accepted']} / {c['rejected']} / {c['weak']} |")

        # Glossary
        lines.append("\n## Glossary\n")
        if glossary and glossary.entries:
            lines.append(f"- **Total entries**: {glossary.total_entries}")
            lines.append("\n| Abbreviation | Full Form | Source | Page |")
            lines.append("|-------------|-----------|--------|------|")
            for entry in glossary.entries[:50]:
                lines.append(f"| {entry.abbreviation} | {entry.full_form} | {entry.source} | {entry.page or '?'} |")
            if len(glossary.entries) > 50:
                lines.append(f"\n*...and {len(glossary.entries) - 50} more*")
        else:
            lines.append("*(No glossary entries)*\n")

        # Validation
        if validation_summary:
            lines.append("\n## Validation\n")
            lines.append(f"- **Chunks validated**: {validation_summary.total_chunks_validated}")
            lines.append(f"- **Passed**: {validation_summary.chunks_passed}")
            lines.append(f"- **With errors**: {validation_summary.chunks_with_errors}")
            lines.append(f"- **Total contradictions**: {validation_summary.total_contradictions}")

        # Cross references and standing instructions
        if profile and profile.cross_references:
            lines.append(f"\n## In-document cross references ({len(profile.cross_references)})\n")
            for x in profile.cross_references[:25]:
                lines.append(f"- p.{x.source_page} `{x.source_section[-60:]}` -> {x.target_kind} {x.target_number}: "
                             f"_{x.evidence[:100]}_")
        if profile and profile.standing_instructions:
            lines.append(f"\n## Standing instructions ({len(profile.standing_instructions)})\n")
            lines.append("| Number | Title | Issued | Status | Chapter |")
            lines.append("|--------|-------|--------|--------|---------|")
            for si in profile.standing_instructions:
                lines.append(f"| {si.number} | {si.title[:60]} | {si.issue_date} | {si.status} | {si.incorporated_in_chapter} |")

        # Referenced Documents
        lines.append("\n## Referenced Documents\n")
        if ref_report:
            if ref_report.missing_documents:
                lines.append(f"### Missing ({len(ref_report.missing_documents)})\n")
                for doc in ref_report.priority_downloads[:30]:
                    lines.append(f"- **{doc['reference']}** ({doc['type']}): {doc['reason']}")
            if ref_report.present_documents:
                lines.append(f"\n### Present ({len(ref_report.present_documents)})\n")
                for name in ref_report.present_documents[:10]:
                    lines.append(f"- {name}")
        else:
            lines.append("*(No reference data)*\n")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Report generated: {report_path}")
        return report_path
