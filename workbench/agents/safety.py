"""Safety Agent (cross-cutting): Phase 0 gate, planning review, execution review, Phase 5 checks.

Modes
- answer            : documented precautions / isolation / PPE / interlocks for the equipment or action
- review_procedure  : hazard words in steps -> per-step warnings; adds documented precautions
- review_actions    : corrective actions that involve opening / isolating / bypassing -> flags
- review_limits     : consequences of operating outside the envelope
- restricted        : bypass / defeat protection -> documented authorization path only, HITL

The agent never invents a precaution: every flag cites a sentence, a claim or a procedure step.
"""
from __future__ import annotations

import re

from workbench.agents.base import BaseAgent
from workbench.core.blocks import SafetyBlock, SafetyFlagItem, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.evidence import SafetyFlag
from workbench.core.plan import PlanStep
from workbench.core.request import SafetyStatus, StructuredRequest
from workbench.core.result import AgentResult
from workbench.services.evidence_store import evidence_from_claim, evidence_from_step

PRECAUTION_RE = re.compile(
    r"[^.\n]*\b(never|do not|don't|must not|shall not|wear|ppe|breathing apparatus|face shield|gloves|goggles|isolat\w*|blind\w*|depressuri[sz]\w*|purg\w*|"
    r"steam(ed)? out|gas test|work permit|permit to work|permit|hot work|confined space|lock(ed)? out|tag(ged)? out|h2s|toxic|flammable|explosi\w*|hazard\w*|caution|warning|danger|"
    r"interlock\w*|trip\w*|psv|relief valve|safeguard\w*|emergency|fire ?water|extinguisher|first aid|escape|positive isolation|energy isolation|electrical(ly)? (isolat|disconnect)\w*|"
    r"hot (surface|oil|line)|scald\w*|burn\w*|pressuri[sz]ed|vent(ed|ing)? (before|prior)|drain(ed|ing)? (before|prior|completely|thoroughly))\b[^.\n]*\.?",
    re.IGNORECASE,
)
SEVERITY_RULES = [
    (r"\b(danger|fatal|explosion|explosive|h2s|toxic|asphyxi|fire|never|must not|shall not)\b", "danger"),
    (r"\b(warning|do not|don't|hot work|confined space|trip|relief|psv|isolat|blind|depressuri|lock)\b", "warning"),
    (r"\b(caution|should|ensure|permit|ppe|gloves|goggles|face shield|apparatus|alarm|slowly|gradually)\b", "caution"),
]
ACTION_HAZARD_RE = re.compile(r"\b(open|opening|isolat\w*|bypass\w*|blind\w*|drain\w*|vent\w*|depressuri\w*|start\w*|stop\w*|switch\w*|manual(ly)?|override|reset|increase|decrease|raise|reduce)\b", re.IGNORECASE)
AUTHORIZATION_RE = re.compile(r"[^.\n]*\b(authori[sz]\w*|approval|approved by|permission|shift in-?charge|division head|management of change|moc|deviation permit|written)\b[^.\n]*\.?", re.IGNORECASE)


def severity_for(text: str) -> str:
    t = text.lower()
    for rx, sev in SEVERITY_RULES:
        if re.search(rx, t):
            return sev
    return "info"


