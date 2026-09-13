"""Main pipeline orchestrator.

Workflow (parse -> clean -> structure -> chunk -> extract):
  1. Discover PDFs in data/raw/
  2. For each PDF:
     a. Parse with Docling                               (checkpoint)
     b. Classify tables                                  (checkpoint)
     c. Normalize: furniture removal, TOC-based chapter structure,
        procedure detection                              (checkpoint)
     d. Build document profile (structure, glossary sources, references,
        standing instructions)                           (checkpoint)
     e. Build glossary                                   (checkpoint)
     f. Chunk: canonical text once, typed chunks         (checkpoint)
     g. Neo4j: schema, document node, glossary terms     (checkpoint)
     h. Document graph: chapters, sections, procedures, cross references,
        standing instructions, referenced documents      (checkpoint)
     i. Embeddings (retrieval representation) -> Chunk nodes
     j. For each chunk:
        - deterministic passes: rule relationships, specification / prose claims,
          table claims (always, no LLM needed)
        - LLM passes on engineering chunks when Ollama is available
        - validate, insert, ontology update              (checkpoint per chunk)
     k. Generate report                                  (checkpoint)

Entry point: python -m src
Flags:
  --clean         Clear Neo4j and reset extraction checkpoint (preserves parsing/normalizing/chunking)
  --rebuild       Re-run everything after parsing (normalize, chunk, graph); clears Neo4j
  --retry-failed  Only retry previously failed chunks
  --max-pages N   Extract only chunks starting on pages 1..N (phase left open for resume)
  --no-llm        Deterministic passes only (rules, specification/prose/table claims, document graph)
"""

from __future__ import annotations

import gc
import io
import json
import logging
import sys
import time
from collections import defaultdict
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

CHUNK_FIELDS = (
    "chunk_id", "document_id", "sequence", "page_start", "page_end", "section_path", "parent_heading", "text",
    "contains_table", "table_ids", "contains_procedure", "procedure_ids", "element_types", "token_estimate",
    "overlap_text", "chunk_type", "is_engineering", "chapter_number", "section_id",
)


