"""ToolAgent: plan a goal, call real local tools, look at what came back, iterate.

Two ways of choosing the next tool share one loop:

- **model-driven** when a local LLM is available: each iteration the model sees the goal, the
  tool catalogue and everything the previous tools returned (truncated), and answers one
  structured object — ``{thought, tool, args, done, final}``. The model chooses; the tool acts;
  the loop stops when the model says it is done or the iteration budget runs out.
- **deterministic** otherwise: a small set of goal shapes ("calculate …", "search … in the
  documents", "run this python: …", "write … to <file>") map to fixed tool sequences, so the
  workbench still executes tasks with the model switched off.

Every tool call is validated, timed, logged to the hash chain and recorded in the report; a tool
that raises becomes a failed iteration, never an exception out of the loop.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from workbench.tools.base import ToolContext, ToolResult
from workbench.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_PREVIEW = 1500

SYSTEM_PROMPT = """You are an engineering assistant that completes tasks by calling local tools.
You never do arithmetic yourself: use the calculate tool. You never invent document content: use search_documents.
Each turn return ONE JSON object: {"thought": "...", "tool": "<tool name or null>", "args": {...}, "done": false, "final": ""}.
Set "done": true with "final" holding the answer for the user when the goal is achieved or cannot be achieved.
Use only the tools listed. Arguments must match the tool's parameters exactly."""


class ToolDecision(BaseModel):
    thought: str = ""
    tool: str | None = None
    args: dict = Field(default_factory=dict)
    done: bool = False
    final: str = ""


class ToolIteration(BaseModel):
    n: int
    tool: str | None
    args: dict = Field(default_factory=dict)
    ok: bool
    output_preview: str = ""
    duration_ms: int = 0
    thought: str = ""
    error: str | None = None


class ToolRunReport(BaseModel):
    goal: str
    mode: str = "deterministic"                 # deterministic | model
    iterations: list[ToolIteration] = Field(default_factory=list)
    final_output: str = ""
    files: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    ok: bool = False
    reason: str = ""
    llm_calls: int = 0
    duration_ms: int = 0
    log_hashes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- deterministic planner
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(?:please\s+)?(?:calculate|compute|evaluate|what is)\s*:?\s*(?P<expr>.+?)\s*\??\s*$", re.I | re.S), "calculate"),
    (re.compile(r"^\s*(?:please\s+)?(?:run|execute)\s+(?:this\s+)?python\s*:?\s*(?P<code>.+)$", re.I | re.S), "run_python"),
    (re.compile(r"^\s*(?:please\s+)?(?:search|find|look up|lookup)\s+(?:for\s+)?(?P<query>.+?)\s+in\s+(?:the\s+)?(?:documents|manuals|knowledge base|docs)\s*\.?\s*$", re.I | re.S), "search_documents"),
    (re.compile(r"^\s*(?:please\s+)?write\s+(?P<content>.+?)\s+to\s+(?:the\s+file\s+)?(?P<path>[\w./\\-]+\.\w+)\s*\.?\s*$", re.I | re.S), "write_file"),
    (re.compile(r"^\s*(?:please\s+)?read\s+(?:the\s+)?spreadsheet\s+(?P<path>[\w./\\-]+\.xlsx)\s*\.?\s*$", re.I), "spreadsheet_read"),
    (re.compile(r"^\s*(?:please\s+)?read\s+(?:the\s+file\s+)?(?P<path>[\w./\\-]+\.\w+)\s*\.?\s*$", re.I), "read_file"),
    (re.compile(r"^\s*(?:please\s+)?list\s+(?:the\s+)?files\b.*$", re.I), "list_files"),
]


def plan_deterministically(goal: str) -> list[tuple[str, dict]]:
    """A fixed tool sequence for a recognised goal shape, or an empty list."""
    for rx, tool in _PATTERNS:
        m = rx.match(goal or "")
        if not m:
            continue
        g = m.groupdict()
        if tool == "calculate":
            return [("calculate", {"expression": g["expr"].strip().rstrip("?.")})]
        if tool == "run_python":
            code = g["code"].strip()
            code = re.sub(r"^```(?:python)?\s*|\s*```$", "", code, flags=re.S)
            return [("run_python", {"code": code})]
        if tool == "search_documents":
            return [("search_documents", {"query": g["query"].strip().strip("'\""), "k": 6})]
        if tool == "write_file":
            content = g["content"].strip()
            if len(content) >= 2 and content[0] == content[-1] and content[0] in "'\"":
                content = content[1:-1]
            return [("write_file", {"path": g["path"].strip(), "content": content})]
        if tool == "spreadsheet_read":
            return [("spreadsheet_read", {"path": g["path"].strip()})]
        if tool == "read_file":
            return [("read_file", {"path": g["path"].strip()})]
        if tool == "list_files":
            return [("list_files", {})]
    return []


