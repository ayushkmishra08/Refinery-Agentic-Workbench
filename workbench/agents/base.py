"""BaseAgent: one ``run`` per agent, uniform AgentResult, shared services via AgentServices.

Rules every agent follows
- Read the ContextPackage first; call the KnowledgeService only for what is missing.
- Every statement carries evidence keys; numbers in prose must appear in the cited evidence.
- The LLM is optional: ``self.llm_json`` returns None when no LLM is available or the call
  fails, and the agent falls back to its deterministic rendering.
- Never invent tags, values or steps. Say what is missing in ``result.missing``.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from workbench.config import WorkbenchConfig
from workbench.core.blocks import Block, CalloutBlock, TextBlock
from workbench.core.context import ContextPackage
from workbench.core.events import EventBus
from workbench.core.evidence import Confidence, Evidence
from workbench.core.knowledge import ChunkRecord, ClaimRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult, Statement
from workbench.llm.client import BaseLLM, LLMOutputError, LLMUnavailable
from workbench.llm.prompts import load_prompt
from workbench.services.evidence_store import evidence_from_chunk, evidence_from_claim

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])")
TAG_SCRUB_RE = re.compile(r"\b\d{1,3}\s*-?\s*[A-Z]{1,4}\s*-\s*\d{1,5}(?:\s*[A-Z](?:\s*/\s*[A-Z])*)?\b|\b[A-Z]{1,4}-\d{2,5}[A-Z]?\b")
META_RE = re.compile(
    r"\b(we are given|the task is|i (?:will|should|need to|must)\b|let me (?:re-?read|check|think|see)|as an ai|"
    r"the user (?:is asking|wants|asked)|here is (?:the|my) (?:summary|answer|report)|instructions?:|"
    r"but we have to|note: this|in plain language for|based on the (?:given|provided) (?:facts|list))",
    re.IGNORECASE,
)


def usable_narrative(text: str, question: str, min_words: int = 12) -> bool:
    """False when a model's prose is not fit to show: too short, the question echoed back, or its own scratchpad.

    Small local models sometimes restate the prompt, or narrate the task instead of doing it
    ("We are given a list of facts. The task is to write..."). Showing that to an engineer is
    worse than showing the quoted evidence alone, so the agent keeps its deterministic
    rendering and records why in the trace.
    """
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()
    if len(words) < min_words or META_RE.search(text):
        return False
    asked = re.sub(r"[^a-z0-9 ]+", " ", question.lower()).split()
    return difflib.SequenceMatcher(None, " ".join(words), " ".join(asked)).ratio() < 0.7


@dataclass
class AgentServices:
    knowledge: Any
    llm: BaseLLM
    cfg: WorkbenchConfig
    events: EventBus = field(default_factory=EventBus)
    resources: Any = None
    prior_results: dict[str, AgentResult] = field(default_factory=dict)   # step_id -> result of finished steps

    def result_of(self, agent: str | None = None, step_id: str | None = None) -> AgentResult | None:
        if step_id and step_id in self.prior_results:
            return self.prior_results[step_id]
        if agent:
            for r in reversed(list(self.prior_results.values())):
                if r.agent == agent:
                    return r
        return None

    def results_of(self, agent: str) -> list[AgentResult]:
        return [r for r in self.prior_results.values() if r.agent == agent]


class BaseAgent:
    name: str = "base"
    phase: str = ""
    description: str = ""

    def __init__(self, services: AgentServices) -> None:
        self.s = services
        self.knowledge = services.knowledge
        self.llm = services.llm
        self.cfg = services.cfg

    # ------------------------------------------------------------------ entry point
    def run(self, request: StructuredRequest, context: ContextPackage, step: PlanStep) -> AgentResult:
        t0 = time.time()
        result = AgentResult(agent=self.name, step_id=step.step_id)
        try:
            self.execute(request, context, step, result)
        except Exception as exc:  # an agent failure becomes a failed step, never a crash
            logger.exception("agent %s failed", self.name)
            result.ok = False
            result.summary = f"{self.name} failed: {exc}"
            result.needs_replan = not step.optional
            result.replan_reason = str(exc)
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------ LLM helpers (optional)
    def llm_json(self, prompt_name: str, schema: type[T], result: AgentResult, *, max_tokens: int | None = None, purpose: str = "", **fields) -> T | None:
        if not self.llm.available():
            return None
        try:
            system = load_prompt(self.cfg.paths.prompts_dir, prompt_name)
        except FileNotFoundError:
            system = "You are a careful refinery engineering assistant. Answer only from the given evidence. Return JSON."
        user = "\n".join(f"## {k}\n{v}" for k, v in fields.items())
        try:
            self.s.events.emit("llm_call", agent=self.name, message=purpose or prompt_name,
                               model=getattr(self.llm, "model", None), data={"prompt": prompt_name, "max_tokens": max_tokens})
            out = self.llm.structured(system, user, schema, max_tokens=max_tokens, purpose=purpose or prompt_name)
            result.llm_calls += 1
            if self.s.resources is not None:
                self.s.resources.note_llm_use()
            return out
        except (LLMUnavailable, LLMOutputError) as exc:
            result.trace.append(f"llm skipped ({purpose or prompt_name}): {exc}")
            return None

    def llm_text(self, prompt_name: str, result: AgentResult, *, max_tokens: int | None = None, purpose: str = "", **fields) -> str | None:
        if not self.llm.available() or not self.cfg.llm.use_llm_for_narrative:
            return None
        try:
            system = load_prompt(self.cfg.paths.prompts_dir, prompt_name)
        except FileNotFoundError:
            system = "You are a careful refinery engineering assistant. Write 2-4 sentences using only the given evidence."
        user = "\n".join(f"## {k}\n{v}" for k, v in fields.items())
        try:
            self.s.events.emit("llm_call", agent=self.name, message=purpose or prompt_name,
                               model=getattr(self.llm, "model", None), data={"prompt": prompt_name, "max_tokens": max_tokens})
            text = self.llm.complete(system, user, max_tokens=max_tokens, purpose=purpose or prompt_name)
            result.llm_calls += 1
            if self.s.resources is not None:
                self.s.resources.note_llm_use()
            return text.strip() or None
        except (LLMUnavailable, LLMOutputError) as exc:
            result.trace.append(f"llm skipped ({purpose or prompt_name}): {exc}")
            return None

    # ------------------------------------------------------------------ evidence helpers
    @staticmethod
    def cite_claim(result: AgentResult, c: ClaimRecord) -> str:
        return result.add_evidence(evidence_from_claim(c))

    @staticmethod
    def cite_chunk(result: AgentResult, ch: ChunkRecord, excerpt: str | None = None) -> str:
        return result.add_evidence(evidence_from_chunk(ch, excerpt))

    @staticmethod
    def cite(result: AgentResult, ev: Evidence) -> str:
        return result.add_evidence(ev)

    @staticmethod
    def statement(result: AgentResult, text: str, keys: list[str], kind: str = "fact") -> None:
        # equipment tags (11-P-01A/B), section numbers (12.3.1) and page refs are identifiers, not measured values
        scrubbed = TAG_SCRUB_RE.sub(" ", text)
        scrubbed = re.sub(r"\b\d+(?:\.\d+){1,3}\b(?=\s+[A-Z])", " ", scrubbed)
        scrubbed = re.sub(r"\b(p\.|page|step|chapter|section|rev)\s*\d+[A-Za-z0-9./-]*", " ", scrubbed, flags=re.IGNORECASE)
        result.statements.append(Statement(text=text, evidence_keys=[k for k in keys if k], numbers=NUMBER_RE.findall(scrubbed), kind=kind))

    @staticmethod
    def text_block(markdown: str, title: str | None = None, citations: list[str] | None = None, block_id: str = "") -> TextBlock:
        return TextBlock(id=block_id, title=title, markdown=markdown, citations=citations or [])

    @staticmethod
    def callout(markdown: str, level: str = "info", title: str | None = None, citations: list[str] | None = None) -> CalloutBlock:
        return CalloutBlock(level=level, title=title, markdown=markdown, citations=citations or [])

    @staticmethod
    def confidence(score: float, basis: str, uncertainties: list[str] | None = None) -> Confidence:
        return Confidence(score=max(0.0, min(1.0, score)), basis=basis, uncertainties=uncertainties or [])

    @staticmethod
    def excerpt(text: str, keywords: list[str], width: int = 420) -> str:
        """The sentence window around the first keyword hit (used for grounded chunk citations)."""
        low = text.lower()
        pos = -1
        for kw in keywords:
            if not kw:
                continue
            pos = low.find(kw.lower())
            if pos >= 0:
                break
        if pos < 0:
            return text[:width]
        start = max(0, text.rfind(".", 0, pos) + 1)
        end = text.find(".", pos + 1)
        end = len(text) if end < 0 else end + 1
        snippet = text[start:end].strip()
        if len(snippet) > width:
            snippet = snippet[:width].rsplit(" ", 1)[0] + " ..."
        return snippet


def blocks_to_markdown(blocks: list[Block]) -> str:
    """Plain-text fallback rendering used for answer_markdown and the CLI."""
    out: list[str] = []
    for b in blocks:
        t = b.type
        if t in ("text", "callout"):
            prefix = {"warning": "**Warning:** ", "danger": "**DANGER:** ", "success": "", "info": ""}.get(getattr(b, "level", "info"), "")
            out.append((f"### {b.title}\n" if b.title else "") + prefix + b.markdown)
        elif t == "kpi":
            out.append((f"### {b.title}\n" if b.title else "") + "\n".join(f"- **{i.label}**: {i.value} {i.unit or ''} {('(' + i.qualifier + ')') if i.qualifier else ''} {i.citation or ''}".rstrip() for i in b.items))
        elif t == "table":
            head = "| " + " | ".join(b.columns) + " |\n|" + "---|" * len(b.columns)
            rows = "\n".join("| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in b.rows)
            out.append((f"### {b.title}\n" if b.title else "") + head + "\n" + rows)
        elif t == "steps":
            lines = [f"### {b.title}" if b.title else "### Procedure"]
            if b.prerequisites:
                lines.append("**Prerequisites**")
                lines += [f"- {p.text} {p.citation or ''}".rstrip() for p in b.prerequisites]
            lines += [f"{s.sequence}. {s.text} {s.citation or ''}".rstrip() + (f"  \n   ⚠ {'; '.join(s.warnings)}" if s.warnings else "") for s in b.steps]
            out.append("\n".join(lines))
        elif t == "graph":
            names = {n.id: n.label for n in b.nodes}
            out.append((f"### {b.title}\n" if b.title else "### Topology\n") + "\n".join(f"- {names.get(e.source, e.source)} —{e.label}→ {names.get(e.target, e.target)} {e.citation or ''}".rstrip() for e in b.edges))
        elif t == "plan":
            out.append(f"### Plan: {b.goal}\n" + "\n".join(f"{i+1}. [{t.status}] {t.title} ({t.agent})" + (f" ← {', '.join(t.depends_on)}" if t.depends_on else "") for i, t in enumerate(b.tasks)))
        elif t == "comparison":
            head = "| Attribute | " + " | ".join(b.subjects) + " |\n|---|" + "---|" * len(b.subjects)
            rows = "\n".join("| " + a + " | " + " | ".join(f"{c.value or '—'} {c.unit or ''} {c.citation or ''}".strip() for c in row) + " |" for a, row in zip(b.attributes, b.cells))
            out.append((f"### {b.title}\n" if b.title else "### Comparison\n") + head + "\n" + rows + ("\n\n" + "\n".join(f"- {d}" for d in b.differences) if b.differences else ""))
        elif t == "limit_gauge":
            marks = ", ".join(f"{m.label} {m.value:g}" for m in b.markers)
            out.append(f"### {b.title or 'Limit check'}\n**{b.entity} — {b.parameter}**: {b.value if b.value is not None else '—'} {b.unit} → **{b.verdict.replace('_', ' ')}**\nMarkers: {marks} {b.unit}\n{b.message}")
        elif t == "conflict":
            lines = [f"### {b.title or 'Documented values'} — {b.subject} / {b.parameter} ({b.status.replace('_', ' ')})"]
            for i, c in enumerate(b.claims):
                star = " ← preferred" if b.preferred_index == i else ""
                lines.append(f"- {c.value} {c.unit or ''} — {c.document_id}, rev {c.revision or '?'}, p.{c.page}, {c.source}{(' [' + c.context + ']') if c.context else ''} {c.citation or ''}{star}")
            if b.resolution:
                lines.append(f"\n{b.resolution}")
            out.append("\n".join(lines))
        elif t == "safety":
            out.append("### Safety\n" + "\n".join(f"- **{f.severity.upper()}**: {f.message} {f.citation or ''}".rstrip() for f in b.flags))
        elif t == "confidence":
            out.append(f"**Confidence:** {b.level} ({b.score:.2f}) — {b.basis}" + ("\n" + "\n".join(f"- {u}" for u in b.uncertainties) if b.uncertainties else ""))
        elif t == "clarification":
            out.append(f"### Clarification needed\n{b.question}\n" + "\n".join(f"- missing: {m}" for m in b.missing) + ("\n" + "\n".join(f"  - option: {o}" for o in b.options) if b.options else ""))
        elif t == "evidence":
            out.append("### Evidence\n" + "\n".join(f"{i.ref} {i.document_id} p.{i.page} ({i.source}): {i.text[:200]}" for i in b.items))
        elif t == "image":
            out.append(f"![{b.caption or ''}]({b.url})")
        elif t == "audit":
            out.append(f"_Audit {b.audit_id}: " + ", ".join(f"{p.name} {p.duration_ms}ms" for p in b.phases) + f"; LLM calls {b.llm_calls}; backend {b.backend}_")
    return "\n\n".join(out)
