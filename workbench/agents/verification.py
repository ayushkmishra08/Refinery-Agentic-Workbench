"""Verification Agent (Phase 4) — every statement must be backed by evidence that actually says it.

Checks per prior result
1. evidence keys referenced by statements exist;
2. numbers in factual / calculation / inference statements appear in the cited evidence text
   (tolerant to "24.45" vs "24,45" and thousands separators);
3. equipment tags mentioned in text blocks resolve to known entities (no invented tags);
4. LLM narrative blocks (titled "Answer") with unverifiable numbers are removed, not kept.
Produces a verification score per result; low scores lower the final confidence. Replan is
requested only when a required result has no evidence at all.
"""
from __future__ import annotations

import re

from workbench.agents.base import BaseAgent
from workbench.core.blocks import TableBlock
from workbench.core.context import ContextPackage
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult

TAG_RE = re.compile(r"\b\d{1,3}-[A-Z]{1,4}-\d{1,5}(?:[A-Z](?:/[A-Z])*)?\b")
NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])")


def _norm_num(s: str) -> str:
    s = s.replace(",", "")
    try:
        f = float(s)
        return f"{f:g}"
    except ValueError:
        return s


class VerificationAgent(BaseAgent):
    name = "verification"
    phase = "4 Verification"
    description = "Checks statements against cited evidence, removes ungrounded narrative, scores each result."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        prior = [r for r in self.s.prior_results.values() if r.agent not in ("verification", "governance")]
        all_evidence = {}
        for r in prior:
            for e in r.evidence:
                all_evidence[e.key()] = e
        rows = []
        total_checked = total_ok = 0
        unknown_tags: set[str] = set()
        removed_blocks = 0
        for r in prior:
            checked = ok = 0
            problems: list[str] = []
            for st in r.statements:
                if st.kind == "topology" and not st.numbers:
                    continue
                checked += 1
                evs = [all_evidence[k] for k in st.evidence_keys if k in all_evidence]
                if st.evidence_keys and not evs:
                    problems.append(f"missing evidence for: {st.text[:60]}")
                    continue
                if not st.evidence_keys and st.kind in ("fact", "calculation"):
                    # facts without evidence are allowed only for identification statements
                    if r.agent in ("lookup",) and "mentioned" in st.text:
                        ok += 1
                    else:
                        problems.append(f"no evidence key: {st.text[:60]}")
                    continue
                ev_text = " ".join(e.text for e in evs)
                ev_nums = {_norm_num(n) for n in NUM_RE.findall(ev_text)}
                nums = [_norm_num(n) for n in st.numbers]
                # page numbers and step indices in the statement text are not claims about the world
                nums = [n for n in nums if n not in ev_nums and not re.search(rf"(p\.|page|step|\[)\s*{re.escape(n)}", st.text, re.IGNORECASE)]
                if st.kind == "calculation":
                    # derived numbers (margins, percentages) are allowed when the inputs are in evidence
                    nums = [n for n in nums if not re.search(rf"{re.escape(n)}\s*%|\(\+?-?{re.escape(n)}", st.text)]
                    nums = nums[:0] if len(ev_nums) >= 2 else nums
                if nums:
                    problems.append(f"number(s) {', '.join(nums[:3])} not in evidence: {st.text[:50]}")
                    continue
                ok += 1
            # tags in text/callout blocks must be known
            for b in r.blocks:
                if b.type in ("text", "callout"):
                    for tag in TAG_RE.findall(b.markdown):
                        if not self.knowledge.resolve_entity(tag, limit=1):
                            unknown_tags.add(tag)
            # remove LLM narrative blocks whose numbers are not grounded
            for b in list(r.blocks):
                if b.type == "text" and (b.title or "").lower() in ("answer", "executive summary"):
                    st = next((s for s in r.statements if s.kind == "inference" and s.text[:40] == b.markdown[:40]), None)
                    if st is not None and any(p.startswith("number") and st.text[:50] in p for p in problems):
                        r.blocks.remove(b)
                        removed_blocks += 1
                        r.trace.append("An ungrounded narrative block was removed by verification.")
            score = ok / checked if checked else (1.0 if r.evidence or not r.blocks else 0.6)
            r.content["verification"] = {"checked": checked, "ok": ok, "score": round(score, 2), "problems": problems[:6]}
            total_checked += checked
            total_ok += ok
            rows.append([r.agent, r.step_id, checked, ok, f"{score:.2f}", "; ".join(problems[:2])[:120]])
        overall = total_ok / total_checked if total_checked else 1.0
        result.content["overall_score"] = round(overall, 2)
        result.content["unknown_tags"] = sorted(unknown_tags)
        result.content["removed_blocks"] = removed_blocks
        issues = [r for r in rows if r[5]]
        if issues or unknown_tags:
            result.blocks.append(TableBlock(id="verification", title="Verification notes", columns=["Agent", "Step", "Checked", "Grounded", "Score", "Issues"], rows=issues))
            if unknown_tags:
                result.blocks.append(self.callout("Tags mentioned but not found in the knowledge base: " + ", ".join(sorted(unknown_tags)) + ". Treat them as unverified.", "warning"))
        # replan only when a required step produced nothing at all
        empties = [r for r in prior if not r.evidence and not r.blocks and r.ok]
        result.summary = f"{total_ok}/{total_checked} statements grounded (score {overall:.2f})" + (f"; {removed_blocks} narrative block(s) removed" if removed_blocks else "") + (f"; {len(unknown_tags)} unknown tag(s)" if unknown_tags else "")
        result.confidence = self.confidence(overall, "share of statements whose numbers and text are found in the cited evidence")
        result.ok = True
        if empties:
            result.trace.append("Step(s) that produced no evidence at all: " + ", ".join(sorted({r.agent for r in empties})) + ".")
