"""Central configuration for the Refinery Knowledge Layer.

All paths, model settings, thresholds, and resource limits are defined here.
No magic numbers scattered through the codebase.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


# Project root (where pyproject.toml lives)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class PathConfig(BaseModel):
    """All data paths relative to project root."""
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    raw_dir: Path = PROJECT_ROOT / "data" / "raw"
    parsed_dir: Path = PROJECT_ROOT / "data" / "parsed"
    normalized_dir: Path = PROJECT_ROOT / "data" / "normalized"
    knowledge_dir: Path = PROJECT_ROOT / "data" / "knowledge"
    checkpoints_dir: Path = PROJECT_ROOT / "data" / "checkpoints"
    reports_dir: Path = PROJECT_ROOT / "data" / "reports"
    prompts_dir: Path = PROJECT_ROOT / "knowledge_layer" / "prompts"

    def ensure_dirs(self) -> None:
        """Create all data directories if they don't exist."""
        for d in [
            self.raw_dir, self.parsed_dir, self.normalized_dir,
            self.knowledge_dir, self.checkpoints_dir, self.reports_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


class ParserSettings(BaseModel):
    """Docling parser configuration."""
    ocr_enabled: bool = True
    ocr_engine: str = Field(default="rapidocr", description="rapidocr, easyocr, or tesseract")
    ocr_languages: list[str] = Field(default_factory=lambda: ["english"])
    ocr_force_full_page: bool = Field(
        default=False,
        description="Force OCR of whole pages (only needed for scanned PDFs). "
                    "False = OCR only bitmap regions, keep the PDF text layer.",
    )
    table_structure_mode: str = Field(default="accurate", description="accurate or fast")
    table_cell_matching: bool = Field(
        default=True,
        description="Match TableFormer cells back to PDF text cells (better text fidelity)",
    )
    page_window_size: int = Field(
        default=15,
        description="Pages per parsing window for memory safety (GTX 1650 4GB / ~12GB RAM)",
    )
    max_pages: int | None = Field(default=None, description="Limit pages for testing; None = all")
    device: str = Field(
        default="auto",
        description="Accelerator for layout/TableFormer models: auto, cuda, cpu",
    )
    num_threads: int = Field(default=8, description="CPU threads for model inference")
    generate_picture_images: bool = Field(
        default=True, description="Export figure crops to data/parsed/<doc>/images/",
    )
    images_scale: float = Field(default=2.0, description="Render scale for exported figure images (1.0 = 72 dpi)")
    formula_enrichment: bool = Field(
        default=False,
        description="Run the CodeFormula VLM on formula regions (heavy; off for 4GB VRAM)",
    )
    save_raw_docling_json: bool = Field(
        default=True,
        description="Persist Docling's native export_to_dict() per window (complete representation)",
    )


class NormalizerSettings(BaseModel):
    """Normalizer thresholds."""
    repeated_element_threshold: float = Field(
        default=0.6,
        description="Fraction of pages an element must appear on to be considered repeated",
    )
    min_content_length: int = Field(
        default=5,
        description="Minimum character length for an element to be kept",
    )
    boilerplate_patterns: list[str] = Field(
        default_factory=lambda: [
            "OPERATING MANUAL",
            "Chapter No",
            "Chapter Rev No",
            "Page No",
            "PLANT NO",
            "PLANT NAME",
            "Revision",
            "Doc. No",
            "Document No",
            "CONFIDENTIAL",
            "PROPRIETARY",
        ],
        description="Text patterns commonly found in page headers/footers (matched only on short lines)",
    )
    procedure_min_steps: int = Field(default=3, description="Minimum list items for a procedure block")
    procedure_min_imperative_fraction: float = Field(
        default=0.5, description="Fraction of items that must read as instructions when no procedure heading introduces them",
    )


class OllamaSettings(BaseModel):
    """Ollama / DeepSeek-R1 configuration."""
    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="deepseek-r1:7b")
    num_ctx: int = Field(
        default=8192,
        description="Context window size. Must hold prompt (~3k tokens) + JSON output; "
                    "KV cache for 8k on the 7B model is ~0.5 GB (GTX 1650 4GB offloads partially).",
    )
    temperature: float = Field(
        default=0.1,
        description="Near-deterministic for factual extraction.",
    )
    num_predict: int = Field(
        default=3072,
        description="Maximum output tokens for the JSON extraction of one chunk. Entity-dense prose "
                    "chunks (~20 entities with evidence sentences) exceed 2048 and were truncated, "
                    "losing the relationships/claims arrays that follow the entities in the JSON.",
    )
    think: bool = Field(
        default=False,
        description="Enable DeepSeek-R1 chain-of-thought. Off: JSON grammar is applied directly "
                    "(much faster on a 4GB GPU).",
    )
    timeout_seconds: int = Field(
        default=1800,
        description="Request timeout. deepseek-r1:7b (4.7 GB) does not fit a 4 GB GPU and generates at ~3 tokens/s "
                    "in the CPU/GPU split, so a 3072-token answer needs ~18 min; a model that fits the GPU "
                    "(RKL_OLLAMA_MODEL=qwen3:4b) is ~10x faster.",
    )
    relationship_pass: bool = Field(
        default=True,
        description="Run a second, relationship-only LLM pass when a chunk yielded >= 2 entities "
                    "but the model proposed no relationships",
    )
    relationship_pass_min_entities: int = Field(default=2)
    rule_relations: bool = Field(
        default=True,
        description="Deterministic routing/composition rules (exact-line evidence) before the LLM passes",
    )
    max_retries: int = Field(default=2)
    skip_chunk_types: list[str] = Field(
        default_factory=lambda: ["document_control", "toc"],
        description="Chunk types never sent to the LLM (deterministic passes still run on them)",
    )
    llm_only_engineering: bool = Field(
        default=True, description="Send only engineering chunks (not document-control content) to the LLM",
    )


