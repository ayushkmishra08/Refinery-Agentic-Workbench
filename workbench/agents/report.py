"""Report Agent (Phase 4/5) — assembles an engineering report from the other agents' results.

The report reuses the blocks the specialist agents produced (values, topology, procedures,
safety, conflicts) under ordered section headings, adds a short executive summary (LLM when
available, otherwise composed from the statements), and saves a Markdown copy under
data/workbench/reports/. Governance uses the report's block order for the final answer.
"""
from __future__ import annotations

import re
import time

from workbench.agents.base import BaseAgent, blocks_to_markdown, usable_narrative
from workbench.core.blocks import Block, TextBlock
from workbench.core.context import ContextPackage
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult

SECTION_ORDER = [
    ("Equipment identified", ["lookup:identify"]),
    ("Documented values", ["lookup", "calculation"]),
    ("Process topology", ["graph"]),
    ("Description", ["explanation"]),
    ("Procedures", ["procedure"]),
    ("Diagnosis", ["diagnostic"]),
    ("Comparison", ["comparison"]),
    ("Revisions and conflicts", ["revision_conflict"]),
    ("Documents and references", ["cross_document"]),
    ("Safety", ["safety"]),
]


class ReportAgent(BaseAgent):
    name = "report"
    phase = "4/5 Report preparation / generation"
    description = "Assembles prior results into an ordered engineering report and saves a Markdown copy."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        prior = [r for r in self.s.prior_results.values() if r.agent not in ("report", "verification", "governance") and r.blocks]
        if not prior:
            result.missing.append("no content gathered for the report")
            result.blocks.append(self.callout("No documented content was gathered, so no report can be written.", "warning"))
            result.summary = "empty report"
            result.confidence = self.confidence(0.2, "no content")
            return
        title = self._title(request)
        blocks: list[Block] = []
        used: set[int] = set()
        for heading, agents in SECTION_ORDER:
            section_blocks: list[Block] = []
            for r in prior:
                key = f"{r.agent}:{r.step_id}" if r.agent == "lookup" and r.step_id in ("scope", "identify") else r.agent
                if (key in agents or r.agent in agents) and id(r) not in used:
                    if r.agent == "lookup" and "lookup:identify" in agents and r.step_id not in ("scope", "identify"):
                        continue
                    if r.agent == "lookup" and "lookup" in agents and r.step_id in ("scope", "identify"):
                        continue
                    section_blocks.extend(b.model_copy(deep=True) for b in r.blocks if b.type not in ("audit", "confidence"))
                    used.add(id(r))
            if section_blocks:
                blocks.append(TextBlock(id=f"h-{heading.lower().replace(' ', '-')}", markdown=f"## {heading}"))
                blocks.extend(section_blocks)
        for r in prior:
            if id(r) not in used:
                blocks.extend(b.model_copy(deep=True) for b in r.blocks if b.type not in ("audit", "confidence"))
        summary = self._summary(request, prior, result)
        head = [TextBlock(id="report-title", markdown=f"# {title}\n\n*Generated {time.strftime('%Y-%m-%d %H:%M')} from {', '.join(d.document_id for d in self.knowledge.documents())}. Every value cites its page; nothing is added from outside the documents.*"),
                TextBlock(id="report-summary", title="Executive summary", markdown=summary)]
        result.blocks = head + blocks
        result.content["replace_blocks"] = True
        path = self.cfg.paths.reports_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', title.lower())[:50]}.md"
        try:
            path.write_text(blocks_to_markdown(result.blocks), encoding="utf-8")
            result.content["report_path"] = str(path)
        except Exception as exc:  # report saving must never fail the answer
            result.trace.append(f"The report markdown could not be written to disk ({exc}); it is still returned in the answer.")
        result.summary = f"report with {len(blocks)} block(s) in {sum(1 for b in blocks if b.type == 'text' and b.markdown.startswith('## '))} section(s)"
        result.confidence = self.confidence(min(0.9, sum(r.confidence.score for r in prior) / max(1, len(prior))), "aggregate of the section confidences")

    def _title(self, request: StructuredRequest) -> str:
        ents = ", ".join(e.name.split(" (")[0] for e in request.entities if e.name)
        text = request.original.text.strip().rstrip(".?!")
        m = re.search(r"report (on|about|explaining|for) (.+)", text, re.IGNORECASE)
        subject = m.group(2) if m else (ents or text)
        return f"Engineering report — {subject[:90]}"

    def _summary(self, request: StructuredRequest, prior: list[AgentResult], result: AgentResult) -> str:
        facts = []
        for r in prior:
            for st in r.statements[:4]:
                if st.kind in ("fact", "calculation") and st.evidence_keys:
                    facts.append(st.text)
        facts = list(dict.fromkeys(facts))[:12]
        missing = [m for r in prior for m in r.missing]
        llm = self.llm_text("report_summary", result, max_tokens=320, purpose="report_summary", request=request.original.text, facts="\n".join(f"- {f}" for f in facts),
                            missing="\n".join(f"- {m}" for m in missing) or "- none") if facts else None
        if llm and usable_narrative(llm, request.original.text, min_words=25):
            return llm
        if llm:
            result.trace.append("The model's executive summary was not usable (request restated or task narrated), so the deterministic fact list is used instead.")
        lines = [f"This report answers: *{request.original.text.strip()}*.", ""]
        lines += [f"- {f}" for f in facts[:8]]
        if missing:
            lines += ["", "Not documented: " + "; ".join(missing[:4]) + "."]
        return "\n".join(lines)
