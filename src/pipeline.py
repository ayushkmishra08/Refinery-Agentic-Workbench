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

Entry point: python -m src.pipeline
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


def process_document(pdf_path: Path, config: PipelineConfig) -> None:
    """Process a single document through the full pipeline."""
    doc_id = pdf_path.stem
    start_time = time.time()

    console.print(f"\n[bold blue]{'=' * 60}[/]")
    console.print(f"[bold blue]Processing: {pdf_path.name}[/]")
    console.print(f"[bold blue]{'=' * 60}[/]")

    # Initialize checkpoint
    checkpoint = PipelineCheckpoint(config, doc_id)

    # Track results for report
    profile = None
    glossary = None
    normalized = None
    table_results = None
    validation_summary = None
    ref_report = None
    extraction_counts = {"entities": 0, "relationships": 0, "claims": 0}

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
    if not checkpoint.is_phase_complete(PipelinePhase.CHUNKING):
        console.print("\n[cyan]Phase 6: Chunking...[/]")
        try:
            from src.chunker import DocumentChunker
            chunker = DocumentChunker(config)
            chunks = chunker.chunk(normalized, table_results)
            checkpoint.mark_phase_complete(PipelinePhase.CHUNKING)
            console.print(f"  [OK] Created {len(chunks)} chunks")
        except Exception as e:
            console.print(f"  [red][FAIL] Chunking failed: {e}[/]")
            logger.exception("Chunking failed")
            return
    else:
        console.print("[dim]Phase 6: Chunking -- skipped (checkpoint)[/]")

    # -- Phase 7: Neo4j Setup ------------------------------------------
    memory = None
    try:
        from src.memory import Neo4jMemory
        memory = Neo4jMemory(config)
        memory.connect()
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

            with Progress(
                SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                BarColumn(), TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("Extracting...", total=len(chunks))

                for chunk in chunks:
                    if checkpoint.is_chunk_complete(chunk.chunk_id):
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
                        extraction = extractor.extract_chunk(
                            chunk, context, profile, glossary,
                        )

                        # Validate
                        validation = validator.validate(extraction, chunk)

                        # Insert into graph
                        if inserter and validation.passed:
                            counts = inserter.insert_extraction(extraction, validation)
                            extraction_counts["entities"] += counts["entities"]
                            extraction_counts["relationships"] += counts["relationships"]
                            extraction_counts["claims"] += counts["claims"]

                        # Update ontology
                        ontology_mgr.update_from_extraction(extraction)

                        checkpoint.mark_chunk_complete(chunk.chunk_id)

                    except Exception as e:
                        logger.error(f"Extraction error for {chunk.chunk_id}: {e}")

                    progress.advance(task)

                    # Memory management
                    gc.collect()

            ontology_mgr.increment_documents()
            ontology_mgr.save()
            extractor.close()

        checkpoint.mark_phase_complete(PipelinePhase.EXTRACTION)
        checkpoint.mark_phase_complete(PipelinePhase.VALIDATION)
        checkpoint.mark_phase_complete(PipelinePhase.GRAPH_INSERTION)

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


def main() -> None:
    """Main pipeline entry point."""
    console.print("[bold]Refinery Knowledge Layer[/] v0.1.0\n")

    config = load_config()
    setup_logging(config)

    pdfs = discover_documents(config)
    if not pdfs:
        return

    for pdf in pdfs:
        try:
            process_document(pdf, config)
        except Exception as e:
            console.print(f"[red]Fatal error processing {pdf.name}: {e}[/]")
            logger.exception(f"Fatal error: {pdf.name}")
            continue

    console.print("\n[bold green]Pipeline complete.[/]")


if __name__ == "__main__":
    main()
