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
    prompts_dir: Path = PROJECT_ROOT / "prompts"

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
    table_structure_mode: str = Field(default="accurate", description="accurate or fast")
    page_window_size: int = Field(
        default=15,
        description="Pages per parsing window for memory safety",
    )
    max_pages: int | None = Field(default=None, description="Limit pages for testing; None = all")


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
            "Page No",
            "Revision",
            "Doc. No",
            "Document No",
            "CONFIDENTIAL",
            "PROPRIETARY",
        ],
        description="Text patterns commonly found in page headers/footers",
    )


class OllamaSettings(BaseModel):
    """Ollama / DeepSeek-R1 configuration."""
    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="deepseek-r1:7b")
    num_ctx: int = Field(
        default=4096,
        description="Context window size. Conservative for 6GB VRAM.",
    )
    temperature: float = Field(
        default=0.1,
        description="Near-deterministic for factual extraction.",
    )
    num_predict: int = Field(
        default=4096,
        description="Maximum output tokens. Must be large enough for full JSON extraction.",
    )
    timeout_seconds: int = Field(default=300, description="Request timeout")
    max_retries: int = Field(default=2)


class Neo4jSettings(BaseModel):
    """Neo4j connection configuration."""
    uri: str = Field(default="bolt://localhost:7687")
    username: str = Field(default="neo4j")
    password: str = Field(default="refinery2024")
    database: str = Field(default="neo4j")


class EmbeddingSettings(BaseModel):
    """Embedding model configuration."""
    model_name: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence transformer model for chunk embeddings",
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
