"""Context Resolver (Phase 0/1) — turns text into a StructuredRequest.

Entity & equipment tag resolution (knowledge-layer canonical tags, aliases, fuzzy names,
session memory for "it / this pump"), scenario and operating-mode detection, numeric values
with units, symptom parsing, action detection, ambiguity detection and evidence requirements.
Deterministic; the LLM is only asked when nothing resolved and the sentence still names
something that looks like equipment (one short call). In ``clarify`` mode it produces the
ClarificationBlock for ambiguous requests.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from workbench.agents.base import BaseAgent
from workbench.core.blocks import ClarificationBlock
from workbench.core.context import ContextPackage
from workbench.core.plan import PlanStep
from workbench.core.request import (
    ClassifierOutput,
    QuantityMention,
    ResolvedEntity,
    SafetyStatus,
    StructuredRequest,
    Symptom,
    TaskType,
    UserRequest,
)
from workbench.core.result import AgentResult
from workbench.orchestration.router import SAFETY_REVIEWED
from workbench.services.calculators.units import PARAMETER_FOR_FAMILY, family, find_quantities
from workbench.services.index.builder import EQUIPMENT_WORDS, NAMED_EQUIPMENT_RE, SPECIFIC_SINGLE_WORDS, norm_alias

try:
    from knowledge_layer.entity_identity import find_tags
except Exception:  # pragma: no cover
    def find_tags(text: str) -> list[str]:  # type: ignore
        return re.findall(r"\b\d{1,3}-[A-Z]{1,4}-\d{1,5}[A-Z/]*\b", text)

SCENARIOS = [
    (r"\bbasrah\b", "Basrah"), (r"\bbombay high\b|\bbh crude\b|\bbh mode\b", "Bombay High"), (r"\bpg mode\b|\bpersian gulf\b", "PG mode"),
    (r"\bkuwait\b", "Kuwait"), (r"\bkirkuk\b", "Kirkuk"), (r"\barab (light|heavy|mix)\b", "Arab"), (r"\bsko (operation|mode)\b", "SKO operation"),
    (r"\benhanced (capacity|case)\b|\b3\.2 mmtpa\b", "enhanced"), (r"\bdesign case\b|\b3\.0 mmtpa\b", "design"),
]
OPERATING_MODES = [
    (r"\bnormal operation\b|\bnormal (operating )?(configuration|condition)s?\b", "normal_operation"), (r"\btemporary (operation|configuration)s?\b", "temporary"),
    (r"\bstart-?up\b|\bstarting\b|\blight-?off\b|\bcommissioning\b|\brestart\b|\bre-?start\b", "startup"), (r"\bshut-?down\b|\bshutting down\b|\bdecommission", "shutdown"),
    (r"\bemergency\b|\besd\b|\bpower failure\b", "emergency"), (r"\bupset\b|\bdeviation\b", "upset"), (r"\bmaintenance\b|\bt&i\b|\bturnaround\b|\binspection\b", "maintenance"),
]
ACTIONS = [
    (r"\bchange ?over\b|\bchange-?over\b|\bswitch ?over\b|\bswap\b|\bto the standby\b", "changeover"), (r"\brestart(ing)?\b|\bre-?start(ing)?\b", "restart"),
    (r"\bstart-?up\b|\bstart(ing)?\b|\blight-?off\b|\bbring(ing)? .* (in|into|on) (service|line|stream)\b|\bcommission(ing)?\b", "startup"),
    (r"\bshut-?down\b|\bshutting down\b|\bstop(ping)?\b|\btake .* out of service\b|\bde-?commission", "shutdown"),
    (r"\bisolat(e|ion|ing)\b|\bblind(ing)?\b|\bhand(ing)? over\b", "isolation"), (r"\bmaintenance\b|\boverhaul\b|\brepair\b", "maintenance"),
    (r"\binspect(ion|ing)?\b", "inspection"), (r"\bsteam ?out\b|\bsteaming\b", "steam_out"), (r"\bwater wash(ing)?\b", "water_wash"), (r"\bsampl(e|ing)\b", "sampling"),
    (r"\bbypass(ing)?\b|\boverride\b|\bdefeat\b|\binhibit\b", "bypass"), (r"\bopen(ing)? (this |the )?(equipment|vessel|exchanger|column|drum|manway)\b", "opening"),
]
PARAMETERS = [
    (r"\bdischarge pressure\b", "pressure", "discharge"), (r"\bsuction pressure\b", "pressure", "suction"), (r"\bdesign pressure\b", "design_pressure", None),
    (r"\bdesign temperature\b", "design_temperature", None), (r"\b(column|tower|vessel|drum|system|operating)? ?pressure\b", "pressure", None),
    (r"\b(outlet|inlet|top|bottom|skin|coil|flash zone|transfer line|cot)? ?temperature\b", "temperature", None), (r"\bflow ?rate\b|\bflow\b|\bthroughput\b|\bcapacity\b|\bcharge rate\b", "flow_rate", None),
    (r"\blevel\b", "level", None), (r"\bspeed\b|\brpm\b", "speed", None), (r"\bdifferential head\b|\bhead\b", "head", None), (r"\bnpsh\b", "npsh", None),
    (r"\bdraft\b|\bdraught\b", "draft", None), (r"\bexcess (o2|oxygen)\b|\boxygen\b", "oxygen", None), (r"\bvacuum\b(?!\s+(heater|column|section|unit|pump|ejector|furnace|tower|distillation|system|residue|bottom))", "vacuum", None), (r"\bduty\b|\bheat duty\b", "duty", None),
]
SYMPTOM_RE = re.compile(
    r"(?P<var>(?:[a-z][a-z /-]{2,40}?)?(?:pressure|temperature|flow|level|vacuum|performance|draft|vibration|current|amperage|speed|delta ?p|differential pressure|suction|discharge))\s+"
    r"(?:is|are|has|have|to the [a-z ]+ has)?\s*(?:been )?(?P<dir>dropping|falling|decreasing|decreased|reducing|reduced|low(?:er)?(?: than normal)?|increasing|increased|rising|high(?:er)?(?: than normal)?|"
    r"fluctuating|hunting|cycling|unstable|not (?:being )?achieved|poor|zero|lost|lower than normal|higher than normal|beyond the normal range|above normal|below normal)",
    re.IGNORECASE,
)
SIMPLE_SYMPTOMS = [
    (r"\bpoor performance\b|\bperforming poorly\b|\bnot performing\b", "performance", "poor"), (r"\bexpected flow is not being achieved\b|\bno flow\b|\blow flow\b", "flow", "low"),
    (r"\brepeated trips?\b|\bkeeps tripping\b|\btripped\b|\btripping\b", "trip", "trip"), (r"\blosing suction\b|\bloss of suction\b|\bcavitat", "suction", "lost"),
    (r"\bfailures?\b", "failure", "failure"), (r"\bleak(s|ing|age)?\b", "leak", "leak"), (r"\bvibration\b", "vibration", "high"),
]
PRONOUN_RE = re.compile(r"\b(it|this|that|these|those|this (one|equipment|pump|column|heater|vessel|valve|exchanger|instrument|protection|chemical)|the same (equipment|pump)|which one|the (pump|heater|column|equipment|vessel|unit|section))\b", re.IGNORECASE)
GENERIC_EQUIPMENT_RE = re.compile(r"\b(the|this|that) (pump|heater|column|vessel|drum|exchanger|valve|instrument|compressor|furnace|tower|equipment)\b", re.IGNORECASE)
ENTITY_REQUIRED = {TaskType.LOOKUP, TaskType.PROCEDURE, TaskType.TROUBLESHOOTING, TaskType.LIMITS, TaskType.MULTI_HOP, TaskType.COMPARISON, TaskType.CONFLICT, TaskType.PROVENANCE}
EVIDENCE_REQUIREMENTS = {
    TaskType.LOOKUP: ["documented value(s) with page reference", "context of each value (role/location/scenario)"],
    TaskType.MULTI_HOP: ["relationships with the sentence they come from", "distinction between documented and inferred links"],
    TaskType.PROCEDURE: ["ordered steps from the manual", "prerequisites / pre-checks", "safety notes and warnings", "referenced procedures"],
    TaskType.TROUBLESHOOTING: ["normal operating range", "documented upset / deviation section", "documented causes", "diagnostic checks", "corrective actions", "safety restrictions"],
    TaskType.LIMITS: ["normal / minimum / maximum values", "design or mechanical limits", "applicable revision", "consequences of deviation"],
    TaskType.EXPLANATION: ["process description text", "operating principle", "connected equipment"],
    TaskType.SAFETY: ["documented precautions", "isolation requirements", "PPE", "interlocks / trips", "authorization requirements"],
    TaskType.COMPARISON: ["values for each subject on matching context", "source of each value"],
    TaskType.CONFLICT: ["every documented value", "revision and source of each", "authority ranking"],
    TaskType.PROVENANCE: ["every documented value", "document, revision, page for each"],
    TaskType.PLANNING: ["relevant procedures", "operating envelope", "safety constraints", "missing data"],
    TaskType.REPORT: ["documented facts with citations", "topology", "procedures"],
    TaskType.CROSS_DOCUMENT: ["matching sections", "cross references", "standing instructions", "referenced documents"],
    TaskType.AMBIGUOUS: [],
}


class LLMEntityGuess(BaseModel):
    equipment_mentions: list[str] = Field(default_factory=list, description="Equipment names or tags mentioned, verbatim")
    parameter: str | None = None
    action: str | None = None


class ContextResolverAgent(BaseAgent):
    name = "context_resolver"
    phase = "0/1 Understanding"
    description = "Resolves entities, scenario, values, symptom and ambiguity into a StructuredRequest."

    # ------------------------------------------------------------------ main API (called by the orchestrator)
    def resolve(self, request: UserRequest, classification: ClassifierOutput, session, result: AgentResult) -> StructuredRequest:
        text = request.text
        req = StructuredRequest(original=request, task_type=classification.task_type, secondary_task_types=list(classification.secondary),
                                intent=classification.intent, classifier=classification)
        req.entities, req.unresolved_mentions = self._resolve_entities(text, result)
        req.quantities = self._quantities(text)
        req.parameter, location = self._parameter(text)
        req.symptom = self._symptom(text)
        if req.symptom and not req.parameter:
            req.parameter, _ = self._parameter(req.symptom.variable)
        req.action = self._first(ACTIONS, text)
        req.scenario = self._first(SCENARIOS, text)
        req.operating_mode = self._first(OPERATING_MODES, text)
        if location and req.parameter == "pressure":
            req.evidence_requirements.append(f"location = {location}")
        # session fallback for pronouns / generic equipment words
        if not req.entities and session is not None and (PRONOUN_RE.search(text) or GENERIC_EQUIPMENT_RE.search(text) or len(text.split()) <= 7):
            for prev in session.last_entities():
                if GENERIC_EQUIPMENT_RE.search(text):
                    word = GENERIC_EQUIPMENT_RE.search(text).group(2).lower()
                    et = (prev.get("entity_type") or "").lower()
                    if word not in ("equipment", "unit", "section") and word not in et and not (word == "furnace" and et == "heater"):
                        continue
                req.entities.append(ResolvedEntity(**{**prev, "method": "session", "confidence": min(0.7, prev.get("confidence", 0.7))}))
                req.resolved_from_session = True
                break
        if not req.parameter and session is not None and req.task_type == TaskType.LIMITS and req.quantities and not req.quantities[0].unit:
            req.parameter = session.last_parameter()
        # LLM assist only when nothing resolved and an equipment word is present
        if not req.entities and self.llm.available() and re.search("|".join(w for w in EQUIPMENT_WORDS if " " not in w), text, re.IGNORECASE):
            guess = self.llm_json("context_resolver", LLMEntityGuess, result, max_tokens=120, purpose="entity_guess", request=text)
            if guess:
                for m in guess.equipment_mentions[:3]:
                    ents, _ = self._resolve_entities(m, result, allow_fuzzy=True)
                    for e in ents:
                        e.method = "llm+" + e.method
                        e.confidence = min(e.confidence, 0.7)
                    req.entities.extend(e for e in ents if e.entity_uid not in req.entity_uids())
                if guess.parameter and not req.parameter:
                    req.parameter = guess.parameter
        req.safety_status, req.safety_signals = self._safety(text, req.task_type, req.action)
        if req.safety_status == SafetyStatus.RESTRICTED and req.task_type != TaskType.SAFETY:
            req.secondary_task_types = [req.task_type, *[t for t in req.secondary_task_types if t != TaskType.SAFETY]]
            req.task_type = TaskType.SAFETY
        req.ambiguities = self._ambiguities(req)
        if req.ambiguities and req.task_type in ENTITY_REQUIRED | {TaskType.SAFETY, TaskType.EXPLANATION} and self._must_clarify(req):
            req.secondary_task_types = [req.task_type, *req.secondary_task_types]
            req.task_type = TaskType.AMBIGUOUS
        req.evidence_requirements = EVIDENCE_REQUIREMENTS.get(req.task_type, []) + req.evidence_requirements
        result.content["structured_request"] = req.model_dump(mode="json", exclude={"original"})
        result.summary = (f"{len(req.entities)} entit{'y' if len(req.entities) == 1 else 'ies'}"
                          + (f", parameter={req.parameter}" if req.parameter else "") + (f", action={req.action}" if req.action else "")
                          + (f", {len(req.quantities)} value(s)" if req.quantities else "") + (f", ambiguous: {', '.join(req.ambiguities)}" if req.ambiguities else ""))
        return req

    # ------------------------------------------------------------------ pieces
    def _resolve_entities(self, text: str, result: AgentResult, allow_fuzzy: bool = True) -> tuple[list[ResolvedEntity], list[str]]:
        found: list[ResolvedEntity] = []
        unresolved: list[str] = []
        seen_uids: set[str] = set()
        covered: list[tuple[int, int]] = []
        positions: dict[str, int] = {}

        def add(mention: str, recs, method: str, base_conf: float, pos: int = 10_000) -> bool:
            if not recs:
                return False
            best = recs[0]
            if best.entity_uid in seen_uids:
                return True
            key = norm_alias(mention)
            exact = key in {norm_alias(a) for a in best.aliases} or key == norm_alias(best.name.split(" (")[0]) or (best.canonical_tag and norm_alias(best.canonical_tag) == key)
            conf = base_conf if exact else base_conf - 0.3
            cands = [r.name for r in recs[1:4] if norm_alias(mention) in {norm_alias(a) for a in r.aliases}]
            found.append(ResolvedEntity(mention=mention, entity_uid=best.entity_uid, canonical_tag=best.canonical_tag, name=best.name, entity_type=best.entity_type,
                                        confidence=round(conf, 2), method=method, candidates=cands))
            positions[best.entity_uid] = pos
            seen_uids.add(best.entity_uid)
            return True

        # 1. explicit tags
        for tag in find_tags(text):
            m = re.search(re.escape(tag).replace(r"\-", r"[\s\-]*"), text, re.IGNORECASE)
            if m:
                covered.append(m.span())
            if not add(tag, self.knowledge.resolve_entity(tag, limit=4), "tag", 0.98, m.start() if m else 10_000):
                unresolved.append(tag)
        # 2. named equipment phrases (longest first)
        phrases = sorted({m.group(1).strip(): m.span() for m in NAMED_EQUIPMENT_RE.finditer(text)}.items(), key=lambda kv: -len(kv[0]))
        for phrase, span in phrases:
            if any(s <= span[0] and span[1] <= e for s, e in covered):
                continue
            words = phrase.lower().split()
            if len(words) == 1 and words[0] in EQUIPMENT_WORDS and words[0] not in SPECIFIC_SINGLE_WORDS:
                continue                      # "the pump" alone is not a mention (handled by session fallback); "the desalter" is
            recs = self.knowledge.resolve_entity(phrase, limit=4)
            if add(phrase, recs, "name", 0.92, span[0]):
                covered.append(span)
            elif allow_fuzzy:
                # try dropping leading adjectives ("running crude charge pump" -> "crude charge pump")
                for i in range(1, len(words) - 1):
                    sub = " ".join(words[i:])
                    recs = self.knowledge.resolve_entity(sub, limit=4)
                    if add(sub, recs, "name", 0.85, span[0]):
                        covered.append(span)
                        break
                else:
                    unresolved.append(phrase)
        # 3. capitalised product / scenario subjects used by claims ("Light Naphtha", "RCO")
        for m in re.finditer(r"\b(RCO|VGO|HVGO|LVGO|ATF|SKO|LPG|naphtha|kerosene|diesel|reduced crude|vacuum residue|slop)\b", text, re.IGNORECASE):
            unresolved.append(m.group(0))
        found.sort(key=lambda e: positions.get(e.entity_uid or "", 10_000))       # order of appearance: "from A to B"
        return found, list(dict.fromkeys(unresolved))

    @staticmethod
    def _quantities(text: str) -> list[QuantityMention]:
        out = []
        for q in find_quantities(text):
            param = PARAMETER_FOR_FAMILY.get(family(q.unit))
            out.append(QuantityMention(raw=f"{q.value:g} {q.unit}", value=q.value, unit=q.unit, parameter=param))
        # bare numbers after "at" ("run this at 500") -> value without unit
        if not out:
            m = re.search(r"\b(?:at|to|of)\s+(\d+(?:\.\d+)?)\s*(?:\?|$|\.|,| and| or)", text, re.IGNORECASE)
            if m:
                out.append(QuantityMention(raw=m.group(1), value=float(m.group(1)), unit=None, parameter=None))
        return out

    @staticmethod
    def _parameter(text: str) -> tuple[str | None, str | None]:
        for rx, param, loc in PARAMETERS:
            if re.search(rx, text, re.IGNORECASE):
                return param, loc
        return None, None

    @staticmethod
    def _symptom(text: str) -> Symptom | None:
        m = SYMPTOM_RE.search(text)
        if m:
            var = re.sub(r"^\s*(the|its|a|an)\s+", "", m.group("var").strip(), flags=re.IGNORECASE)
            d = m.group("dir").lower()
            direction = ("low" if any(k in d for k in ("drop", "fall", "decreas", "reduc", "low", "not", "poor", "zero", "lost", "below")) else
                         "high" if any(k in d for k in ("increas", "ris", "high", "above", "beyond")) else "fluctuating")
            return Symptom(variable=var, direction=direction, raw=m.group(0))
        for rx, var, direction in SIMPLE_SYMPTOMS:
            mm = re.search(rx, text, re.IGNORECASE)
            if mm:
                return Symptom(variable=var, direction=direction, raw=mm.group(0))
        return None

    @staticmethod
    def _first(table, text: str) -> str | None:
        for rx, value in table:
            if re.search(rx, text, re.IGNORECASE):
                return value
        return None

    def _safety(self, text: str, task_type: TaskType, action: str | None) -> tuple[SafetyStatus, list[str]]:
        signals = []
        for pat in self.cfg.governance.restricted_patterns:
            if re.search(pat, text, re.IGNORECASE):
                signals.append(f"restricted: {pat}")
        if signals or action == "bypass":
            return SafetyStatus.RESTRICTED, signals or ["action=bypass"]
        if task_type in SAFETY_REVIEWED or re.search(r"\b(safety|isolat|hazard|ppe|interlock|trip|permit|maintenance|open(ing)? (the |this )?equipment|emergency|fire|toxic|h2s|relief|chemical)\b", text, re.IGNORECASE):
            return SafetyStatus.SENSITIVE, ["task or wording is safety-sensitive"]
        return SafetyStatus.CLEAR, []

    @staticmethod
    def _ambiguities(req: StructuredRequest) -> list[str]:
        amb: list[str] = []
        text = req.original.text
        unit_scope = bool(re.search(r"\b(the |whole |entire )?(unit|plant|cdu|vdu|cdu-ii|atmospheric section|vacuum section|stabilizer section|preheat train)\b", text, re.IGNORECASE))
        if not req.entities and not unit_scope and req.task_type in ENTITY_REQUIRED | {TaskType.SAFETY, TaskType.EXPLANATION}:
            claims_task = req.task_type in (TaskType.LOOKUP, TaskType.COMPARISON, TaskType.CONFLICT, TaskType.PROVENANCE)
            if claims_task and (req.unresolved_mentions or req.scenario):
                pass                                        # product / crude-case subjects can be answered from claims
            elif req.task_type == TaskType.COMPARISON and re.search(r"\b(normal|temporary|startup|shutdown|revision)\b", text, re.IGNORECASE):
                pass                                        # mode / procedure / revision comparisons need no entity
            else:
                amb.append("entity")
        if req.task_type == TaskType.LIMITS and req.quantities:
            if not req.quantities[0].unit:
                amb.append("unit")
            if not req.parameter and not req.quantities[0].parameter:
                amb.append("parameter")
        if req.task_type == TaskType.LIMITS and not req.quantities and not req.parameter and PRONOUN_RE.search(text):
            amb.append("parameter")
        if req.task_type == TaskType.COMPARISON and len(req.entities) < 2 and not req.scenario and not re.search(r"\b(normal|temporary|startup|shutdown|revision|basrah|bombay)\b", text, re.IGNORECASE):
            amb.append("comparison subjects")
        if req.task_type == TaskType.SAFETY and not req.entities and not req.action and re.search(r"\b(is (this|it) safe|which one)\b", text, re.IGNORECASE):
            amb.append("action")
        return amb

    @staticmethod
    def _must_clarify(req: StructuredRequest) -> bool:
        """Only stop for clarification when nothing at all anchors the request."""
        if "entity" in req.ambiguities and not req.unresolved_mentions and not req.quantities and not req.symptom and not req.action:
            return True
        if "entity" in req.ambiguities and (req.symptom or req.action) and not req.unresolved_mentions:
            return True
        return set(req.ambiguities) >= {"unit", "parameter"} or ("entity" in req.ambiguities and "unit" in req.ambiguities)

    # ------------------------------------------------------------------ plan step: clarify
    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        missing = list(request.ambiguities) or ["entity"]
        options: list[str] = []
        text = request.original.text
        if "entity" in missing:
            # suggest candidates only when the request names an equipment class ("the pump");
            # a full-text search on "How do I start it?" returns noise, and one odd suggestion
            # is worse than none.
            word_m = GENERIC_EQUIPMENT_RE.search(text)
            if word_m:
                options += [e.name for e in self.knowledge.search_entities(word_m.group(2), limit=6)]
            elif request.unresolved_mentions:
                options += [e.name for m in request.unresolved_mentions[:2] for e in self.knowledge.search_entities(m, limit=3)]
        questions = {
            "entity": "Which equipment do you mean? Give the tag (e.g. 11-P-01) or the name (e.g. crude charge pump).",
            "parameter": "Which parameter is the value for (flow rate, pressure, temperature, level ...)?",
            "unit": "What unit is the value in (m3/h, kg/cm2 g/a, °C ...)?",
            "comparison subjects": "What should be compared (two pieces of equipment, two operating cases such as Basrah vs Bombay High, or two procedures)?",
            "action": "What action or condition should be checked for safety (e.g. opening the exchanger, bypassing a trip, starting the pump)?",
        }
        q = " ".join(questions[m] for m in missing if m in questions) or "Please add the equipment, parameter and unit."
        intended = request.secondary_task_types[0].value if request.secondary_task_types else "lookup"
        result.blocks.append(ClarificationBlock(id="clarify", title="I need one more detail", question=q, missing=missing, options=options[:6]))
        result.content["intended_task_type"] = intended
        result.summary = f"clarification requested: {', '.join(missing)}"
        result.confidence = self.confidence(0.2, "request could not be anchored to a documented entity", [f"missing {m}" for m in missing])