class Neo4jSettings(BaseModel):
    """Neo4j connection configuration."""
    uri: str = Field(default="bolt://localhost:7687")
    username: str = Field(default="neo4j")
    password: str = Field(default="refinery2024")
    database: str = Field(default="neo4j")


class EmbeddingSettings(BaseModel):
    """Embedding model configuration."""
    model_name: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Sentence transformer model for chunk embeddings (384-dim, drop-in for all-MiniLM-L6-v2 "
                    "with stronger retrieval on technical text; the vector index is recreated automatically "
                    "if the dimension changes)",
    )
    dimensions: int = Field(default=384)
    device: str = Field(default="cpu", description="cpu to avoid VRAM contention")
    batch_size: int = Field(default=32)


class ChunkerSettings(BaseModel):
    """Structure-aware chunker configuration."""
    target_tokens: int = Field(default=800, description="Target tokens per chunk")
    max_tokens: int = Field(default=1200, description="Maximum tokens per chunk")
    overlap_sentences: int = Field(
        default=2,
        description="Sentences from previous chunk included as context bridge",
    )


class ValidationSettings(BaseModel):
    """Validation layer configuration."""
    evidence_similarity_threshold: float = Field(
        default=0.8,
        description="Minimum fuzzy match ratio for evidence text in source chunk",
    )
    enable_cross_check: bool = Field(
        default=True,
        description="Whether to cross-check important chunks",
    )
    cross_check_table_types: list[str] = Field(
        default_factory=lambda: [
            "equipment_table",
            "material_balance_table",
            "operating_limit_table",
            "process_data_table",
            "safety_table",
        ],
        description="Table types that trigger cross-checking",
    )


class IdentitySettings(BaseModel):
    """Global entity identity / cross-document linking."""
    unit_scoping: bool = Field(
        default=True,
        description="Prefix tags that have no plant-number prefix with the document's unit slug",
    )
    tag_patterns: list[str] = Field(
        default_factory=lambda: [
            r"^(?:(?P<plant>\d{1,3})-)?(?P<prefix>[A-Z]{1,4})-(?P<number>\d{1,5})(?P<suffix>[A-Z](?:/[A-Z])*)?$",
        ],
        description="Regexes (named groups plant/prefix/number/suffix) that define a canonical tag",
    )
    same_as_threshold: float = Field(
        default=0.8,
        description="Minimum confidence for creating a SAME_AS link between untagged entities",
    )
    name_match_confidence: float = Field(default=0.85, description="Score for an exact name/canonical_name match")
    glossary_match_confidence: float = Field(default=0.9, description="Score for a glossary term/abbreviation match")
    vector_candidates: int = Field(default=5, description="Similar chunks to inspect for candidate entities")
    ontology_promote_min_documents: int = Field(default=2)
    ontology_promote_min_chunks: int = Field(default=5)


class PipelineConfig(BaseModel):
    """Complete pipeline configuration."""
    paths: PathConfig = Field(default_factory=PathConfig)
    parser: ParserSettings = Field(default_factory=ParserSettings)
    normalizer: NormalizerSettings = Field(default_factory=NormalizerSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    chunker: ChunkerSettings = Field(default_factory=ChunkerSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    identity: IdentitySettings = Field(default_factory=IdentitySettings)

    # Global
    log_level: str = Field(default="INFO")
    enable_checkpoints: bool = Field(default=True)


def load_config() -> PipelineConfig:
    """Load pipeline configuration with environment variable overrides."""
    config = PipelineConfig()

    # Allow environment variable overrides for sensitive values
    if neo4j_uri := os.getenv("RKL_NEO4J_URI"):
        config.neo4j.uri = neo4j_uri
    if neo4j_user := os.getenv("RKL_NEO4J_USER"):
        config.neo4j.username = neo4j_user
    if neo4j_pass := os.getenv("RKL_NEO4J_PASSWORD"):
        config.neo4j.password = neo4j_pass
    if ollama_url := os.getenv("RKL_OLLAMA_URL"):
        config.ollama.base_url = ollama_url
    if ollama_model := os.getenv("RKL_OLLAMA_MODEL"):
        config.ollama.model = ollama_model

    config.paths.ensure_dirs()
    return config
