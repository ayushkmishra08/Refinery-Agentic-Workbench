"""Tool contract: a name, a description, a JSON-schema parameter block, and ``run``."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


class ToolError(ValueError):
    """Bad arguments or a refused operation. Becomes a failed ToolResult, never an exception to the caller."""


@dataclass
class ToolContext:
    session_key: str
    workspace: Path
    knowledge: Any = None                 # a KnowledgeService (guarded), or None
    principal: Any = None
    sandbox: Any = None                   # workbench.sandbox.SandboxRunner
    llm: Any = None
    log: Any = None                       # workbench.sovereignty.hashchain.HashChainedLog
    resources: Any = None
    extras: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)


class ToolResult(BaseModel):
    tool: str
    ok: bool = True
    output: str = ""
    data: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None

    @classmethod
    def failure(cls, tool: str, error: str) -> "ToolResult":
        return cls(tool=tool, ok=False, output=error, error=error)


class Tool:
    name: str = "tool"
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}, "required": []}

    def validate(self, args: dict) -> dict:
        """jsonschema-lite: required keys present, declared types respected, unknown keys dropped."""
        if not isinstance(args, dict):
            raise ToolError(f"{self.name}: arguments must be an object")
        props = self.parameters.get("properties", {})
        for key in self.parameters.get("required", []):
            if key not in args or args[key] is None:
                raise ToolError(f"{self.name}: missing required argument {key!r}")
        clean: dict = {}
        for key, value in args.items():
            spec = props.get(key)
            if spec is None:
                continue
            expected = _TYPES.get(spec.get("type", ""))
            if expected is not None and value is not None and not isinstance(value, expected):
                if expected is str and isinstance(value, (int, float)):
                    value = str(value)
                elif expected in (int, (int, float)) and isinstance(value, str):
                    try:
                        value = float(value) if expected == (int, float) else int(value)
                    except ValueError as exc:
                        raise ToolError(f"{self.name}: argument {key!r} must be {spec.get('type')}") from exc
                elif expected is bool and isinstance(value, int):
                    value = bool(value)
                else:
                    raise ToolError(f"{self.name}: argument {key!r} must be {spec.get('type')}")
            clean[key] = value
        return clean

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    def __call__(self, args: dict, ctx: ToolContext) -> ToolResult:
        """Validate, run, time, log. Exceptions become failed results."""
        t0 = time.time()
        try:
            clean = self.validate(args or {})
            result = self.run(clean, ctx)
        except ToolError as exc:
            result = ToolResult.failure(self.name, str(exc))
        except Exception as exc:
            logger.exception("tool %s failed", self.name)
            result = ToolResult.failure(self.name, f"{type(exc).__name__}: {exc}")
        result.tool = self.name
        result.duration_ms = result.duration_ms or int((time.time() - t0) * 1000)
        if ctx.log is not None:
            try:
                ctx.log.append({"kind": "tool_call", "tool": self.name, "session": ctx.session_key,
                                "args_sha256": hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
                                "ok": result.ok, "duration_ms": result.duration_ms, "files": result.files,
                                "evidence": len(result.evidence), "error": result.error})
            except Exception as exc:
                logger.warning("tool log write failed: %s", exc)
        return result

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def confine(workspace: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``workspace`` or refuse: no absolute paths, no ``..``, no symlink escapes."""
    if not isinstance(rel, str) or not rel.strip():
        raise ToolError("a relative file path is required")
    candidate = Path(rel)
    if candidate.is_absolute() or rel.startswith(("/", "\\")) or ":" in rel.split("/")[0].split("\\")[0]:
        raise ToolError(f"absolute paths are not allowed: {rel!r}")
    if any(part == ".." for part in candidate.parts):
        raise ToolError(f"'..' is not allowed in paths: {rel!r}")
    root = workspace.resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ToolError(f"path escapes the workspace: {rel!r}") from exc
    return target
