# Refinery Knowledge Layer — Implementation Walkthrough

## What Was Built

A complete source-grounded refinery engineering knowledge extraction system — **36 files** across 5 packages, all verified with **49 passing tests**.

### Architecture

```
PDF → Parser → Normalizer → Table Classifier → Document Profiler → Glossary Builder
                                                        ↓
                        Chunker → Memory Retriever → LLM Extractor → Validator → Graph Inserter
                                                        ↓
                                    Neo4j Knowledge Graph + Reports + Ontology
```

---

## Project Structure

### Schemas (9 files)
| File | Purpose |
|------|---------|
| [`parsed_document.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/parsed_document.py) | Layer 1: Raw parsed elements, pages, tables |
| [`normalized_document.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/normalized_document.py) | Layer 2: Cleaned content with section hierarchy |
| [`knowledge.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/knowledge.py) | Layer 3: 130+ entity types, 40+ relationship types, extraction output |
| [`claims.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/claims.py) | Engineering claims with unit validation rules |
| [`table_schema.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/table_schema.py) | 18-type table classification + structure |
| [`document_profile.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/document_profile.py) | Document orientation metadata |
| [`glossary.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/glossary.py) | Terminology with lookup maps |
| [`validation.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/validation.py) | 5-category validation results |
| [`ontology.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/schemas/ontology.py) | Corpus-driven evolving ontology |

### Source Modules (18 files)
| File | Purpose |
|------|---------|
| [`config.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/knowledge_layer/config.py) | Central configuration with VRAM-safe defaults |
| [`parser.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/parser.py) | Docling PDF parser with checkpointing |
| [`normalizer.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/normalizer.py) | Deterministic boilerplate removal |
| [`table_classifier.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/table_classifier.py) | Regex-based table type classification |
| [`table_reconstructor.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/table_reconstructor.py) | Merged-cell propagation + dedup |
| [`document_profile.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/document_profile.py) | Deterministic document orientation |
| [`glossary_builder.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/glossary_builder.py) | Abbreviation extraction (table + inline) |
| [`chunker.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/chunker.py) | Structure-aware chunking |
| [`memory.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/memory.py) | Neo4j graph operations + vector search |
| [`retriever.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/retriever.py) | Targeted graph context retrieval |
| [`extractor.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/knowledge_layer/extractor.py) | Constrained LLM extraction via Ollama |
| [`validator.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/validator.py) | 5-layer validation (schema, evidence, unit, domain, contradiction) |
| [`graph.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/graph.py) | Idempotent Neo4j graph insertion |
| [`provenance.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/provenance.py) | Source provenance tracking |
| [`ontology_manager.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/ontology_manager.py) | Corpus-driven ontology evolution |
| [`reference_tracker.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/reference_tracker.py) | Cross-document reference tracking |
| [`reporter.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/reporter.py) | Markdown report generation |
| [`checkpoint.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/checkpoint.py) | Phase + chunk-level checkpointing |
| [`pipeline.py`](file:///c:/Users/pandi/Documents/AyushCodes/Refinery_KL_Extraction/src/pipeline.py) | Main 9-phase pipeline orchestrator |

---

## Test Results

```
============================= 49 passed in 0.29s ==============================
```

### Anti-Hallucination Regressions
- ATF = "Aviation Turbine Fuel" (not invented by model)
- Pressure claims with °C units → **rejected**
- Co-occurrence does NOT create FEEDS relationships
- Motor cannot have SUCTION_FROM
- Entities without evidence → **rejected**

### Smoke Test (no external services)
```
[OK] All schemas import and validate
[OK] Created: 3 pages, 1 tables
[OK] Kept 5/6 elements
[OK] t1: abbreviation_table (confidence: 0.90)
[OK] Type: process_description
[OK] ATF = Aviation Turbine Fuel (correct!)
[OK] Created 1 chunks
[OK] Pressure claim with degC unit correctly rejected!
ALL SMOKE TESTS PASSED (0.2s)
```

---

## Key Design Decisions

1. **No LLM for normalization/classification** — Phases 1-7 are 100% deterministic (regex + heuristics)
2. **Evidence-first extraction** — Every entity, relationship, and claim requires a source quote
3. **Unit validation rules** — Hardcoded `CLAIM_UNIT_RULES` map prevents impossible combinations
4. **Checkpoint at every phase** — Interrupted processing resumes exactly where it stopped
5. **Graceful degradation** — Pipeline works without Ollama/Neo4j (skips graph + extraction phases)
6. **Article stripping** — "The Vacuum Distillation Unit" → "Vacuum Distillation Unit"
7. **VRAM-safe defaults** — 4096 context window, CPU embeddings, no concurrent GPU tasks

---

## Next Steps (User Action Required)

1. **Install Ollama**: Download from [ollama.com](https://ollama.com), then:
   ```
   ollama pull deepseek-r1:7b
   ```

2. **Install Neo4j**: Download Neo4j Community/Desktop, start it, set password to `refinery2024` (or override via `RKL_NEO4J_PASSWORD` env var)

3. **Place PDFs**: Copy refinery documents to `data/raw/`

4. **Run the pipeline**:
   ```
   python -m knowledge_layer
   ```


Based on the code in src/pipeline.py, here is what is happening, the expected output, and how failures are handled.

1. What is currently happening?
When you run python -m knowledge_layer, the pipeline starts up and acts as an orchestrator for processing PDF documents. Here is the exact sequence of events:

Initialization: It loads the configuration and sets up structured logging using the rich library.
Discovery: It scans the data/raw/ directory for any .pdf files.
Document Processing: For each PDF found, it runs through a 9-phase processing pipeline.
Phase 1-5: Parses the document using Docling, normalizes it, classifies tables, builds a document profile, and builds a glossary.
Phase 6: Chunks the document into smaller pieces.
Phase 7: Connects to a Neo4j database, sets up the schema, and inserts the document and glossary terms (if Neo4j is available).
Phase 8 (Extraction): It initializes an OllamaExtractor (which seems to use DeepSeek-R1 based on the file's docstring). For each chunk of the document, it retrieves context, asks the model to extract data (entities, relationships, claims), validates the extraction, and inserts the valid data into Neo4j.
Phase 9: Generates a final document report.
Checkpointing: Throughout these phases, the pipeline heavily utilizes a checkpoint system (PipelineCheckpoint). It checks if a phase or a specific text chunk has already been completed; if so, it skips it and loads the data from disk to save time.
2. What output should I expect?
Because the pipeline uses the rich console library, you can expect a nicely formatted, colored terminal output. Specifically, you will see:

A startup header: Refinery Knowledge Layer v0.1.0
A summary of documents found, e.g., Found 1 document(s) to process followed by the filename and its size in MB. (Or a yellow warning if no PDFs are found).
A blue banner indicating the start of a specific document being processed.
Step-by-step cyan status messages for each phase (e.g., Phase 1: Parsing with Docling...).
If a phase finishes successfully, you will see an [OK] message with stats (e.g., [OK] Parsed: X pages, Y tables).
If a phase was previously completed, you will see a dimmed message like [dim]Phase 1: Parsing -- skipped (checkpoint)[/].
During Phase 8 (Model Extraction): You will see a live progress bar (with a spinner) showing the extraction progress chunk by chunk (e.g., Extracting... 5/20).
Potential yellow [WARNING] messages if Neo4j or the Ollama model is unavailable.
A final green [DONE] message for the document showing the total time elapsed and the total number of Entities, Relations, and Claims extracted.
A final Pipeline complete. message at the very end.
3. How is the model made sure not to fail?
The code is written with several layers of fault tolerance specifically around the model extraction and the broader pipeline:

Availability Check: Before attempting to extract anything, it calls extractor.check_model_available(). If the Ollama model is offline or unavailable, it prints a warning and gracefully skips Phase 8 entirely rather than crashing.
Per-Chunk Try/Catch: The model extraction loop (iterating over the document chunks) wraps the extraction, validation, and graph insertion steps in a try...except Exception as e: block. If the model fails on a specific chunk (e.g., a timeout or malformed output), it logs the error but safely continues to the next chunk without crashing the pipeline.
Phase & Document Isolation: Every major phase is wrapped in a try/except block. If a phase fails entirely, it prints [FAIL] and stops processing that specific document, but the main() loop will catch it and continue processing the next PDF in the queue.
Resilient Checkpointing: Because of PipelineCheckpoint, if the model or pipeline does crash midway through Phase 8, you can just rerun python -m knowledge_layer. The system will automatically skip all completed phases and already-extracted chunks, resuming exactly where it left off.
Output Validation: To prevent the model from failing the database insertion by hallucinating bad JSON/data, the model's output is passed through an ExtractionValidator. It is only inserted into the Neo4j graph if validation.passed is true.
Memory Management: The code explicitly calls gc.collect() (garbage collection) after every single chunk is processed to prevent the script from running out of memory during long model extraction runs.