def setup_logging(config: PipelineConfig) -> None:
    """Configure structured logging."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
    from src.chunker import Chunk
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


def process_document(
    pdf_path: Path,
    config: PipelineConfig,
    clean: bool = False,
    retry_failed: bool = False,
    max_pages: int | None = None,
    rebuild: bool = False,
    no_llm: bool = False,
) -> None:
    """Process a single document through the full pipeline."""
    doc_id = pdf_path.stem
    start_time = time.time()

    console.print(f"\n[bold blue]{'=' * 60}[/]")
    console.print(f"[bold blue]Processing: {pdf_path.name}[/]")
    console.print(f"[bold blue]{'=' * 60}[/]")

    checkpoint = PipelineCheckpoint(config, doc_id)

    if rebuild:
        console.print("[yellow]  --rebuild: re-running every phase after parsing and clearing Neo4j data[/]")
        checkpoint.reset_after_parsing()
        clean = True
    elif clean:
        console.print("[yellow]  --clean: resetting extraction phase and Neo4j data[/]")
        checkpoint.reset_extraction_phase()

    profile = None
    glossary = None
    normalized = None
    table_results = None
    ref_report = None
    document_graph_counts: dict = {}
    extraction_counts = {"entities": 0, "relationships": 0, "claims": 0, "conflicts": 0, "corroborations": 0, "errors": 0}
    extraction_stats = {
        "total": 0, "success": 0, "empty": 0, "retry_succeeded": 0, "json_failed": 0, "error": 0,
        "skipped": 0, "deterministic_only": 0,
    }
    source_counts = defaultdict(lambda: {
        "relationships": {"accepted": 0, "rejected": 0, "weak": 0},
        "claims": {"accepted": 0, "rejected": 0, "weak": 0},
    })

    # -- Phase 1: Parse -----------------------------------------------
    parsed_doc = None
    if not checkpoint.is_phase_complete(PipelinePhase.PARSING):
        console.print("\n[cyan]Phase 1: Parsing with Docling...[/]")
        try:
            from src.parser import DocumentParser
            from src.parse_validation import validate_parsed_document, write_parse_report
            parser = DocumentParser(config)
            parsed_doc = parser.parse(pdf_path)

            validation = validate_parsed_document(parsed_doc)
            report_path = write_parse_report(
                parsed_doc, validation, parser.output_dir(doc_id) / "parse_report.md",
            )
            console.print(
                f"  [OK] Parsed: {len(parsed_doc.pages)}/{parsed_doc.total_pages} pages, "
                f"{len(parsed_doc.tables)} tables, "
                f"{sum(len(p.elements) for p in parsed_doc.pages)} elements "
                f"in {parsed_doc.parse_duration_seconds:.0f}s"
            )
            console.print(f"  Parse report: {report_path}")
            failed_checks = [c for c in validation.checks if not c["ok"] and c["severity"] == "error"]
            if not validation.passed:
                for c in failed_checks:
                    console.print(f"  [red][FAIL] {c['check']}: {c['detail']}[/]")
                console.print("[red]Parse validation failed; not proceeding to normalization.[/]")
                return
            for c in validation.checks:
                if not c["ok"]:
                    console.print(f"  [yellow][WARN] {c['check']}: {c['detail']}[/]")

            checkpoint.set_source_hash(parsed_doc.source_hash)
            checkpoint.mark_phase_complete(PipelinePhase.PARSING)
        except Exception as e:
            console.print(f"  [red][FAIL] Parse failed: {e}[/]")
            logger.exception("Parse failed")
            return
    else:
        console.print("[dim]Phase 1: Parsing -- skipped (checkpoint)[/]")
        from src.parser import load_parsed_document
        parsed_doc = load_parsed_document(config, doc_id)

    if not parsed_doc:
        console.print("[red]Cannot proceed without parsed document.[/]")
        return

    # -- Phase 2: Table Classification (before normalization: furniture tables are filtered by class) ---
    console.print("\n[cyan]Phase 2: Classifying tables...[/]")
    try:
        from src.table_classifier import TableClassifier
        from src.table_reconstructor import reconstruct_table
        table_results = TableClassifier(config).classify_all(parsed_doc)
        for table in table_results.classified_tables:
            if table.enters_extraction:
                reconstruct_table(table)
        checkpoint.mark_phase_complete(PipelinePhase.TABLE_CLASSIFICATION)
        console.print(
            f"  [OK] Classified {table_results.total_tables} tables: "
            + ", ".join(f"{k}={v}" for k, v in sorted(table_results.classification_summary.items()))
        )
    except Exception as e:
        console.print(f"  [red][FAIL] Table classification failed: {e}[/]")
        logger.exception("Table classification failed")
        table_results = None

    # -- Phase 3: Normalize (clean + structure + procedures) -----------
    norm_path = config.paths.normalized_dir / f"{doc_id}_normalized.json"
    if not checkpoint.is_phase_complete(PipelinePhase.NORMALIZING) or not norm_path.exists():
        console.print("\n[cyan]Phase 3: Normalizing (furniture removal, chapter structure, procedures)...[/]")
        try:
            from src.normalizer import DocumentNormalizer
            normalized = DocumentNormalizer(config).normalize(parsed_doc, table_results)
            checkpoint.mark_phase_complete(PipelinePhase.NORMALIZING)
            s = normalized.stats
            console.print(
                f"  [OK] Normalized: kept {s.kept_elements}/{s.total_elements} elements; "
                f"dropped figures={s.figure_placeholders_found} header boxes={s.repeated_headers_found} "
                f"running titles={s.running_titles_found} approvals={s.approval_blocks_found} toc={s.toc_entries_found}"
            )
            console.print(
                f"  [OK] Structure: {len(normalized.chapters)} chapters ({s.chapters_from_toc} from TOC), "
                f"{len(normalized.sections)} sections, {len(normalized.procedures)} procedures "
                f"({s.procedure_steps_found} steps)"
            )
        except Exception as e:
            console.print(f"  [red][FAIL] Normalization failed: {e}[/]")
            logger.exception("Normalization failed")
            return
    else:
        console.print("[dim]Phase 3: Normalizing -- skipped (checkpoint)[/]")
        from schemas.normalized_document import NormalizedDocument
        normalized = NormalizedDocument.model_validate_json(norm_path.read_text(encoding="utf-8"))
        if not normalized.chapters and not normalized.procedures:
            console.print("  [yellow]Normalized document predates the structure-aware normalizer; re-normalizing[/]")
            from src.normalizer import DocumentNormalizer
            normalized = DocumentNormalizer(config).normalize(parsed_doc, table_results)
            checkpoint.unmark_phase(PipelinePhase.CHUNKING)

    if not normalized:
        console.print("[red]Cannot proceed without normalized document.[/]")
        return

    # -- Phase 4: Document Profile -------------------------------------
    profile_path = config.paths.knowledge_dir / doc_id / "document_profile.json"
    if not checkpoint.is_phase_complete(PipelinePhase.DOCUMENT_PROFILE) or not profile_path.exists():
        console.print("\n[cyan]Phase 4: Building document profile...[/]")
        try:
            from src.document_profile import DocumentProfiler
            from schemas.table_schema import TableNormalizationResult
            if table_results is None:
                table_results = TableNormalizationResult(document_id=doc_id)
            profile = DocumentProfiler(config).build_profile(normalized, table_results, parsed_doc.tables)
            checkpoint.mark_phase_complete(PipelinePhase.DOCUMENT_PROFILE)
            console.print(
                f"  [OK] Profile: type={profile.document_type.value}, rev={profile.revision or '?'}, "
                f"{len(profile.chapters)} chapters, {len(profile.abbreviations)} abbreviations, "
                f"{len(profile.referenced_documents)} referenced documents, "
                f"{len(profile.cross_references)} cross references, "
                f"{len(profile.standing_instructions)} standing instructions"
            )
        except Exception as e:
            console.print(f"  [red][FAIL] Profile failed: {e}[/]")
            logger.exception("Profile failed")
    else:
        console.print("[dim]Phase 4: Document profile -- skipped (checkpoint)[/]")
        from schemas.document_profile import DocumentProfile
        profile = DocumentProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))

    # -- Phase 5: Glossary ---------------------------------------------
    glossary_path = config.paths.knowledge_dir / doc_id / "glossary.json"
    if not checkpoint.is_phase_complete(PipelinePhase.GLOSSARY) or not glossary_path.exists():
        console.print("\n[cyan]Phase 5: Building glossary...[/]")
        try:
            from src.glossary_builder import GlossaryBuilder
            from schemas.document_profile import DocumentProfile as DP
            if profile is None:
                profile = DP(document_id=doc_id, source_filename=pdf_path.name)
            glossary = GlossaryBuilder(config).build_glossary(normalized, profile)
            checkpoint.mark_phase_complete(PipelinePhase.GLOSSARY)
            console.print(f"  [OK] Glossary: {glossary.total_entries} entries")
        except Exception as e:
            console.print(f"  [red][FAIL] Glossary failed: {e}[/]")
            logger.exception("Glossary failed")
    else:
        console.print("[dim]Phase 5: Glossary -- skipped (checkpoint)[/]")
        from schemas.glossary import DocumentGlossary
        glossary = DocumentGlossary.model_validate_json(glossary_path.read_text(encoding="utf-8"))

    # -- Phase 6: Chunk ------------------------------------------------
    chunks = []
    chunks_path = config.paths.knowledge_dir / doc_id / "chunks.json"
    if not checkpoint.is_phase_complete(PipelinePhase.CHUNKING) or not chunks_path.exists():
        console.print("\n[cyan]Phase 6: Chunking (canonical text, typed chunks)...[/]")
        try:
            from src.chunker import DocumentChunker
            chunks = DocumentChunker(config).chunk(normalized, table_results)
            save_chunks(chunks, chunks_path)
            checkpoint.mark_phase_complete(PipelinePhase.CHUNKING)
            checkpoint.unmark_phase(PipelinePhase.EMBEDDING)   # chunk ids/text changed
            types = defaultdict(int)
            for c in chunks:
                types[c.chunk_type] += 1
            console.print(f"  [OK] Created {len(chunks)} chunks: " + ", ".join(f"{k}={v}" for k, v in sorted(types.items())))
        except Exception as e:
            console.print(f"  [red][FAIL] Chunking failed: {e}[/]")
            logger.exception("Chunking failed")
            return
    else:
        console.print("[dim]Phase 6: Chunking -- skipped (checkpoint)[/]")
        chunks = load_chunks(chunks_path, normalized)
        if chunks and "chunk_type" not in json.loads(chunks_path.read_text(encoding="utf-8"))[0]:
            console.print("  [yellow]Chunk cache predates typed chunks; re-chunking[/]")
            from src.chunker import DocumentChunker
            chunks = DocumentChunker(config).chunk(normalized, table_results)
            save_chunks(chunks, chunks_path)
            checkpoint.unmark_phase(PipelinePhase.EMBEDDING)
        console.print(f"  [dim]Loaded {len(chunks)} chunks from disk[/]")

    # -- Phase 7: Neo4j Setup ------------------------------------------
    memory = None
    try:
        from src.memory import Neo4jMemory
        memory = Neo4jMemory(config)
        memory.connect()

        if clean:
            console.print("  [yellow]clearing all Neo4j data...[/]")
            memory.clear_all_data()

        doc_in_graph = memory.document_exists(doc_id)
        chunks_in_graph = memory.count_chunks(doc_id) if doc_in_graph else 0
        if checkpoint.is_phase_complete(PipelinePhase.NEO4J_SETUP) and not doc_in_graph:
            console.print("  [yellow]Document node missing in this Neo4j database; redoing Neo4j setup[/]")
            checkpoint.unmark_phase(PipelinePhase.NEO4J_SETUP)
            checkpoint.unmark_phase(PipelinePhase.DOCUMENT_GRAPH)
        if checkpoint.is_phase_complete(PipelinePhase.EMBEDDING) and chunks_in_graph == 0:
            console.print("  [yellow]Chunk nodes missing in this Neo4j database; redoing chunk storage[/]")
            checkpoint.unmark_phase(PipelinePhase.EMBEDDING)

        if not checkpoint.is_phase_complete(PipelinePhase.NEO4J_SETUP):
            console.print("\n[cyan]Phase 7: Setting up Neo4j schema...[/]")
            memory.setup_schema()
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
            if glossary:
                import hashlib
                for entry in glossary.entries:
                    term_uid = hashlib.md5(f"{entry.term}|{doc_id}".encode()).hexdigest()[:16]
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

        # -- Phase 7a: Document graph (structure, procedures, references) ----
        if not checkpoint.is_phase_complete(PipelinePhase.DOCUMENT_GRAPH):
            console.print("\n[cyan]Phase 7a: Writing document graph (chapters, sections, procedures, references)...[/]")
            from src.document_graph import write_document_graph
            document_graph_counts = write_document_graph(
                memory, normalized, profile,
                plant=(profile.plant or None) if profile else None,
                unit=(profile.unit or profile.plant or None) if profile else None,
                unit_scoping=config.identity.unit_scoping,
            )
            checkpoint.mark_phase_complete(PipelinePhase.DOCUMENT_GRAPH)
            console.print("  [OK] Document graph: " + ", ".join(f"{k}={v}" for k, v in document_graph_counts.items()))
        else:
            console.print("[dim]Phase 7a: Document graph -- skipped (checkpoint)[/]")
    except Exception as e:
        console.print(f"  [yellow]WARNING: Neo4j not available: {e}[/]")
        msg = str(e).lower()
        if "unauthorized" in msg or "authentication" in msg or "credentials" in msg:
            console.print(
                "  [yellow]  Credentials rejected. The pipeline uses user "
                f"'{config.neo4j.username}' / password '{config.neo4j.password}' at {config.neo4j.uri}.\n"
                "  Set RKL_NEO4J_PASSWORD (and RKL_NEO4J_URI / RKL_NEO4J_USER if needed) to match your "
                "Neo4j instance, or set that instance's password to the value above.[/]"
            )
        elif "connect" in msg or "refused" in msg or "unavailable" in msg:
            console.print(
                f"  [yellow]  Nothing is listening at {config.neo4j.uri}. Start the Neo4j instance "
                "(scripts/start_neo4j.ps1 -Detached), but only one at a time: they share ports 7687/7474.[/]"
            )
        console.print("  [yellow]  Skipping graph-dependent phases (extraction results will not be stored).[/]")
        memory = None

    # -- Phase 7b: Embeddings -> Neo4j memory ---------------------------
    if chunks:
        console.print(
            f"\n[cyan]Phase 7b: Embedding {len(chunks)} chunks "
            f"({config.embedding.model_name}, {config.embedding.device})...[/]"
        )
        try:
            from src.embedder import ChunkEmbedder, store_chunks_in_memory
            embedder = ChunkEmbedder(config)
            vectors = embedder.embed_chunks(doc_id, chunks)
            console.print(f"  [OK] {len(vectors)} chunk embeddings ready (cached on disk)")
            if memory and checkpoint.is_phase_complete(PipelinePhase.EMBEDDING) and memory.count_embedded_chunks(doc_id) == 0:
                console.print("  [yellow]Chunk nodes carry no embeddings (vector index was recreated); re-storing[/]")
                checkpoint.unmark_phase(PipelinePhase.EMBEDDING)
            if memory and not checkpoint.is_phase_complete(PipelinePhase.EMBEDDING):
                written = store_chunks_in_memory(memory, doc_id, chunks)
                checkpoint.mark_phase_complete(PipelinePhase.EMBEDDING)
                console.print(f"  [OK] {written} Chunk nodes with embeddings stored in Neo4j")
            elif memory:
                console.print("  [dim]Chunk nodes already stored in Neo4j (checkpoint)[/]")
        except Exception as e:
            console.print(f"  [yellow]WARNING: Embedding step failed: {e}[/]")
            logger.exception("Embedding failed")

    # -- Phase 8: Extraction + Validation + Graph Insertion ------------
    if chunks and not checkpoint.is_phase_complete(PipelinePhase.EXTRACTION):
        console.print(f"\n[cyan]Phase 8: Extracting from {len(chunks)} chunks...[/]")

        extractor = None
        if no_llm:
            console.print("  [yellow]--no-llm: deterministic passes only (rules, specification/prose/table claims)[/]")
        else:
            try:
                from src.extractor import OllamaExtractor
                extractor = OllamaExtractor(config)
                if not extractor.check_model_available():
                    console.print("  [yellow]WARNING: Ollama/model not available; running deterministic passes only.[/]")
                    extractor = None
            except Exception as e:
                console.print(f"  [yellow]WARNING: Extractor init failed: {e}; running deterministic passes only.[/]")
                extractor = None

        from schemas.document_profile import DocumentProfile as DP
        from schemas.knowledge import ChunkExtraction
        from src.entity_resolver import EntityResolver
        from src.graph import GraphInserter
        from src.ontology_manager import OntologyManager
        from src.retriever import MemoryRetriever, RetrievalContext
        from src.validator import ExtractionValidator

        if profile is None:
            profile = DP(document_id=doc_id, source_filename=pdf_path.name)

        validator = ExtractionValidator(config, memory)
        resolver = EntityResolver(
            config, memory, glossary,
            plant=(profile.plant or None), unit=(profile.unit or profile.plant or None),
        )
        inserter = GraphInserter(memory, resolver) if memory else None
        retriever = MemoryRetriever(memory, glossary) if memory else None
        ontology_mgr = OntologyManager(config)

        parsed_tables_by_id = {t.table_id: t for t in parsed_doc.tables}
        table_class_by_id = {t.table_id: t.classification.value for t in table_results.classified_tables} \
            if table_results is not None else {}
        table_contexts: dict = {}
        try:
            from src.table_context import build_table_contexts
            table_contexts = build_table_contexts(normalized, parsed_tables_by_id)
            logger.info(f"Table contexts: {len(table_contexts)} tables, "
                        f"{sum(1 for c in table_contexts.values() if c.continuation_of)} continuations")
        except Exception as e:
            logger.warning(f"Table context derivation skipped: {e}")

        if retry_failed:
            failed_ids = {f["chunk_id"] if isinstance(f, dict) else f for f in checkpoint.get_failed_chunks()}
            chunks_to_process = [c for c in chunks if c.chunk_id in failed_ids]
            console.print(f"  [yellow]--retry-failed: retrying {len(chunks_to_process)} previously failed chunks[/]")
        else:
            chunks_to_process = chunks
        if max_pages is not None:
            chunks_to_process = [c for c in chunks_to_process if c.page_start <= max_pages]
            console.print(f"  [yellow]--max-pages {max_pages}: extracting only {len(chunks_to_process)} chunks "
                          f"starting on pages 1-{max_pages}[/]")

        def merge_rels(extraction, new_rels) -> int:
            existing = {(r.subject.lower(), r.predicate.value, r.object.lower()) for r in extraction.relationships}
            added = 0
            for r in new_rels:
                key = (r.subject.lower(), r.predicate.value, r.object.lower())
                if key not in existing:
                    extraction.relationships.append(r)
                    existing.add(key)
                    added += 1
            return added

        def merge_claims(extraction, new_claims) -> int:
            def key_of(c):
                return (c.subject.lower(), c.context_key(), c.value.lower(), c.unit.lower())
            existing = {key_of(c) for c in extraction.claims}
            added = 0
            for c in new_claims:
                k = key_of(c)
                if k not in existing:
                    extraction.claims.append(c)
                    existing.add(k)
                    added += 1
            return added

        def deterministic_passes(chunk, extraction, context) -> None:
            """Rule relationships, specification / prose claims and table claims (no LLM)."""
            if config.ollama.rule_relations:
                from src.rule_relations import extract_rule_relationships
                known = [e.name for e in extraction.entities] + \
                        [e.canonical_name for e in extraction.entities if e.canonical_name] + \
                        [k.get("name", "") for k in context.known_identities]
                if glossary:
                    known += [g.term for g in glossary.entries] + [g.full_form for g in glossary.entries if g.full_form]
                merge_rels(extraction, extract_rule_relationships(
                    chunk.text, chunk.chunk_id, doc_id, chunk.page_start, chunk.section_path, known_names=known,
                ))
            if chunk.is_engineering:
                from src.spec_claims import extract_prose_claims, extract_spec_claims
                if chunk.elements:
                    merge_claims(extraction, extract_spec_claims(
                        chunk.elements, chunk.chunk_id, doc_id, chunk.section_path, chunk.parent_heading,
                    ))
                merge_claims(extraction, extract_prose_claims(
                    chunk.text, chunk.chunk_id, doc_id, chunk.section_path, chunk.page_start, chunk.parent_heading,
                ))
            if chunk.table_ids and parsed_tables_by_id and chunk.is_engineering:
                from src.table_claims import extract_table_claims
                table_claims = []
                for tid in dict.fromkeys(chunk.table_ids):
                    tbl = parsed_tables_by_id.get(tid)
                    if tbl is None:
                        continue
                    ctx = table_contexts.get(tid)
                    inherit = (parsed_tables_by_id.get(ctx.continuation_of)
                               if ctx is not None and ctx.continuation_of else None)
                    table_claims.extend(extract_table_claims(
                        tbl, chunk.chunk_id, doc_id, chunk.section_path,
                        subject_hint=chunk.parent_heading or None,
                        classification=table_class_by_id.get(tid),
                        context=ctx, inherit_from=inherit,
                    ))
                merge_claims(extraction, table_claims)

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

                context = RetrievalContext()
                if retriever:
                    try:
                        context = retriever.retrieve_context(chunk)
                    except Exception as e:
                        logger.debug(f"Retrieval error: {e}")

                use_llm = (
                    extractor is not None
                    and chunk.chunk_type not in config.ollama.skip_chunk_types
                    and (chunk.is_engineering or not config.ollama.llm_only_engineering)
                )

                try:
                    if use_llm:
                        result = extractor.extract_chunk(chunk, context, profile, glossary)
                        status_key = {
                            ExtractionStatus.SUCCESS: "success", ExtractionStatus.EMPTY: "empty",
                            ExtractionStatus.RETRY_SUCCEEDED: "retry_succeeded",
                            ExtractionStatus.JSON_FAILED: "json_failed", ExtractionStatus.ERROR: "error",
                        }.get(result.status)
                        if status_key:
                            extraction_stats[status_key] += 1
                        if result.is_failure:
                            # The LLM failed, but the deterministic passes still contribute
                            extraction = ChunkExtraction(
                                chunk_id=chunk.chunk_id, document_id=doc_id, page_start=chunk.page_start,
                                page_end=chunk.page_end, section=chunk.section_path, model_name="deterministic",
                            )
                            checkpoint.mark_chunk_failed(chunk.chunk_id, result.failure_reason)
                        else:
                            extraction = result.extraction
                            if (
                                config.ollama.relationship_pass
                                and len(extraction.entities) >= config.ollama.relationship_pass_min_entities
                            ):
                                rels2, claims2 = extractor.extract_relationships(chunk, extraction.entities, context, profile)
                                merge_rels(extraction, rels2)
                                merge_claims(extraction, claims2)
                        llm_failed = result.is_failure
                    else:
                        extraction_stats["deterministic_only"] += 1
                        extraction = ChunkExtraction(
                            chunk_id=chunk.chunk_id, document_id=doc_id, page_start=chunk.page_start,
                            page_end=chunk.page_end, section=chunk.section_path, model_name="deterministic",
                        )
                        llm_failed = False

                    deterministic_passes(chunk, extraction, context)

                    validation = validator.validate(extraction, chunk)
                    rejected = {i.item_id for i in validation.issues if i.severity.value == "error"}
                    for r in extraction.relationships:
                        key = "accepted" if r.relationship_id not in rejected else "rejected"
                        source_counts[r.source]["relationships"][key] += 1
                        if r.relationship_id not in rejected and r.grounding == "weak":
                            source_counts[r.source]["relationships"]["weak"] += 1
                    for c in extraction.claims:
                        key = "accepted" if c.claim_id not in rejected else "rejected"
                        source_counts[c.source]["claims"][key] += 1
                        if c.claim_id not in rejected and c.grounding == "weak":
                            source_counts[c.source]["claims"]["weak"] += 1
                    logger.info(
                        f"{chunk.chunk_id} [{chunk.chunk_type}]: entities={len(extraction.entities)} "
                        f"rels(llm1/llm2/rule)="
                        f"{sum(1 for r in extraction.relationships if r.source == 'llm_pass1')}/"
                        f"{sum(1 for r in extraction.relationships if r.source == 'llm_pass2')}/"
                        f"{sum(1 for r in extraction.relationships if r.source == 'rule')} "
                        f"claims(llm1/llm2/rule/table)="
                        f"{sum(1 for c in extraction.claims if c.source == 'llm_pass1')}/"
                        f"{sum(1 for c in extraction.claims if c.source == 'llm_pass2')}/"
                        f"{sum(1 for c in extraction.claims if c.source == 'rule')}/"
                        f"{sum(1 for c in extraction.claims if c.source == 'table')} "
                        f"rejected={len(rejected)}"
                    )

                    if inserter:
                        counts = inserter.insert_extraction(extraction, validation, chunk_embedding=chunk.embedding)
                        for k in ("entities", "relationships", "claims", "conflicts", "corroborations", "errors"):
                            extraction_counts[k] += counts.get(k, 0)

                    ontology_mgr.update_from_extraction(extraction)
                    if not llm_failed:
                        checkpoint.mark_chunk_complete(chunk.chunk_id)

                except Exception as e:
                    logger.error(f"Extraction error for {chunk.chunk_id}: {e}")
                    logger.debug("Extraction error detail", exc_info=True)
                    checkpoint.mark_chunk_failed(chunk.chunk_id, str(e))
                    extraction_stats["error"] += 1

                progress.advance(task)
                gc.collect()

        ontology_mgr.increment_documents()
        ontology_mgr.save()
        if extractor is not None:
            extractor.close()

        _print_extraction_summary(extraction_stats, extraction_counts, checkpoint)
        console.print("\n[bold]Recall by source[/] (accepted / rejected / weak-grounded)")
        for src in ("llm_pass1", "llm_pass2", "rule", "table"):
            sc = source_counts.get(src)
            if not sc:
                continue
            r, c = sc["relationships"], sc["claims"]
            console.print(
                f"  {src:10s} relationships {r['accepted']}/{r['rejected']}/{r['weak']}   "
                f"claims {c['accepted']}/{c['rejected']}/{c['weak']}"
            )
        try:
            (config.paths.reports_dir / doc_id).mkdir(parents=True, exist_ok=True)
            (config.paths.reports_dir / doc_id / "extraction_by_source.json").write_text(
                json.dumps({k: v for k, v in source_counts.items()}, indent=2), encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"Could not write extraction_by_source.json: {e}")

        if max_pages is None and not retry_failed:
            checkpoint.mark_phase_complete(PipelinePhase.EXTRACTION)
            checkpoint.mark_phase_complete(PipelinePhase.VALIDATION)
            checkpoint.mark_phase_complete(PipelinePhase.GRAPH_INSERTION)
        else:
            console.print("  [dim]Partial extraction run: extraction phase left open for resume[/]")
    elif not chunks:
        console.print("[dim]Phase 8: No chunks to process[/]")
    else:
        console.print("[dim]Phase 8: Extraction -- skipped (checkpoint)[/]")

    checkpoint.flush()

    # -- Phase 9: Report -----------------------------------------------
    console.print("\n[cyan]Phase 9: Generating report...[/]")
    try:
        from src.reporter import ReportGenerator
        from src.reference_tracker import ReferenceTracker

        ref_tracker = ReferenceTracker(config)
        if profile:
            ref_tracker.add_profile_references(profile)
        ref_report = ref_tracker.generate_report()

        report_path = ReportGenerator(config).generate_document_report(
            doc_id=doc_id,
            profile=profile,
            glossary=glossary,
            normalized=normalized,
            table_results=table_results,
            ref_report=ref_report,
            processing_time=time.time() - start_time,
            extraction_counts=extraction_counts,
            chunks=chunks,
            source_counts={k: v for k, v in source_counts.items()},
            document_graph_counts=document_graph_counts,
            extraction_stats=extraction_stats,
        )
        checkpoint.mark_phase_complete(PipelinePhase.REPORTING)
        if checkpoint.is_phase_complete(PipelinePhase.EXTRACTION):
            checkpoint.mark_phase_complete(PipelinePhase.COMPLETE)
        console.print(f"  [OK] Report: {report_path}")
    except Exception as e:
        console.print(f"  [red][FAIL] Report failed: {e}[/]")
        logger.exception("Report failed")

    if memory:
        memory.close()

    elapsed = time.time() - start_time
    console.print(f"\n[bold green][DONE] {pdf_path.name} completed in {elapsed:.1f}s[/]")
    console.print(
        f"  Entities: {extraction_counts['entities']}, "
        f"Relations: {extraction_counts['relationships']}, "
        f"Claims: {extraction_counts['claims']} "
        f"(potential conflicts: {extraction_counts['conflicts']}, corroborations: {extraction_counts['corroborations']})"
    )


def _print_extraction_summary(stats: dict, counts: dict, checkpoint: PipelineCheckpoint) -> None:
    """Print a detailed extraction summary."""
    console.print("\n[bold]Extraction Summary[/]")
    console.print(f"  Total chunks processed: {stats['total']}")
    console.print(f"  LLM successful:         {stats['success']}")
    console.print(f"  LLM empty:              {stats['empty']}")
    console.print(f"  LLM retry succeeded:    {stats['retry_succeeded']}")
    console.print(f"  LLM JSON parse failed:  {stats['json_failed']}")
    console.print(f"  Other errors:           {stats['error']}")
    console.print(f"  Deterministic only:     {stats['deterministic_only']}")
    console.print(f"  Skipped (checkpoint):   {stats['skipped']}")
    console.print()
    console.print("  Graph insertions:")
    console.print(f"    Entities:            {counts['entities']}")
    console.print(f"    Claims:              {counts['claims']}")
    console.print(f"    Relationships:       {counts['relationships']}")
    console.print(f"    Potential conflicts: {counts.get('conflicts', 0)}  (same subject + context key, different value)")
    console.print(f"    Corroborations:      {counts.get('corroborations', 0)}  (same subject + context key, same value)")
    console.print(f"    Insert errors:       {counts.get('errors', 0)}")

    failed = checkpoint.get_failed_chunks()
    if failed:
        console.print(f"\n  [yellow]Failed chunks ({len(failed)}) -- will retry on next run:[/]")
        for f in failed[:10]:
            chunk_id = f["chunk_id"] if isinstance(f, dict) else f
            reason = f.get("reason", "unknown") if isinstance(f, dict) else "unknown"
            console.print(f"    - {chunk_id}: {reason[:80]}")
        if len(failed) > 10:
            console.print(f"    ... and {len(failed) - 10} more")


def main() -> None:
    """Main pipeline entry point."""
    console.print("[bold]Refinery Knowledge Layer[/] v0.3.0\n")

    config = load_config()
    setup_logging(config)

    clean = "--clean" in sys.argv
    rebuild = "--rebuild" in sys.argv
    retry_failed = "--retry-failed" in sys.argv
    no_llm = "--no-llm" in sys.argv
    max_pages = None
    if "--max-pages" in sys.argv:
        max_pages = int(sys.argv[sys.argv.index("--max-pages") + 1])
        console.print(f"[yellow]--max-pages {max_pages}: extraction limited to chunks starting on pages 1-{max_pages}[/]")

    if rebuild:
        console.print("[yellow]Running in --rebuild mode: re-running everything after parsing, clearing Neo4j[/]")
    elif clean:
        console.print("[yellow]Running in --clean mode: will reset extraction + Neo4j[/]")
    if retry_failed:
        console.print("[yellow]Running in --retry-failed mode: only retrying failed chunks[/]")
    if no_llm:
        console.print("[yellow]Running in --no-llm mode: deterministic extraction only[/]")

    pdfs = discover_documents(config)
    if not pdfs:
        return

    for pdf in pdfs:
        try:
            process_document(pdf, config, clean=clean, retry_failed=retry_failed, max_pages=max_pages,
                             rebuild=rebuild, no_llm=no_llm)
        except Exception as e:
            console.print(f"[red]Fatal error processing {pdf.name}: {e}[/]")
            logger.exception(f"Fatal error: {pdf.name}")
            continue

    console.print("\n[bold green]Pipeline complete.[/]")


if __name__ == "__main__":
    main()
