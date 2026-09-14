"""End-to-end run: UserRequest -> FinalResponse. Single place that wires config, services, agents."""
from __future__ import annotations

from workbench.config import WorkbenchConfig
from workbench.core.request import UserRequest
from workbench.core.result import FinalResponse


def run(request: UserRequest, cfg: WorkbenchConfig) -> FinalResponse:
    raise NotImplementedError("build phase: see docs/PLAN.md")
