"""The model capability registry: what each open-weight model is good at.

Loaded from ``workbench/models/registry.yaml`` and merged with an optional local override at
``data/workbench/models/registry.local.yaml`` (same shape; an entry with the same ``name``
replaces the built-in one, a new name is added). Which models are actually *installed* comes
from Ollama's ``/api/tags`` at runtime; a registered model that is not pulled is listed but
never routed to.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TASK_KINDS: tuple[str, ...] = (
    "classification", "resolution", "extraction", "summarization", "composition", "reasoning",
    "calculation", "code", "vision", "planning", "tool_agent",
)
DEFAULT_REGISTRY = Path(__file__).with_name("registry.yaml")


class ModelProfile(BaseModel):
    name: str
    family: str = ""
    size_gb: float = 0.0
    context: int = 4096
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    min_vram_mb: int = 0
    thinking: bool = False
    capabilities: dict[str, float] = Field(default_factory=dict)
    notes: str = ""
    source: str = "builtin"                      # builtin | local | discovered

    def capability(self, kind: str) -> float:
        if kind in self.capabilities:
            return float(self.capabilities[kind])
        text = [v for k, v in self.capabilities.items() if k != "vision"]
        return (sum(text) / len(text)) if text else 0.3

    @property
    def has_vision(self) -> bool:
        return "image" in self.modalities or self.capability("vision") > 0.3

    def fits(self, vram_mb: int) -> bool:
        return vram_mb >= self.min_vram_mb


class ModelRegistry(BaseModel):
    models: list[ModelProfile] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    def get(self, name: str) -> ModelProfile | None:
        for m in self.models:
            if m.name == name or m.name == f"{name}:latest" or f"{m.name}:latest" == name:
                return m
        return None

    def names(self) -> list[str]:
        return [m.name for m in self.models]

    def register(self, profile: ModelProfile) -> None:
        """Add or replace a model. This is the whole 'plug in a new model' step."""
        self.models = [m for m in self.models if m.name != profile.name] + [profile]

    def merged_with(self, other: "ModelRegistry") -> "ModelRegistry":
        out = ModelRegistry(models=list(self.models), sources=list(self.sources) + list(other.sources))
        for m in other.models:
            out.register(m)
        return out


def _read_yaml(path: Path, source: str) -> ModelRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = []
    for row in raw.get("models", []) or []:
        try:
            models.append(ModelProfile.model_validate({**row, "source": source}))
        except Exception as exc:
            logger.warning("model registry entry skipped in %s: %s", path, exc)
    return ModelRegistry(models=models, sources=[str(path)])


def load_registry(local_override: Path | None = None, builtin: Path = DEFAULT_REGISTRY) -> ModelRegistry:
    reg = _read_yaml(builtin, "builtin") if builtin.exists() else ModelRegistry()
    if local_override and local_override.exists():
        try:
            reg = reg.merged_with(_read_yaml(local_override, "local"))
        except Exception as exc:
            logger.warning("local model registry %s unreadable: %s", local_override, exc)
    return reg


def discover_installed(base_url: str = "http://localhost:11434", timeout: float = 3.0) -> list[dict]:
    """Ollama's installed models: [{name, size_gb, family, modalities}] or [] when it is down."""
    import httpx

    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        r.raise_for_status()
    except Exception as exc:
        logger.info("Ollama not reachable for model discovery: %s", exc)
        return []
    out = []
    for m in r.json().get("models", []):
        details = m.get("details") or {}
        out.append({
            "name": m.get("name", ""),
            "size_gb": round(int(m.get("size") or 0) / (1024 ** 3), 2),
            "family": details.get("family") or "",
            "parameter_size": details.get("parameter_size") or "",
            "quantization": details.get("quantization_level") or "",
            "modified": m.get("modified_at") or "",
        })
    return out
