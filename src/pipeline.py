"""Main pipeline orchestrator.

Workflow:
  1. Discover PDFs in data/raw/
  2. For each PDF:
     a. Parse with Docling (checkpoint)
     b. Normalize (checkpoint)
     c. Classify tables (checkpoint)
     d. Build document profile (checkpoint)
     e. Build glossary (checkpoint)
     f. Setup Neo4j memory (if available)
     g. Chunk document (checkpoint)
     h. For each chunk:
        - Retrieve context from Neo4j
        - Extract with DeepSeek-R1
        - Validate extraction
        - Insert into Neo4j
        - Update ontology
        (checkpoint per chunk)
     i. Generate report (checkpoint)

Entry point: python -m src
Flags:
  --clean       Clear Neo4j and reset extraction checkpoint (preserves parsing)
  --retry-failed  Only retry previously failed chunks
"""

from __future__ import annotations

import gc
import io
import json
import logging
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from src.checkpoint import PipelineCheckpoint, PipelinePhase
from src.config import PipelineConfig, load_config
from src.extractor import ExtractionStatus

# Force UTF-8 output to avoid Windows cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)
logger = logging.getLogger("rkl")


def setup_logging(config: PipelineConfig) -> None:
    """Configure structured logging."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def discover_documents(config: PipelineConfig) -> list[Path]:
    """Find all PDFs in data/raw/."""
    raw_dir = config.paths.raw_dir
    pdfs = sorted(raw_dir.glob("*.pdf"))
    if not pdfs:
        console.print("[yellow]No PDFs found in data/raw/. Place your documents there.[/]")
    else:
        console.print(f"[green]Found {len(pdfs)} document(s) to process[/]")
        for pdf in pdfs:
            size_mb = pdf.stat().st_size / (1024 * 1024)
            console.print(f"  -> {pdf.name} ({size_mb:.1f} MB)")
    return pdfs


def process_document(
    pdf_path: Path,
    config: PipelineConfig,
    clean: bool = False,
    retry_failed: bool = False,
) -> None:
    """Process a single document through the full pipeline."""
    doc_id = pdf_path.stem
    start_time = time.time()

    console.print(f"\n[bold blue]{'=' * 60}[/]")
    console.print(f"[bold blue]Processing: {pdf_path.name}[/]")
    console.print(f"[bold blue]{'=' * 60}[/]")

    # Initialize checkpoint
    checkpoint = PipelineCheckpoint(config, doc_id)

    # Handle --clean: reset extraction + clear Neo4j
    if clean:
        console.print("[yellow]  --clean: resetting extraction phase and Neo4j data[/]")
        checkpoint.reset_extraction_phase()

    # Track results for report
    profile = None
    glossary = None
    normalized = None
    table_results = None
    validation_summary = None
    ref_report = None
    extraction_counts = {
        "entities": 0, "relationships": 0, "claims": 0, "errors": 0,
    }
    extraction_stats = {
        "total": 0, "success": 0, "empty": 0,
        "retry_succeeded": 0, "json_failed": 0, "error": 0, "skipped": 0,
    }

    # -- Phase 1: Parse -----------------------------------------------
    parsed_doc = None
    if not checkpoint.is_phase_complete(PipelinePhase.PARSING):
        console.print("\n[cyan]Phase 1: Parsing with Docling...[/]")
        try:
            from src.parser import DocumentParser
            parser = DocumentParser(config)
            parsed_doc = parser.parse(pdf_path)
            checkpoint.set_source_hash(parsed_doc.source_hash)
            checkpoint.mark_phase_complete(PipelinePhase.PARSING)
            console.print(
                f"  [OK] Parsed: {parsed_doc.total_pages} pages, "
                f"{len(parsed_doc.tables)} tables"
            )
        except Exception as e:
            console.print(f"  [red][FAIL] Parse failed: {e}[/]")
            logger.exception("Parse failed")
            return
    else:
        console.print("[dim]Phase 1: Parsing -- skipped (checkpoint)[/]")
        # Load from disk
        parsed_path = config.paths.parsed_dir / f"{doc_id}_parsed.json"
        if parsed_path.exists():
            from schemas.parsed_document import ParsedDocument
            parsed_doc = ParsedDocument.model_validate_json(
                parsed_path.read_text(encoding="utf-8")
            )

    if not parsed_doc:
        console.print("[red]Cannot proceed without parsed document.[/]")
        return

    # -- Phase 2: Normalize --------------------------------------------
    if not checkpoint.is_phase_complete(PipelinePhase.NORMALIZING):
        console.print("\n[cyan]Phase 2: Normalizing...[/]")
        try:
            from src.normalizer import DocumentNormalizer
            normalizer = DocumentNormalizer(config)
            normalized = normalizer.normalize(parsed_doc)
            checkpoint.mark_phase_complete(PipelinePhase.NORMALIZING)
            console.print(
                f"  [OK] Normalized: kept {normalized.stats.kept_elements}/"
                f"{normalized.stats.total_elements} elements"
            )
        except Exception as e:
            console.print(f"  [red][FAIL] Normalization failed: {e}[/]")
            logger.exception("Normalization failed")
            return
    else:
        console.print("[dim]Phase 2: Normalizing -- skipped (checkpoint)[/]")
        norm_path = config.paths.normalized_dir / f"{doc_id}_normalized.json"
        if norm_path.exists():
            from schemas.normalized_document import NormalizedDocument
            normalized = NormalizedDocument.model_validate_json(
                norm_path.read_text(encoding="utf-8")
            )

    if not normalized:
        console.print("[red]Cannot proceed without normalized document.[/]")
        return

    # -- Phase 3: Table Classification ---------------------------------
    if not checkpoint.is_phase_complete(PipelinePhase.TABLE_CLASSIFICATION):
        console.print("\n[cyan]Phase 3: Classifying tables...[/]")
        try:
            from src.table_classifier import TableClassifier
            from src.table_reconstructor import reconstruct_table
            classifier = TableClassifier(config)
            table_results = classifier.classify_all(parsed_doc)

            # Reconstruct engineering tables
            for table in table_results.classified_tables:
                if table.enters_extraction:
                    reconstruct_table(table)

            checkpoint.mark_phase_complete(PipelinePhase.TABLE_CLASSIFICATION)
            console.print(
                f"  [OK] Classified {table_results.total_tables} tables: "
                + ", ".join(
                    f"{k}={v}"
                    for k, v in sorted(table_results.classification_summary.items())
                )
            )
        except Exception as e:
            console.print(f"  [red][FAIL] Table classification failed: {e}[/]")
            logger.exception("Table classification failed")
            table_results = None  # Non-fatal; continue
    else:
        console.print("[dim]Phase 3: Table classification -- skipped (checkpoint)[/]")

    # -- Phase 4: Document Profile -------------------------------------
    if not checkpoint.is_phase_complete(PipelinePhase.DOCUMENT_PROFILE):
        console.print("\n[cyan]Phase 4: Building document profile...[/]")
        try:
            from src.document_profile import DocumentProfiler
            from schemas.table_schema import TableNormalizationResult
            profiler = DocumentProfiler(config)
            if table_results is None:
                table_results = TableNormalizationResult(document_id=doc_id)
            profile = profiler.build_profile(normalized, table_results)
            checkpoint.mark_phase_complete(PipelinePhase.DOCUMENT_PROFILE)
            console.print(
                f"  [OK] Profile: type={profile.document_type.value}, "
                f"{len(profile.abbreviations)} abbreviations"
            )
        except Exception as e:
            console.print(f"  [red][FAIL] Profile failed: {e}[/]")
            logger.exception("Profile failed")
    else:
        console.print("[dim]Phase 4: Document profile -- skipped (checkpoint)[/]")
        profile_path = config.paths.knowledge_dir / doc_id / "document_profile.json"
        if profile_path.exists():
            from schemas.document_profile import DocumentProfile
            profile = DocumentProfile.model_validate_json(
                profile_path.read_text(encoding="utf-8")
            )

    # -- Phase 5: Glossary ---------------------------------------------
    if not checkpoint.is_phase_complete(PipelinePhase.GLOSSARY):
        console.print("\n[cyan]Phase 5: Building glossary...[/]")
        try:
            from src.glossary_builder import GlossaryBuilder
            from schemas.document_profile import DocumentProfile as DP
            builder = GlossaryBuilder(config)
            if profile is None:
                profile = DP(document_id=doc_id, source_filename=pdf_path.name)
            glossary = builder.build_glossary(normalized, profile)
            checkpoint.mark_phase_complete(PipelinePhase.GLOSSARY)
            console.print(f"  [OK] Glossary: {glossary.total_entries} entries")
        except Exception as e:
            console.print(f"  [red][FAIL] Glossary failed: {e}[/]")
            logger.exception("Glossary failed")
    else:
        console.print("[dim]Phase 5: Glossary -- skipped (checkpoint)[/]")
        glossary_path = config.paths.knowledge_dir / doc_id / "glossary.json"
        if glossary_path.exists():
            from schemas.glossary import DocumentGlossary
            glossary = DocumentGlossary.model_validate_json(
                glossary_path.read_text(encoding="utf-8")
            )

    # -- Phase 6: Chunk ------------------------------------------------
    chunks = []
    chunks_path = config.paths.knowledge_dir / doc_id / "chunks.json"
    if not checkpoint.is_phase_complete(PipelinePhase.CHUNKING):
        console.print("\n[cyan]Phase 6: Chunking...[/]")
        try:
            from src.chunker import DocumentChunker
            chunker = DocumentChunker(config)
            chunks = chunker.chunk(normalized, table_results)

            # Persist chunks to disk so they can be reloaded on restart
            chunks_data = []
            for c in chunks:
                chunks_data.append({
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "sequence": c.sequence,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "section_path": c.section_path,
                    "parent_heading": c.parent_heading,
                    "text": c.text,
                    "contains_table": c.contains_table,
                    "table_ids": c.table_ids,
                    "contains_procedure": c.contains_procedure,
                    "element_types": c.element_types,
                    "token_estimate": c.token_estimate,
                    "overlap_text": c.overlap_text,
                })
            chunks_path.parent.mkdir(parents=True, exist_ok=True)
            chunks_path.write_text(
                json.dumps(chunks_data, indent=2), encoding="utf-8",
            )

            checkpoint.mark_phase_complete(PipelinePhase.CHUNKING)
            console.print(f"  [OK] Created {len(chunks)} chunks (saved to disk)")
        except Exception as e:
            console.print(f"  [red][FAIL] Chunking failed: {e}[/]")
            logger.exception("Chunking failed")
            return
    else:
        console.print("[dim]Phase 6: Chunking -- skipped (checkpoint)[/]")
        # Reload chunks from disk
        if chunks_path.exists():
            from src.chunker import Chunk
            chunks_data = json.loads(chunks_path.read_text(encoding="utf-8"))
            for cd in chunks_data:
                chunks.append(Chunk(
                    chunk_id=cd["chunk_id"],
                    document_id=cd["document_id"],
                    sequence=cd["sequence"],
                    page_start=cd["page_start"],
                    page_end=cd["page_end"],
                    section_path=cd["section_path"],
                    parent_heading=cd["parent_heading"],
                    text=cd["text"],
                    contains_table=cd.get("contains_table", False),
                    table_ids=cd.get("table_ids", []),
                    contains_procedure=cd.get("contains_procedure", False),
                    element_types=cd.get("element_types", []),
                    token_estimate=cd.get("token_estimate", 0),
                    overlap_text=cd.get("overlap_text", ""),
                ))
            console.print(f"  [dim]Loaded {len(chunks)} chunks from disk[/]")
        else:
            # Chunks not saved yet — rechunk
            console.print("  [yellow]Chunk cache not found, re-chunking...[/]")
            from src.chunker import DocumentChunker
            chunker = DocumentChunker(config)
            chunks = chunker.chunk(normalized, table_results)
            console.print(f"  [OK] Re-chunked: {len(chunks)} chunks")

    # -- Phase 7: Neo4j Setup ------------------------------------------
    memory = None
    try:
        from src.memory import Neo4jMemory
        memory = Neo4jMemory(config)
        memory.connect()

        # Clear Neo4j data if --clean
        if clean:
            console.print("  [yellow]--clean: clearing all Neo4j data...[/]")
            memory.clear_all_data()

        if not checkpoint.is_phase_complete(PipelinePhase.NEO4J_SETUP):
            console.print("\n[cyan]Phase 7: Setting up Neo4j schema...[/]")
            memory.setup_schema()

            # Insert document node
            if profile:
                memory.upsert_document({
                    "document_id": doc_id,
                    "title": profile.title or "",
                    "doc_type": profile.document_type.value,
                    "revision": profile.revision or "",
                    "status": profile.status or "",
                    "plant": profile.plant or "",
                    "unit": profile.unit or "",
                    "source_filename": pdf_path.name,
                    "total_pages": profile.total_pages,
                })

            # Insert glossary terms
            if glossary:
                for entry in glossary.entries:
                    import hashlib
                    term_uid = hashlib.md5(
                        f"{entry.term}|{doc_id}".encode()
                    ).hexdigest()[:16]
                    memory.upsert_term({
                        "uid": term_uid,
                        "term": entry.term,
                        "canonical_meaning": entry.canonical_meaning,
                        "abbreviation": entry.abbreviation,
                        "full_form": entry.full_form,
                        "document_id": doc_id,
                        "page": entry.page or 0,
                        "evidence": entry.evidence,
                        "confidence": entry.confidence,
                    })

            checkpoint.mark_phase_complete(PipelinePhase.NEO4J_SETUP)
            console.print("  [OK] Neo4j schema ready, document and glossary inserted")
        else:
            console.print("[dim]Phase 7: Neo4j setup -- skipped (checkpoint)[/]")
    except Exception as e:
        console.print(f"  [yellow]WARNING: Neo4j not available: {e}[/]")
        console.print("  [yellow]  Skipping graph-dependent phases.[/]")
        memory = None

    # -- Phase 8: Extraction + Validation + Graph Insertion ------------
    if chunks and not checkpoint.is_phase_complete(PipelinePhase.EXTRACTION):
        console.print(f"\n[cyan]Phase 8: Extracting from {len(chunks)} chunks...[/]")

        extractor = None
        try:
            from src.extractor import OllamaExtractor
            extractor = OllamaExtractor(config)
            if not extractor.check_model_available():
                console.print("  [yellow]WARNING: Ollama/model not available. Skipping extraction.[/]")
                extractor = None
        except Exception as e:
            console.print(f"  [yellow]WARNING: Extractor init failed: {e}[/]")

        if extractor:
            from src.validator import ExtractionValidator
            from src.graph import GraphInserter
            from src.retriever import MemoryRetriever
            from src.ontology_manager import OntologyManager

            validator = ExtractionValidator(config, memory)
            inserter = GraphInserter(memory) if memory else None
            retriever = MemoryRetriever(memory, glossary) if memory else None
            ontology_mgr = OntologyManager(config)

            from src.retriever import RetrievalContext
            from schemas.document_profile import DocumentProfile as DP

            if profile is None:
                profile = DP(document_id=doc_id, source_filename=pdf_path.name)

            # Determine which chunks to process
            if retry_failed:
                failed_ids = {
                    f["chunk_id"] if isinstance(f, dict) else f
                    for f in checkpoint.get_failed_chunks()
                }
                chunks_to_process = [c for c in chunks if c.chunk_id in failed_ids]
                console.print(
                    f"  [yellow]--retry-failed: retrying {len(chunks_to_process)} "
                    f"previously failed chunks[/]"
                )
            else:
                chunks_to_process = chunks

            with Progress(
                SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                BarColumn(), TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("Extracting...", total=len(chunks_to_process))

                for chunk in chunks_to_process:
                    extraction_stats["total"] += 1

                    if checkpoint.is_chunk_complete(chunk.chunk_id):
                        extraction_stats["skipped"] += 1
                        progress.advance(task)
                        continue

                    # Retrieve context
                    context = RetrievalContext()
                    if retriever:
                        try:
                            context = retriever.retrieve_context(chunk)
                        except Exception as e:
                            logger.debug(f"Retrieval error: {e}")

                    # Extract
                    try:
                        result = extractor.extract_chunk(
                            chunk, context, profile, glossary,
                        )

                        # Track extraction status
                        if result.status == ExtractionStatus.SUCCESS:
                            extraction_stats["success"] += 1
                        elif result.status == ExtractionStatus.EMPTY:
                            extraction_stats["empty"] += 1
                        elif result.status == ExtractionStatus.RETRY_SUCCEEDED:
                            extraction_stats["retry_succeeded"] += 1
                        elif result.status == ExtractionStatus.JSON_FAILED:
                            extraction_stats["json_failed"] += 1
                        elif result.status == ExtractionStatus.ERROR:
                            extraction_stats["error"] += 1

                        # Only validate and insert successful extractions
                        if result.is_success:
                            extraction = result.extraction

                            # Validate
                            validation = validator.validate(extraction, chunk)

                            # Insert into graph
                            if inserter and validation.passed:
                                counts = inserter.insert_extraction(
                                    extraction, validation,
                                )
                                extraction_counts["entities"] += counts["entities"]
                                extraction_counts["relationships"] += counts["relationships"]
                                extraction_counts["claims"] += counts["claims"]
                                extraction_counts["errors"] += counts.get("errors", 0)

                            # Update ontology
                            ontology_mgr.update_from_extraction(extraction)

                            # Mark as complete
                            checkpoint.mark_chunk_complete(chunk.chunk_id)

                        elif result.is_failure:
                            # Mark as failed — will be retried on next run
                            checkpoint.mark_chunk_failed(
                                chunk.chunk_id, result.failure_reason,
                            )

                    except Exception as e:
                        logger.error(f"Extraction error for {chunk.chunk_id}: {e}")
                        checkpoint.mark_chunk_failed(chunk.chunk_id, str(e))
                        extraction_stats["error"] += 1

                    progress.advance(task)

                    # Memory management
                    gc.collect()

            ontology_mgr.increment_documents()
            ontology_mgr.save()
            extractor.close()

            # Print extraction summary
            _print_extraction_summary(extraction_stats, extraction_counts, checkpoint)

        checkpoint.mark_phase_complete(PipelinePhase.EXTRACTION)
        checkpoint.mark_phase_complete(PipelinePhase.VALIDATION)
        checkpoint.mark_phase_complete(PipelinePhase.GRAPH_INSERTION)
    elif not chunks:
        console.print("[dim]Phase 8: No chunks to process (loading from checkpoint)[/]")
        # Chunks were loaded from a previous run; check if extraction was done
    else:
        console.print("[dim]Phase 8: Extraction -- skipped (checkpoint)[/]")

    # Flush checkpoint
    checkpoint.flush()

    # -- Phase 9: Report -----------------------------------------------
    if not checkpoint.is_phase_complete(PipelinePhase.REPORTING):
        console.print("\n[cyan]Phase 9: Generating report...[/]")
        try:
            from src.reporter import ReportGenerator
            from src.reference_tracker import ReferenceTracker

            # Reference tracking
            ref_tracker = ReferenceTracker(config)
            if profile:
                ref_tracker.add_profile_references(profile)
            ref_report = ref_tracker.generate_report()

            reporter = ReportGenerator(config)
            report_path = reporter.generate_document_report(
                doc_id=doc_id,
                profile=profile,
                glossary=glossary,
                normalized=normalized,
                table_results=table_results,
                ref_report=ref_report,
                processing_time=time.time() - start_time,
                extraction_counts=extraction_counts,
            )

            checkpoint.mark_phase_complete(PipelinePhase.REPORTING)
            checkpoint.mark_phase_complete(PipelinePhase.COMPLETE)
            console.print(f"  [OK] Report: {report_path}")
        except Exception as e:
            console.print(f"  [red][FAIL] Report failed: {e}[/]")
            logger.exception("Report failed")
    else:
        console.print("[dim]Phase 9: Reporting -- skipped (checkpoint)[/]")

    # Close Neo4j
    if memory:
        memory.close()

    elapsed = time.time() - start_time
    console.print(f"\n[bold green][DONE] {pdf_path.name} completed in {elapsed:.1f}s[/]")
    console.print(
        f"  Entities: {extraction_counts['entities']}, "
        f"Relations: {extraction_counts['relationships']}, "
        f"Claims: {extraction_counts['claims']}"
    )


def _print_extraction_summary(
    stats: dict, counts: dict, checkpoint: PipelineCheckpoint,
) -> None:
    """Print a detailed extraction summary."""
    console.print("\n[bold]Extraction Summary[/]")
    console.print(f"  Total chunks processed: {stats['total']}")
    console.print(f"  Successful:             {stats['success']}")
    console.print(f"  Empty (no content):     {stats['empty']}")
    console.print(f"  Retry succeeded:        {stats['retry_succeeded']}")
    console.print(f"  JSON parse failed:      {stats['json_failed']}")
    console.print(f"  Other errors:           {stats['error']}")
    console.print(f"  Skipped (checkpoint):   {stats['skipped']}")
    console.print()
    console.print(f"  Graph insertions:")
    console.print(f"    Entities:      {counts['entities']}")
    console.print(f"    Claims:        {counts['claims']}")
    console.print(f"    Relationships: {counts['relationships']}")
    console.print(f"    Insert errors: {counts.get('errors', 0)}")

    failed = checkpoint.get_failed_chunks()
    if failed:
        console.print(f"\n  [yellow]Failed chunks ({len(failed)}) — will retry on next run:[/]")
        for f in failed[:10]:
            chunk_id = f["chunk_id"] if isinstance(f, dict) else f
            reason = f.get("reason", "unknown") if isinstance(f, dict) else "unknown"
            console.print(f"    - {chunk_id}: {reason[:80]}")
        if len(failed) > 10:
            console.print(f"    ... and {len(failed) - 10} more")


def main() -> None:
    """Main pipeline entry point."""
    console.print("[bold]Refinery Knowledge Layer[/] v0.2.0\n")

    config = load_config()
    setup_logging(config)

    # Parse command line flags
    clean = "--clean" in sys.argv
    retry_failed = "--retry-failed" in sys.argv

    if clean:
        console.print("[yellow]Running in --clean mode: will reset extraction + Neo4j[/]")
    if retry_failed:
        console.print("[yellow]Running in --retry-failed mode: only retrying failed chunks[/]")

    pdfs = discover_documents(config)
    if not pdfs:
        return

    for pdf in pdfs:
        try:
            process_document(pdf, config, clean=clean, retry_failed=retry_failed)
        except Exception as e:
            console.print(f"[red]Fatal error processing {pdf.name}: {e}[/]")
            logger.exception(f"Fatal error: {pdf.name}")
            continue

    console.print("\n[bold green]Pipeline complete.[/]")


if __name__ == "__main__":
    main()
