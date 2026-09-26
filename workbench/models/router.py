"""The model router: task kind -> the best installed model that fits the hardware.

Nothing in the agents says "task X -> model Y". An agent names the *purpose* of a call
(``purpose="compose_answer"``); the router maps that to a task kind, scores every registered
model on it, discards models that are not installed, penalises ones that would spill off the
GPU, prefers the model already resident when the difference is small (a swap costs 5-15 s on a
4 GB card), and records the decision — chosen model, every candidate's score, and the reason —
in a hash-chained routing log. ``decompose`` splits a hybrid request (read a scan, calculate a
margin, write a note) into sub-tasks and routes each one.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from pydantic import BaseModel, Field

from workbench.models.registry import TASK_KINDS, ModelProfile, ModelRegistry
from workbench.sovereignty.hashchain import HashChainedLog

logger = logging.getLogger(__name__)

#: purpose strings used across the agents -> task kind
PURPOSE_TO_KIND: dict[str, str] = {
    "classify": "classification", "task_classifier": "classification",
    "followup_rewrite": "classification",
    "entity_guess": "resolution", "context_resolver": "resolution",
    "diagnose": "extraction", "diagnostic": "extraction",
    "why_summary": "summarization", "explanation": "summarization", "report_summary": "summarization",
    "compose_answer": "composition", "answer_composer": "composition",
    "plan_refine": "planning", "planner": "planning",
    "vision": "vision", "describe_image": "vision", "ocr_review": "vision",
    "tool_agent": "tool_agent", "tool_plan": "tool_agent",
    "code": "code", "write_code": "code", "fix_code": "code",
    "calculation": "calculation", "calc_structure": "calculation",
    "reasoning": "reasoning", "compare": "reasoning",
}


def kind_of_purpose(purpose: str | None) -> str:
    if not purpose:
        return "composition"
    p = purpose.strip().lower()
    if p in PURPOSE_TO_KIND:
        return PURPOSE_TO_KIND[p]
    for key, kind in PURPOSE_TO_KIND.items():
        if key in p:
            return kind
    return "composition"


class Candidate(BaseModel):
    name: str
    capability: float
    installed: bool
    fits_vram: bool
    resident: bool = False
    score: float
    note: str = ""


class RoutingDecision(BaseModel):
    kind: str
    purpose: str = ""
    chosen: str | None
    candidates: list[Candidate] = Field(default_factory=list)
    reason: str = ""
    needs_vision: bool = False
    ts: float = Field(default_factory=time.time)
    session: str | None = None
    run_id: str | None = None

    def brief(self) -> str:
        top = ", ".join(f"{c.name}={c.score:.2f}" for c in self.candidates[:3])
        return f"{self.kind}: {self.chosen or 'no model'} ({top})"


class SubTask(BaseModel):
    kind: str
    description: str
    needs_vision: bool = False
    deterministic_tool: str | None = None      # calculate / search_documents / run_python ... when the LLM is not the worker


class RoutingPlan(BaseModel):
    text: str
    subtasks: list[SubTask]
    decisions: list[RoutingDecision]
    hybrid: bool

    def summary(self) -> str:
        return "; ".join(f"{s.kind} -> {d.chosen or (s.deterministic_tool or 'deterministic')}" for s, d in zip(self.subtasks, self.decisions))


# ------------------------------------------------------------------------- decomposition rules
_DECOMP: list[tuple[str, str, str, bool, str | None]] = [
    # (regex, kind, description, needs_vision, deterministic_tool)
    (r"\b(scan(ned)?|image|photo(graph)?|picture|drawing|p&id|pid\b|handwrit|sketch|screenshot|ocr)\b", "vision", "read the scanned page / image", True, "ocr_image"),
    (r"\b(calculat|comput|margin|percent|%|ratio|sum of|total of|difference between .* and|how much (more|less)|convert)\b", "calculation", "carry out the calculation deterministically and show the steps", False, "calculate"),
    (r"\b(python|code|script|function|program|unit test|refactor|bug)\b", "code", "write and verify code in the sandbox", False, "run_python"),
    (r"\b(spreadsheet|excel|xlsx|workbook|sheet|csv)\b", "tool_agent", "read or edit the spreadsheet", False, "spreadsheet_read"),
    (r"\b(write|draft|prepare|compose|note|memo|email|letter|summary|summari[sz]e|report|minutes)\b", "composition", "write the document from the gathered material", False, None),
    (r"\b(compare|versus|vs\.?|trade-?off|which is better|evaluate|assess|recommend)\b", "reasoning", "reason over the compared material", False, None),
    (r"\b(why|explain|cause|because|mechanism)\b", "summarization", "explain from the documented passages", False, None),
    (r"\b(manual|sop|procedure|standard|document|correspondence|spec)\b", "summarization", "ground the answer in the internal documents", False, "search_documents"),
]


class ModelRouter:
    def __init__(self, registry: ModelRegistry, *, installed: list[str] | None = None, vram_mb: int = 0,
                 default_model: str | None = None, vision_default: str | None = None,
                 log_path: Path | None = None, swap_margin: float = 0.08) -> None:
        self.registry = registry
        self.installed: set[str] = set()
        for n in installed or []:
            self.installed.add(n)
            if n.endswith(":latest"):
                self.installed.add(n[: -len(":latest")])
        self.vram_mb = vram_mb
        self.default_model = default_model
        self.vision_default = vision_default
        self.resident: str | None = None
        self.swap_margin = swap_margin
        self.log = HashChainedLog(log_path, name="routing") if log_path else None
        self.decisions: list[RoutingDecision] = []

    # ------------------------------------------------------------------ installed models
    def is_installed(self, name: str) -> bool:
        if not self.installed:
            return name in (self.default_model, self.vision_default)
        return name in self.installed or f"{name}:latest" in self.installed

    def profile_for(self, name: str) -> ModelProfile:
        p = self.registry.get(name)
        if p is not None:
            return p
        # a model Ollama has but the registry does not know: a generalist with modest scores
        return ModelProfile(name=name, capabilities={k: 0.5 for k in TASK_KINDS if k != "vision"}, source="discovered")

    def available_profiles(self) -> list[ModelProfile]:
        names = set(self.registry.names()) | {n for n in self.installed if not n.endswith(":latest")}
        return [self.profile_for(n) for n in sorted(names)]

    # ------------------------------------------------------------------ the decision
    def route(self, kind: str, *, purpose: str = "", needs_vision: bool = False, budget: str = "normal",
              session: str | None = None, run_id: str | None = None) -> RoutingDecision:
        kind = kind if kind in TASK_KINDS else "composition"
        needs_vision = needs_vision or kind == "vision"
        cands: list[Candidate] = []
        for p in self.available_profiles():
            installed = self.is_installed(p.name)
            fits = p.fits(self.vram_mb) if self.vram_mb else True
            cap = p.capability("vision" if needs_vision else kind)
            note = ""
            if needs_vision and not p.has_vision:
                cap = 0.0
                note = "no image input"
            score = cap
            if not installed:
                score = 0.0
                note = note or "not installed"
            elif not fits:
                score *= 0.6
                note = note or f"spills off the GPU ({p.size_gb} GB > {self.vram_mb} MB)"
                if budget == "fast":
                    score *= 0.5
            if p.thinking and budget == "fast":
                score *= 0.8
            resident = p.name == self.resident
            if resident and score > 0:
                score += 0.02
            cands.append(Candidate(name=p.name, capability=round(cap, 3), installed=installed, fits_vram=fits,
                                   resident=resident, score=round(score, 4), note=note))
        cands.sort(key=lambda c: (-c.score, c.name))
        chosen: str | None = None
        reason = ""
        usable = [c for c in cands if c.score > 0]
        if usable:
            best = usable[0]
            chosen = best.name
            reason = f"highest {('vision' if needs_vision else kind)} capability among installed models"
            if self.resident and self.resident != best.name:
                res = next((c for c in usable if c.name == self.resident), None)
                if res is not None and best.score - res.score < self.swap_margin:
                    chosen = res.name
                    reason = f"{best.name} scores only {best.score - res.score:.2f} higher; keeping the resident model avoids a swap"
            if not best.fits_vram:
                reason += "; will split between GPU and CPU"
        else:
            fallback = self.vision_default if needs_vision else self.default_model
            if fallback:
                chosen = fallback
                reason = "no registered model is installed; using the profile default"
            else:
                reason = "no model available for this task"
        decision = RoutingDecision(kind=kind, purpose=purpose, chosen=chosen, candidates=cands, reason=reason,
                                   needs_vision=needs_vision, session=session, run_id=run_id)
        self._record(decision)
        return decision

    def note_resident(self, model: str | None) -> None:
        self.resident = model

    def _record(self, d: RoutingDecision) -> None:
        self.decisions.append(d)
        if len(self.decisions) > 500:
            del self.decisions[:-500]
        if self.log is not None:
            try:
                self.log.append({"kind": d.kind, "purpose": d.purpose, "chosen": d.chosen, "reason": d.reason,
                                 "needs_vision": d.needs_vision, "session": d.session, "run_id": d.run_id,
                                 "candidates": [{"name": c.name, "score": c.score, "installed": c.installed,
                                                 "fits_vram": c.fits_vram, "note": c.note} for c in d.candidates[:6]]})
            except Exception as exc:
                logger.warning("routing log write failed: %s", exc)

    def decisions_since(self, ts: float) -> list[RoutingDecision]:
        return [d for d in self.decisions if d.ts >= ts]

    # ------------------------------------------------------------------ decomposition
    def decompose(self, text: str) -> list[SubTask]:
        low = text.lower()
        found: list[SubTask] = []
        for rx, kind, desc, vision, tool in _DECOMP:
            if re.search(rx, low) and not any(s.kind == kind for s in found):
                found.append(SubTask(kind=kind, description=desc, needs_vision=vision, deterministic_tool=tool))
        if not found:
            found.append(SubTask(kind="composition", description="answer from the retrieved evidence"))
        # a request that gathers material and writes something up ends with composition
        if len(found) > 1 and not any(s.kind == "composition" for s in found):
            found.append(SubTask(kind="composition", description="assemble the sub-results into one reply"))
        return found

    def plan(self, text: str, *, session: str | None = None, run_id: str | None = None, budget: str = "normal") -> RoutingPlan:
        subs = self.decompose(text)
        decisions = [self.route(s.kind, purpose=f"subtask:{s.kind}", needs_vision=s.needs_vision, budget=budget,
                                session=session, run_id=run_id) for s in subs]
        return RoutingPlan(text=text, subtasks=subs, decisions=decisions, hybrid=len(subs) > 1)

    # ------------------------------------------------------------------ introspection
    def table(self) -> list[dict]:
        rows = []
        for p in self.available_profiles():
            rows.append({"name": p.name, "family": p.family, "size_gb": p.size_gb, "context": p.context,
                         "modalities": p.modalities, "installed": self.is_installed(p.name),
                         "fits_vram": p.fits(self.vram_mb) if self.vram_mb else True, "min_vram_mb": p.min_vram_mb,
                         "resident": p.name == self.resident, "capabilities": p.capabilities, "notes": p.notes,
                         "source": p.source, "thinking": p.thinking})
        return rows

    def best_per_kind(self) -> dict[str, str | None]:
        out = {}
        for kind in TASK_KINDS:
            d = self.route(kind, purpose=f"probe:{kind}")
            self.decisions.pop()              # a probe is not a decision worth keeping in memory
            out[kind] = d.chosen
        return out
