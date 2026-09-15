"""Calculation Agent (Phase 4) — deterministic numeric work on documented claims.

Modes: range (build the min / normal / max / design envelope for a parameter) and
check_value (compare a given value with that envelope). Arithmetic and unit conversion live in
services/calculators; the LLM is never involved.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.agents.retrieval import context_label, predicates_for
from workbench.core.blocks import GaugeMarker, LimitGaugeBlock, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.knowledge import ClaimRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult
from workbench.services.calculators.limits import PREDICATE_FAMILY, check_value, markers_from_claims
from workbench.services.calculators.units import Quantity, family, normalize_unit
from workbench.services.revision_resolver import rank_claims


class CalculationAgent(BaseAgent):
    name = "calculation"
    phase = "4 Execution (Calculation / Limit envelope)"
    description = "Builds the operating envelope from claims and checks values against it."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "range")
        entity = request.primary_entity
        claims = self._claims(request, context)
        if not claims:
            result.missing.append("no documented values for the parameter")
            result.blocks.append(self.callout("No documented value exists for this parameter and equipment, so no envelope can be built. UNKNOWN is reported.", "warning"))
            result.summary = "no values"
            result.confidence = self.confidence(0.2, "no claims")
            result.needs_replan = mode == "check_value" and not step.optional
            result.replan_reason = "no documented limits" if result.needs_replan else None
            return
        docs = {d.document_id: d for d in self.knowledge.documents()}
        claims = rank_claims(claims, docs)
        label = entity.name if entity and entity.name else claims[0].subject
        param = request.parameter or self._param_from_claims(claims)
        if mode == "check_value" and request.quantities:
            return self._check(request, claims, label, param, result)
        return self._range(request, claims, label, param, result)

    # ------------------------------------------------------------------ helpers
    def _claims(self, request: StructuredRequest, context: ContextPackage) -> list[ClaimRecord]:
        prior = self.s.result_of("lookup")
        rows: list[ClaimRecord] = []
        if prior and prior.content.get("claims"):
            rows = [ClaimRecord.model_validate(c) for c in prior.content["claims"]]
        if not rows:
            entity = request.primary_entity
            rows = context.claims_for(entity.entity_uid) if entity and entity.entity_uid else list(context.claims)
            if not rows and entity and entity.entity_uid:
                rows = self.knowledge.entity_claims(entity.entity_uid)
        preds = predicates_for(request.parameter)
        if preds:
            filtered = [c for c in rows if c.predicate in preds]
            if request.parameter == "pressure":
                text = request.original.text.lower()
                loc = "discharge" if "discharge" in text else "suction" if "suction" in text else None
                if loc:
                    at_loc = [c for c in filtered if (c.location or "").lower() == loc]
                    filtered = at_loc or filtered
            rows = filtered or rows
        if request.quantities and request.quantities[0].unit:
            fam = family(request.quantities[0].unit)
            same = [c for c in rows if c.unit and family(c.unit) == fam]
            rows = same or rows
        return [c for c in rows if c.numeric_value is not None]

    @staticmethod
    def _param_from_claims(claims: list[ClaimRecord]) -> str | None:
        """The parameter is the unit family most of the claims belong to (not the first claim's)."""
        from collections import Counter

        fams = Counter(family(c.unit) for c in claims if c.unit and family(c.unit) != "unknown")
        if not fams:
            for c in claims:
                fam = PREDICATE_FAMILY.get(c.predicate)
                if fam:
                    return {"volumetric_flow": "flow_rate", "mass_flow": "flow_rate"}.get(fam, fam)
            return None
        fam = fams.most_common(1)[0][0]
        return {"volumetric_flow": "flow_rate", "mass_flow": "flow_rate"}.get(fam, fam)

    def _range(self, request, claims, label, param, result) -> None:
        # one envelope per unit family: prefer the requested parameter's family, else the family with most claims
        from collections import Counter

        fam_counts = Counter(family(c.unit) for c in claims if c.unit)
        wanted = {"flow_rate": "volumetric_flow", "pressure": "pressure", "design_pressure": "pressure", "temperature": "temperature", "design_temperature": "temperature"}.get(param or "")
        fam = wanted if wanted in fam_counts else (fam_counts.most_common(1)[0][0] if fam_counts else "unknown")
        fam_claims = [c for c in claims if c.unit and family(c.unit) == fam] or claims
        unit_counts = Counter(c.unit for c in fam_claims if c.unit)
        unit = unit_counts.most_common(1)[0][0] if unit_counts else None
        claims = fam_claims
        markers = markers_from_claims(claims, target_unit=unit)
        rows, cits = [], []
        for m in markers:
            key = self.cite_claim(result, m.claim)
            rows.append([m.label, f"{m.value:g}", m.unit, context_label(m.claim) or "—", f"p.{m.claim.page}" if m.claim.page else ""])
            cits.append([key])
            self.statement(result, f"{label} {param or m.claim.predicate} {m.label} = {m.value:g} {m.unit}", [key])
        gauge = LimitGaugeBlock(id="envelope", title=f"Operating envelope — {label} ({(param or 'value').replace('_', ' ')})", entity=label, parameter=(param or claims[0].predicate).replace("_", " "),
                                unit=unit or "", value=None, markers=[GaugeMarker(label=m.label, value=m.value, citation=c[0]) for m, c in zip(markers, cits)], verdict="unknown",
                                message="Documented values only; no measured value was supplied.", citations=[c[0] for c in cits])
        result.blocks.append(gauge)
        result.blocks.append(TableBlock(id="envelope-table", title="Documented limit values", columns=["Role", "Value", "Unit", "Context", "Page"], rows=rows, row_citations=cits))
        by = {m.label: m for m in markers}
        if "normal" in by and ("maximum" in by or "design" in by):
            hi = by.get("maximum") or by.get("design")
            pct = (hi.value - by["normal"].value) / by["normal"].value * 100 if by["normal"].value else 0
            result.blocks.append(self.text_block(f"Margin between normal ({by['normal'].value:g} {by['normal'].unit}) and {hi.label} ({hi.value:g} {hi.unit}): **{hi.value - by['normal'].value:g} {hi.unit} ({pct:+.1f}%)**.", citations=[cits[markers.index(by['normal'])][0], cits[markers.index(hi)][0]]))
            self.statement(result, f"margin normal to {hi.label} is {hi.value - by['normal'].value:g} {hi.unit}", [cits[markers.index(by['normal'])][0], cits[markers.index(hi)][0]], kind="calculation")
        result.content["markers"] = [{"label": m.label, "value": m.value, "unit": m.unit, "claim_id": m.claim.claim_id} for m in markers]
        result.content["limit_claims"] = [m.claim.model_dump(mode="json") for m in markers]
        result.summary = f"envelope with {len(markers)} marker(s): " + ", ".join(f"{m.label} {m.value:g}" for m in markers[:4])
        result.confidence = self.confidence(0.85 if len(markers) >= 2 else 0.6, "values from tables/specifications with roles", ["single documented value; no range" if len(markers) < 2 else ""] if len(markers) < 2 else [])

    def _check(self, request, claims, label, param, result) -> None:
        q = request.quantities[0]
        unit = q.unit or next((c.unit for c in claims if c.unit), "")
        value = Quantity(q.value, normalize_unit(unit) if unit else "", None)
        if not q.unit:
            result.blocks.append(self.callout(f"The value {q.value:g} was given without a unit; it is assumed to be in **{unit}** (the unit of the documented values). Confirm before acting.", "warning"))
        verdict = check_value(value, claims)
        cits = []
        for m in verdict.markers:
            key = self.cite_claim(result, m.claim)
            cits.append(key)
            self.statement(result, f"{label} {m.label} {param or m.claim.predicate} = {m.value:g} {m.unit}", [key])
        result.blocks.append(LimitGaugeBlock(id="limit-check", title=f"Limit check — {label}", entity=label, parameter=(param or claims[0].predicate).replace("_", " "), unit=verdict.unit,
                                             value=value.value, markers=[GaugeMarker(label=m.label, value=m.value, citation=k) for m, k in zip(verdict.markers, cits)],
                                             verdict=verdict.verdict, message=verdict.message, citations=cits))
        level = {"within_normal": "success", "within_design": "warning", "outside_design": "danger", "unknown": "info"}[verdict.verdict]
        result.blocks.append(self.callout(f"**{value.value:g} {value.unit}** → **{verdict.verdict.replace('_', ' ')}**. {verdict.message}", level, citations=cits))
        self.statement(result, f"{value.value:g} {value.unit} is {verdict.verdict.replace('_', ' ')}", cits, kind="calculation")
        result.content["verdict"] = verdict.verdict
        result.content["deviations"] = verdict.deviations
        result.content["markers"] = [{"label": m.label, "value": m.value, "unit": m.unit, "claim_id": m.claim.claim_id} for m in verdict.markers]
        result.summary = f"{value.value:g} {value.unit}: {verdict.verdict.replace('_', ' ')}"
        result.confidence = self.confidence(0.9 if verdict.verdict != "unknown" and q.unit else 0.6 if verdict.verdict != "unknown" else 0.3,
                                            "deterministic comparison against documented markers", ["unit assumed from the documents"] if not q.unit else [])
