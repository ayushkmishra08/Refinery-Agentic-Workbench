"""Workbench configuration. Reuses the knowledge-layer PipelineConfig for Neo4j,
Ollama and embedding settings so both layers point at the same local services."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from knowledge_layer.config import PipelineConfig, load_config as load_kl_config

PROJECT_ROOT = Path(__file__).parent.parent.resolve()


class WorkbenchPaths(BaseModel):
    root: Path = PROJECT_ROOT / "data" / "workbench"
    sessions_dir: Path = PROJECT_ROOT / "data" / "workbench" / "sessions"
    audit_dir: Path = PROJECT_ROOT / "data" / "workbench" / "audit"
    reports_dir: Path = PROJECT_ROOT / "data" / "workbench" / "reports"
    prompts_dir: Path = PROJECT_ROOT / "workbench" / "prompts"
    fixtures_dir: Path = PROJECT_ROOT / "workbench" / "fixtures"

    def ensure_dirs(self) -> None:
        for d in (self.sessions_dir, self.audit_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)


class AgentLLMSettings(BaseModel):
    """Per-role model choice. Small/fast for classification, larger for synthesis."""
    classifier_model: str = Field(default="qwen3:4b")
    reasoning_model: str = Field(default="deepseek-r1:7b")
    temperature: float = 0.1
    num_ctx: int = 8192
    timeout_seconds: int = 600


class GovernanceSettings(BaseModel):
    require_hitl_for_high_risk: bool = True
    min_confidence_to_answer: float = 0.5
    max_replan_iterations: int = 2
    high_risk_task_types: list[str] = Field(
        default_factory=lambda: ["procedure", "safety", "troubleshooting", "limits"]
    )


class WorkbenchConfig(BaseModel):
    paths: WorkbenchPaths = Field(default_factory=WorkbenchPaths)
    llm: AgentLLMSettings = Field(default_factory=AgentLLMSettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)
    knowledge_backend: str = Field(
        default="mock",
        description="'mock' (fixtures, no services) until extraction is complete; 'neo4j' afterwards",
    )
    knowledge_layer: PipelineConfig = Field(default_factory=load_kl_config)


def load_config() -> WorkbenchConfig:
    cfg = WorkbenchConfig()
    if backend := os.getenv("RWB_KNOWLEDGE_BACKEND"):
        cfg.knowledge_backend = backend
    if m := os.getenv("RWB_CLASSIFIER_MODEL"):
        cfg.llm.classifier_model = m
    if m := os.getenv("RWB_REASONING_MODEL"):
        cfg.llm.reasoning_model = m
    cfg.paths.ensure_dirs()
    return cfg