class SafetyAgent(BaseAgent):
    name = "safety"
    phase = "cross-cutting (0 gate, 3, 4, 5)"
    description = "Retrieves documented precautions, reviews procedures/actions/limits, enforces the restricted path."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "answer")
        if request.safety_status == SafetyStatus.RESTRICTED:
            return self._restricted(request, context, result)
        if mode == "review_procedure":
            return self._review_procedure(request, context, result)
        if mode == "review_actions":
            return self._review_actions(request, context, result)
        if mode == "review_limits":
            return self._review_limits(request, context, result)
        return self._answer(request, context, result)

    # ------------------------------------------------------------------ retrieval of precautions
    def _precaution_flags(self, request: StructuredRequest, context: ContextPackage, result: AgentResult, k: int = 8, extra_query: str = "") -> list[SafetyFlagItem]:
        uids = request.entity_uids()
        kws = [e.name.split(" (")[0] for e in request.entities if e.name] + [e.canonical_tag for e in request.entities if e.canonical_tag]
        action_words = " ".join(w for w in [request.action or "", "maintenance" if re.search(r"maintenance|working on|work on|repair|overhaul", request.original.text, re.IGNORECASE) else "",
                                             "isolation blinding permit" if re.search(r"isolat|maintenance|open|working on", request.original.text, re.IGNORECASE) else ""] if w)
        class_word = (request.primary_entity.entity_type or "").lower() if request.primary_entity else ""
        query = f"{request.original.text} {extra_query} {action_words} precaution".strip()
        chunks: list = []
        if uids:
            # pass 1: text that mentions the equipment itself (its section, its procedures)
            chunks += [c for c in self.knowledge.chunks_for_entity(uids[0], limit=10) if c.chunk_type in ("safety", "procedure", "equipment", "narrative", "upset")]
            chunks += self.knowledge.search_chunks(f"{query} {class_word}", k=6, chunk_types=["safety", "procedure", "equipment", "narrative"], entity_uids=uids)
        # pass 2: safe-work practices for the action / equipment class (permits, isolation, PPE)
        chunks += self.knowledge.search_chunks(f"{class_word} {action_words} {extra_query} safe work permit isolation PPE".strip(), k=k, chunk_types=["safety", "procedure", "narrative"], chapters=[31, 27, 23, 32])
        _seen_ids: set[str] = set()
        chunks = [c for c in chunks if not (c.chunk_id in _seen_ids or _seen_ids.add(c.chunk_id))]
        flags: list[SafetyFlagItem] = []
        seen: set[str] = set()
        specific: list[SafetyFlagItem] = []
        generic: list[SafetyFlagItem] = []
        class_word = (request.primary_entity.entity_type or "").lower() if request.primary_entity else ""
        for ch in chunks:
            text = ch.text
            text_mentions_entity = any(k.lower() in text.lower() for k in kws) if kws else True
            for m in PRECAUTION_RE.finditer(text):
                sent = re.sub(r"\s+", " ", m.group(0)).strip(" -•")
                if len(sent) < 25 or len(sent) > 420 or sent.lower() in seen or sent.startswith("#"):
                    continue
                if re.match(r"^\d+(\.\d+)*\s+[A-Z][A-Z &/:-]{6,}$", sent) or sent.isupper():
                    continue                                       # section headings are not precautions
                sent_mentions = any(k.lower() in sent.lower() for k in kws) if kws else False
                if kws and not text_mentions_entity and ch.chunk_type != "safety":
                    continue
                if kws and not text_mentions_entity and not sent_mentions and class_word and class_word not in sent.lower() \
                        and not re.search(r"permit|isolat|blind|lock|ppe|goggles|gloves|hot work|confined|depressuri|drain|purge|steam out|gas test", sent, re.IGNORECASE):
                    continue                                       # generic hazard prose (e.g. LPG chapter) that is not about this work
                seen.add(sent.lower())
                key = self.cite_chunk(result, ch, sent)
                sev = severity_for(sent)
                item = SafetyFlagItem(severity=sev, message=sent, citation=key, requires_authorization=bool(AUTHORIZATION_RE.search(sent)))
                self.statement(result, sent, [key])
                if sent_mentions or (class_word and class_word in sent.lower()):
                    specific.append(item)
                elif re.search(r"permit|isolat|blind|lock|ppe|goggles|gloves|hot work|confined|depressuri|drain|purge|steam out|gas test|trip|interlock|psv|relief", sent, re.IGNORECASE):
                    generic.append(item)
            if len(specific) >= 10:
                break
        flags = specific[:10] + generic[: max(0, 12 - len(specific[:10]))]
        # trip / alarm / relief claims for the entity
        for uid in uids:
            for c in self.knowledge.entity_claims(uid):
                if (c.parameter_role or "") in ("trip", "alarm", "relief") or c.predicate in ("set_pressure", "relief_pressure", "trip_setting"):
                    key = self.cite(result, evidence_from_claim(c))
                    flags.append(SafetyFlagItem(severity="warning", message=f"{c.subject}: {c.predicate.replace('_', ' ')} {c.parameter_role or ''} = {c.value} {c.unit or ''}".strip(), citation=key))
        order = {"danger": 0, "warning": 1, "caution": 2, "info": 3}
        flags.sort(key=lambda f: order.get(f.severity, 3))
        return flags

    def _to_result_flags(self, flags: list[SafetyFlagItem], result: AgentResult) -> None:
        for f in flags:
            ev = next((e for e in result.evidence if e.key() == f.citation), None)
            result.safety_flags.append(SafetyFlag(severity=f.severity, message=f.message, evidence=[ev] if ev else [], requires_authorization=f.requires_authorization))

    # ------------------------------------------------------------------ modes
    def _answer(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        flags = self._precaution_flags(request, context, result, k=10)
        procs = [p for p in (context.procedures or []) if p.procedure_type in ("safety", "isolation", "emergency", "shutdown")][:3]
        if not flags and not procs:
            result.missing.append("no documented safety requirement found for the request")
            result.blocks.append(self.callout("No documented precaution, isolation requirement or interlock was found for this equipment/action. Follow the site permit-to-work system; nothing is added from general knowledge.", "warning"))
            result.summary = "no documented precautions"
            result.confidence = self.confidence(0.2, "nothing retrieved")
            return
        entity = request.primary_entity
        title = f"Documented safety requirements" + (f" — {entity.name}" if entity and entity.name else "")
        result.blocks.append(SafetyBlock(id="safety", title=title, flags=flags[:12], citations=[f.citation for f in flags[:12] if f.citation]))
        if procs:
            rows = [[p.title[:80], p.procedure_type, f"p.{p.page_start}", len(p.steps)] for p in procs]
            result.blocks.append(TableBlock(id="safety-procs", title="Related safety / isolation procedures", columns=["Procedure", "Type", "Page", "Steps"], rows=rows))
            result.content["procedure_ids"] = [p.procedure_id for p in procs]
        self._to_result_flags(flags[:12], result)
        result.content["flags"] = [f.model_dump(mode="json") for f in flags[:12]]
        result.summary = f"{len(flags[:12])} documented safety item(s)" + (f", {len(procs)} procedure(s)" if procs else "")
        result.confidence = self.confidence(0.75 if flags else 0.5, "precaution sentences quoted with page references", ["general refinery practice is not added; only documented items"])

    def _review_procedure(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        proc_res = self.s.result_of("procedure", "steps") or self.s.result_of("procedure")
        step_flags: list[SafetyFlagItem] = []
        if proc_res:
            for b in proc_res.blocks:
                if b.type == "steps":
                    for st in b.steps:
                        if st.warnings:
                            sev = "danger" if any(w in ("danger", "never", "must not", "toxic", "h2s", "fire", "explosion") for w in st.warnings) else "warning"
                            step_flags.append(SafetyFlagItem(severity=sev, message=f"Step {st.sequence}: {st.text[:200]}", citation=st.citation))
        general = self._precaution_flags(request, context, result, k=6)
        flags = step_flags[:8] + [g for g in general if g.severity in ("danger", "warning")][:6]
        if not flags:
            result.blocks.append(self.callout("No hazard wording was found in the procedure steps and no additional documented precaution applies. Standard permit-to-work rules still apply.", "info"))
            result.summary = "no additional safety flags"
            result.confidence = self.confidence(0.6, "steps scanned for hazard words; precaution search returned nothing")
            return
        result.blocks.append(SafetyBlock(id="safety-review", title="Safety review of the procedure", flags=flags, citations=[f.citation for f in flags if f.citation]))
        self._to_result_flags(flags, result)
        result.content["flags"] = [f.model_dump(mode="json") for f in flags]
        result.summary = f"{len(step_flags[:8])} step warning(s), {len(flags) - len(step_flags[:8])} documented precaution(s)"
        result.confidence = self.confidence(0.75, "warnings are the procedure's own wording plus quoted precautions")

    def _review_actions(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        diag = self.s.result_of("diagnostic", "actions") or self.s.result_of("diagnostic")
        actions: list[str] = []
        if diag:
            actions = diag.content.get("actions", []) or diag.content.get("checks", [])
        flags: list[SafetyFlagItem] = []
        for a in actions[:12]:
            if ACTION_HAZARD_RE.search(a):
                flags.append(SafetyFlagItem(severity="caution", message=f"Action involves a field intervention — confirm permit / isolation before: {a[:180]}", citation=None))
        general = self._precaution_flags(request, context, result, k=6)
        flags += [g for g in general if g.severity in ("danger", "warning")][:6]
        if not flags:
            result.blocks.append(self.callout("The recommended checks are observational; no documented safety restriction applies beyond normal operating practice.", "info"))
            result.summary = "no restrictions on actions"
            result.confidence = self.confidence(0.6, "actions scanned; no hazard wording")
            return
        result.blocks.append(SafetyBlock(id="safety-actions", title="Safety restrictions on the recommended actions", flags=flags, citations=[f.citation for f in flags if f.citation]))
        self._to_result_flags(flags, result)
        result.content["flags"] = [f.model_dump(mode="json") for f in flags]
        result.summary = f"{len(flags)} safety restriction(s)"
        result.confidence = self.confidence(0.7, "hazard wording in actions plus quoted precautions")

    def _review_limits(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        calc = self.s.result_of("calculation")
        verdict = calc.content.get("verdict") if calc else None
        flags: list[SafetyFlagItem] = []
        if verdict in ("outside_design",):
            flags.append(SafetyFlagItem(severity="danger", message="The value is outside the documented design/trip envelope. Operating there is not covered by the manual; stop and revert to the documented range.", citation=None, requires_authorization=True))
        elif verdict == "within_design":
            flags.append(SafetyFlagItem(severity="warning", message="The value is outside the normal operating range but within design. Continued operation there requires supervision and a documented reason.", citation=None))
        if verdict == "within_normal":
            result.summary = "value within the normal range; no safety implication"
            result.confidence = self.confidence(0.7, "value inside the documented normal range")
            return
        general = self._precaution_flags(request, context, result, k=6, extra_query="consequences of deviation")
        flags += [g for g in general if g.severity in ("danger", "warning", "caution")][:6]
        if not flags:
            result.summary = "no safety flags"
            result.confidence = self.confidence(0.6, "value inside normal range")
            return
        result.blocks.append(SafetyBlock(id="safety-limits", title="Safety implications", flags=flags, citations=[f.citation for f in flags if f.citation]))
        self._to_result_flags(flags, result)
        result.content["flags"] = [f.model_dump(mode="json") for f in flags]
        result.summary = f"{len(flags)} safety implication(s)"
        result.confidence = self.confidence(0.7, "verdict from documented limits plus quoted consequences")

    def _restricted(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        """Bypass / defeat a protection: no instructions, only the documented authorization path."""
        uids = request.entity_uids()
        chunks = self.knowledge.search_chunks("bypass interlock trip protection authorization approval management of change deviation", k=8,
                                              chunk_types=["safety", "narrative", "procedure", "control"], chapters=[27, 31, 23, 7, 30, 1], entity_uids=uids or None)
        flags: list[SafetyFlagItem] = [SafetyFlagItem(severity="danger", message="Request to bypass / defeat a protective function. The workbench does not provide bypass instructions. Only the documented authorization route is shown below, and a human review is required.", requires_authorization=True)]
        for ch in chunks:
            for m in AUTHORIZATION_RE.finditer(ch.text):
                sent = re.sub(r"\s+", " ", m.group(0)).strip()
                if 30 <= len(sent) <= 400 and re.search(r"bypass|interlock|trip|protection|alarm|deviation|safeguard", sent, re.IGNORECASE):
                    key = self.cite_chunk(result, ch, sent)
                    flags.append(SafetyFlagItem(severity="warning", message=sent, citation=key, requires_authorization=True))
                    self.statement(result, sent, [key])
            if len(flags) >= 8:
                break
        sis = self.knowledge.standing_instructions("bypass interlock protection deviation")
        if sis:
            result.blocks.append(TableBlock(id="sis", title="Standing instructions that may apply", columns=["Number", "Title", "Status", "Page"], rows=[[s.number, s.title, s.status or "", s.page or ""] for s in sis[:8]]))
        result.blocks.insert(0, SafetyBlock(id="restricted", title="Protection bypass — restricted request", flags=flags, citations=[f.citation for f in flags if f.citation]))
        self._to_result_flags(flags, result)
        result.content["restricted"] = True
        result.content["flags"] = [f.model_dump(mode="json") for f in flags]
        result.summary = "restricted: bypass request routed to documented authorization path"
        result.confidence = self.confidence(0.8, "policy decision; authorization sentences quoted where documented")