# --------------------------------------------------------------------------- the loop
class ToolAgent:
    def __init__(self, registry: ToolRegistry, ctx: ToolContext, llm: Any = None, max_iterations: int = 8) -> None:
        self.registry = registry
        self.ctx = ctx
        self.llm = llm if llm is not None else ctx.llm
        self.max_iterations = max(1, int(max_iterations))

    # ------------------------------------------------------------------ helpers
    def _call(self, name: str, args: dict, n: int, report: ToolRunReport, thought: str = "") -> ToolResult:
        tool = self.registry.get(name)
        t0 = time.time()
        if tool is None:
            res = ToolResult.failure(name, f"unknown tool {name!r}; available: {', '.join(self.registry.names())}")
        else:
            res = tool(args, self.ctx)
        res.duration_ms = res.duration_ms or int((time.time() - t0) * 1000)
        report.iterations.append(ToolIteration(n=n, tool=name, args=args, ok=res.ok, thought=thought,
                                               output_preview=(res.output or "")[:_PREVIEW], duration_ms=res.duration_ms,
                                               error=res.error))
        report.files.extend(f for f in res.files if f not in report.files)
        report.evidence.extend(res.evidence)
        report.steps.extend(res.steps)
        if self.ctx.log is not None:
            try:
                report.log_hashes.append(self.ctx.log.head)
            except Exception:
                pass
        return res

    def _tool_catalogue(self) -> str:
        return "\n".join(f"- {t['name']}: {t['description']} parameters={json.dumps(t['parameters'].get('properties', {}))} "
                         f"required={t['parameters'].get('required', [])}" for t in self.registry.describe())

    # ------------------------------------------------------------------ run
    def run(self, goal: str) -> ToolRunReport:
        t0 = time.time()
        report = ToolRunReport(goal=goal)
        use_model = self.llm is not None and self._llm_available()
        try:
            if use_model:
                report.mode = "model"
                self._run_model(goal, report)
            else:
                report.mode = "deterministic"
                self._run_deterministic(goal, report)
        except Exception as exc:                        # the loop itself must not raise
            logger.exception("tool agent failed")
            report.ok = False
            report.reason = f"{type(exc).__name__}: {exc}"
        report.duration_ms = int((time.time() - t0) * 1000)
        return report

    def _llm_available(self) -> bool:
        try:
            return bool(self.llm.available())
        except Exception:
            return False

    def _run_deterministic(self, goal: str, report: ToolRunReport) -> None:
        plan = plan_deterministically(goal)
        if not plan:
            report.ok = False
            report.reason = ("no model is available and the goal does not match a known shape "
                             "(calculate …, search … in the documents, run this python: …, write … to <file>, read <file>, list files)")
            report.final_output = report.reason
            return
        outputs: list[str] = []
        for n, (name, args) in enumerate(plan, start=1):
            res = self._call(name, args, n, report)
            outputs.append(res.output)
            if not res.ok:
                report.ok = False
                report.reason = res.error or f"{name} failed"
                report.final_output = "\n".join(outputs)
                return
        report.ok = True
        report.reason = "deterministic plan completed"
        report.final_output = "\n".join(outputs)

    def _run_model(self, goal: str, report: ToolRunReport) -> None:
        from workbench.llm.client import LLMOutputError, LLMUnavailable

        history: list[str] = []
        catalogue = self._tool_catalogue()
        for n in range(1, self.max_iterations + 1):
            user = (f"## goal\n{goal}\n\n## tools\n{catalogue}\n\n## previous tool results\n"
                    + ("\n\n".join(history) if history else "(none yet)")
                    + f"\n\n## iteration {n} of {self.max_iterations}\nChoose the next tool, or finish with done=true.")
            try:
                decision = self.llm.structured(SYSTEM_PROMPT, user, ToolDecision, purpose="tool_agent")
                report.llm_calls += 1
            except (LLMUnavailable, LLMOutputError) as exc:
                report.iterations.append(ToolIteration(n=n, tool=None, ok=False, error=f"model call failed: {exc}"))
                if not history:                      # nothing happened yet: fall back rather than give up
                    report.mode = "deterministic"
                    self._run_deterministic(goal, report)
                    return
                break
            if decision.done or not decision.tool:
                report.ok = True
                report.reason = "model declared the goal complete" if decision.done else "model chose no tool"
                report.final_output = decision.final or (history[-1] if history else "")
                return
            res = self._call(decision.tool, decision.args or {}, n, report, thought=decision.thought)
            history.append(f"[{n}] {decision.tool}({json.dumps(decision.args or {}, default=str)[:400]}) -> "
                           f"{'ok' if res.ok else 'FAILED'}\n{(res.output or res.error or '')[:_PREVIEW]}")
        report.ok = False
        report.reason = f"iteration budget of {self.max_iterations} exhausted"
        report.final_output = history[-1] if history else ""
