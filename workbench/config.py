"""Workbench configuration: hardware profiles, model choices, retrieval and governance settings.

Design rules
- One LLM resident at a time. Model swaps on a 4 GB card cost 5-15 s, so the text model
  answers everything and the vision model is loaded only when an image arrives.
- Everything that can be deterministic is deterministic; the LLM is a structured function
  (JSON-schema constrained) that the agents call sparingly. Every agent also works with
  ``RWB_LLM=off`` (rule-based classification, template narratives).
- Profiles are selected from the detected VRAM (torch) and can be forced with RWB_PROFILE.

Environment overrides: RWB_PROFILE, RWB_LLM_MODEL, RWB_VISION_MODEL, RWB_OLLAMA_URL, RWB_LLM
(on/off), RWB_RERANKER (on/off), RWB_KNOWLEDGE_BACKEND (auto|files|mock|neo4j), RWB_DOCS
(comma-separated document ids to load).
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from knowledge_layer.config import PipelineConfig, load_config as load_kl_config

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


# --------------------------------------------------------------------------- hardware profiles
class HardwareProfile(BaseModel):
    name: str
    min_vram_mb: int
    llm_model: str
    vision_model: str | None
    num_ctx: int
    num_predict_short: int = 512          # classification / resolution / verification
    num_predict_long: int = 1200          # synthesis / report narrative
    keep_alive: str = "10m"
    reranker_model: str | None = "BAAI/bge-reranker-base"
    reranker_device: str = "cpu"
    embedding_device: str = "cpu"
    llm_extraction: bool = Field(default=True, description="Ask the LLM to structure causes/checks/actions (one ~50-100 s call on a 4 GB card); off there, the deterministic extraction alone is used")
    notes: str = ""


PROFILES: dict[str, HardwareProfile] = {
    "cpu": HardwareProfile(
        name="cpu", min_vram_mb=0, llm_model="qwen3.5:2b", vision_model="qwen3.5:2b", num_ctx=4096,
        num_predict_long=800, reranker_model=None, llm_extraction=False,
        notes="No CUDA. Small model on CPU; reranker off to keep latency tolerable.",
    ),
    "gpu_4gb": HardwareProfile(
        name="gpu_4gb", min_vram_mb=3500, llm_model="qwen3:4b", vision_model="qwen3.5:2b", num_ctx=4096, llm_extraction=False,
        notes="GTX 1650 class. qwen3:4b (2.5 GB Q4) fits fully with a 4k KV cache; qwen3.5:2b (2.7 GB) "
              "handles image input on demand. qwen3.5:4b (3.4 GB) would spill to CPU.",
    ),
    "gpu_8gb": HardwareProfile(
        name="gpu_8gb", min_vram_mb=7500, llm_model="qwen3.5:4b", vision_model="qwen3.5:4b", num_ctx=8192,
        num_predict_long=1600,
        notes="One multimodal model (qwen3.5:4b, 3.4 GB) serves text and images.",
    ),
    "gpu_12gb": HardwareProfile(
        name="gpu_12gb", min_vram_mb=11500, llm_model="qwen3.5:9b", vision_model="qwen3.5:9b", num_ctx=16384,
        num_predict_long=2000, reranker_model="BAAI/bge-reranker-v2-m3", reranker_device="cuda", embedding_device="cuda",
        notes="College GPU (12-16 GB). qwen3.5:9b (6.6 GB) text+vision, reranker and embeddings on the GPU.",
    ),
    "gpu_16gb": HardwareProfile(
        name="gpu_16gb", min_vram_mb=15500, llm_model="qwen3.5:9b", vision_model="qwen3.5:9b", num_ctx=32768,
        num_predict_long=2400, reranker_model="BAAI/bge-reranker-v2-m3", reranker_device="cuda", embedding_device="cuda",
        notes="Same model as 12 GB with a 32k context; gpt-oss:20b (13 GB) is the text-only alternative (RWB_LLM_MODEL).",
    ),
    "gpu_24gb": HardwareProfile(
        name="gpu_24gb", min_vram_mb=23000, llm_model="qwen3.6:27b", vision_model="qwen3.6:27b", num_ctx=32768,
        num_predict_long=3000, reranker_model="BAAI/bge-reranker-v2-m3", reranker_device="cuda", embedding_device="cuda",
        notes="qwen3.6:27b (18 GB) text+image; qwen3.5:27b (17 GB) is the alternative.",
    ),
}


def detect_vram_mb() -> int:
    """Total VRAM of GPU 0 in MB, 0 when CUDA is unavailable."""
    try:
        import torch  # noqa: WPS433 (optional dependency already used by the knowledge layer)

        if not torch.cuda.is_available():
            return 0
        _free, total = torch.cuda.mem_get_info(0)
        return int(total // (1024 * 1024))
    except Exception:
        return 0


def select_profile(vram_mb: int | None = None) -> HardwareProfile:
    forced = os.getenv("RWB_PROFILE")
    if forced and forced in PROFILES:
        return PROFILES[forced]
    vram = detect_vram_mb() if vram_mb is None else vram_mb
    best = PROFILES["cpu"]
    for p in PROFILES.values():
        if vram >= p.min_vram_mb and p.min_vram_mb >= best.min_vram_mb:
            best = p
    return best


# --------------------------------------------------------------------------- settings blocks
class WorkbenchPaths(BaseModel):
    root: Path = PROJECT_ROOT / "data" / "workbench"
    sessions_dir: Path = PROJECT_ROOT / "data" / "workbench" / "sessions"
    audit_dir: Path = PROJECT_ROOT / "data" / "workbench" / "audit"
    reports_dir: Path = PROJECT_ROOT / "data" / "workbench" / "reports"
    cache_dir: Path = PROJECT_ROOT / "data" / "workbench" / "cache"
    uploads_dir: Path = PROJECT_ROOT / "data" / "workbench" / "uploads"
    thinking_dir: Path = PROJECT_ROOT / "data" / "workbench" / "thinking"
    prompts_dir: Path = PROJECT_ROOT / "workbench" / "prompts"
    fixtures_dir: Path = PROJECT_ROOT / "workbench" / "fixtures"
    benchmarks_dir: Path = PROJECT_ROOT / "workbench" / "benchmarks"

    def ensure_dirs(self) -> None:
        for d in (self.sessions_dir, self.audit_dir, self.reports_dir, self.cache_dir, self.uploads_dir, self.thinking_dir):
            d.mkdir(parents=True, exist_ok=True)


class LLMSettings(BaseModel):
    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = ""                        # filled from the profile unless RWB_LLM_MODEL is set
    vision_model: str | None = None
    num_ctx: int = 4096
    num_predict_short: int = 512
    num_predict_long: int = 1200
    temperature: float = 0.1
    keep_alive: str = "10m"
    timeout_seconds: int = 240
    think: bool = False
    max_retries: int = 1
    use_llm_for_classification: bool = Field(
        default=True, description="Call the LLM only when the rule classifier is unsure (confidence < threshold)",
    )
    classifier_confidence_threshold: float = 0.72
    use_llm_for_narrative: bool = Field(default=True, description="Short natural-language summaries in answers")
    use_llm_for_extraction: bool = Field(default=True, description="Causes/checks extraction from upset chunks")


class RetrievalSettings(BaseModel):
    bm25_k: int = 30
    vector_k: int = 30
    fused_k: int = 20                      # candidates handed to the reranker
    final_k: int = 8
    use_vectors: bool = True
    use_reranker: bool = True
    reranker_model: str | None = "BAAI/bge-reranker-base"
    reranker_device: str = "cpu"
    embedding_model: str = "BAAI/bge-small-en-v1.5"   # must match the knowledge layer's index
    embedding_device: str = "cpu"
    max_chunk_chars_in_prompt: int = 1800
    rrf_k: int = 60


class GovernanceSettings(BaseModel):
    require_hitl_for_high_risk: bool = True
    min_confidence_to_answer: float = 0.35
    max_replan_iterations: int = 2
    high_risk_task_types: list[str] = Field(default_factory=lambda: ["procedure", "safety", "troubleshooting", "limits", "planning"])
    restricted_patterns: list[str] = Field(
        default_factory=lambda: [
            r"\bbypass(?:ing)?\b", r"\bdefeat\b", r"\bdisable\b.*\b(trip|interlock|protection|alarm|safeguard)",
            r"\boverride\b", r"\bjumper\b", r"\bforce\b.*\b(interlock|trip)\b", r"\binhibit\b.*\b(trip|interlock)\b",
        ],
        description="Requests to remove or defeat a protection are answered only with the documented authorization path",
    )


class WorkbenchConfig(BaseModel):
    paths: WorkbenchPaths = Field(default_factory=WorkbenchPaths)
    profile: HardwareProfile = Field(default_factory=select_profile)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)
    knowledge_backend: str = Field(default="auto", description="auto | files | mock | neo4j")
    document_ids: list[str] = Field(default_factory=list, description="Knowledge-layer documents to load; empty = all found")
    knowledge_layer: PipelineConfig = Field(default_factory=load_kl_config)
    session_ttl_turns: int = 20
    log_level: str = "INFO"

    def apply_profile(self) -> None:
        p = self.profile
        self.llm.model = self.llm.model or p.llm_model
        self.llm.vision_model = self.llm.vision_model or p.vision_model
        self.llm.num_ctx = p.num_ctx
        self.llm.num_predict_short = p.num_predict_short
        self.llm.num_predict_long = p.num_predict_long
        self.llm.keep_alive = p.keep_alive
        self.retrieval.reranker_model = p.reranker_model
        self.retrieval.reranker_device = p.reranker_device
        self.retrieval.embedding_device = p.embedding_device
        if p.reranker_model is None:
            self.retrieval.use_reranker = False
        self.llm.use_llm_for_extraction = p.llm_extraction

    def discovered_documents(self) -> list[str]:
        """Document ids whose knowledge-layer artefacts exist (normalized + chunks)."""
        kl = self.knowledge_layer.paths
        found: list[str] = []
        if kl.knowledge_dir.exists():
            for d in sorted(kl.knowledge_dir.iterdir()):
                if not d.is_dir() or d.name in ("smoke_test", "test"):
                    continue
                if (d / "chunks.json").exists() and (kl.normalized_dir / f"{d.name}_normalized.json").exists():
                    found.append(d.name)
        return found

    def resolve_backend(self) -> str:
        if self.knowledge_backend != "auto":
            return self.knowledge_backend
        return "files" if (self.document_ids or self.discovered_documents()) else "mock"


def load_config() -> WorkbenchConfig:
    cfg = WorkbenchConfig()
    cfg.apply_profile()
    env = os.getenv
    if v := env("RWB_LLM_MODEL"):
        cfg.llm.model = v
    if v := env("RWB_VISION_MODEL"):
        cfg.llm.vision_model = v
    if v := env("RWB_OLLAMA_URL"):
        cfg.llm.base_url = v
    elif v := env("RKL_OLLAMA_URL"):
        cfg.llm.base_url = v
    if (v := env("RWB_LLM")) and v.lower() in ("0", "off", "false", "no"):
        cfg.llm.enabled = False
    if (v := env("RWB_RERANKER")) and v.lower() in ("0", "off", "false", "no"):
        cfg.retrieval.use_reranker = False
    if v := env("RWB_LLM_EXTRACTION"):
        cfg.llm.use_llm_for_extraction = v.lower() in ("1", "on", "true", "yes")
    if v := env("RWB_KNOWLEDGE_BACKEND"):
        cfg.knowledge_backend = v
    if v := env("RWB_DOCS"):
        cfg.document_ids = [x.strip() for x in v.split(",") if x.strip()]
    cfg.paths.ensure_dirs()
    return cfg
