"""Task Classifier (Phase 0/1) — hybrid: weighted rules first, LLM only when the rules are unsure.

The rules are high-precision phrase patterns per TaskType (the benchmark categories). They
produce a score per type; the primary type is the best score, secondary types are the others
above half of it. When the best score is low or two types tie, the small LLM is asked for a
structured verdict (one call, ~100 tokens) and its answer is combined with the rules.
Ambiguity (missing entity / parameter / unit) is decided by the Context Resolver, not here.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.plan import PlanStep
from workbench.core.request import ClassifierOutput, StructuredRequest, TaskType, UserRequest
from workbench.core.result import AgentResult

W = 1.0
RULES: dict[TaskType, list[tuple[str, float]]] = {
    TaskType.PROCEDURE: [
        (r"\bhow (do|should|can|would) (i|we|one|the operator)\b.*\b(start|stop|shut|change ?over|switch ?over|isolate|restart|re-?start|commission|line ?up|bring|prepare|take .* out of service)", 2.0),
        (r"\b(procedure|steps?|sequence|checklist) (for|to|of)\b", 1.6), (r"\b(start-?up|shutdown|shut-?down|changeover|change-?over|restart|isolation|light-?off|steam ?out) procedure\b", 2.2),
        (r"\bpre-?start(up)? checks?\b|\bchecks? (must|should|to) be (completed|done|carried out) before\b", 2.2), (r"\bconditions? (must|should) be (satisfied|met) before\b", 2.2),
        (r"\bgive me the .* procedure\b|\bexplain the procedure\b|\bwhat is the .* procedure\b", 2.2), (r"\bhow (do|to) (i |we )?(change ?over|start|restart|shut ?down|isolate)\b", 2.0),
        (r"\b(recommended|correct|proper|preferred|right|best|standard|approved|safe) (way|method|sequence|approach|order) (to|for|of)\b", 2.4),
        (r"\b(way|steps?|sequence) to (start|stop|shut ?down|restart|change ?over|isolate|commission|line ?up|bring)\b", 2.0),
        (r"\brestarting the unit\b|\brestart(ing)? (the )?(unit|section|plant)\b", 1.5),
        (r"\bdetermine the (correct |right |applicable )?.{0,40}procedure\b|\bout of service for maintenance\b|\btake .{0,40} out of service\b", 2.4),
    ],
    TaskType.TROUBLESHOOTING: [
        (r"\b(is|are) (dropping|increasing|rising|falling|decreasing|fluctuating|hunting|cycling|(higher|lower) than normal|too (low|high))\b", 2.4),
        (r"\bwhat (should|could|might|must) (i|we|the operator) (check|investigate|look (at|for)|do first|verify)\b", 2.0),
        (r"\bwhat could (be causing|cause|explain)\b|\bpossible (causes?|reasons?)\b|\bprobable causes?\b|\bwhy (is|are|does|did) .*\b(low|high|dropping|rising|falling|increasing|decreasing|not|fail|trip)", 2.2),
        (r"\bpoor performance\b|\bnot being achieved\b|\bexpected flow is not\b|\bhas decreased\b|\bhas increased\b|\bhas dropped\b", 2.0),
        (r"\brepeated (trips?|failures?|tripping)\b|\bkeeps? tripping\b", 1.8), (r"\btroubleshoot(ing)?\b|\bdiagnos(e|tic)\b|\binvestigate the possible causes\b", 1.8),
        (r"\bcorrective actions?\b|\bdeviation\b.*\bcauses\b", 1.2),
    ],
    TaskType.LIMITS: [
        (r"\boperating (limits?|range|envelope|window)\b|\ballowable\b|\bmaximum allowable\b", 3.2), (r"\b(normal|maximum|minimum|design|rated) (operating )?(value|range|limit|limits)\b", 1.8),
        (r"\bis (this|it|that|\d[\d.,]* ?\w*) (acceptable|ok|okay|allowed|permissible|fine|within)\b", 2.4), (r"\bwithin the (documented |normal |design )?(operating )?(envelope|range|limits?)\b", 2.2),
        (r"\bwhat happens if .* (exceeds?|goes? (above|high|low|beyond)|drops? below)\b", 2.0), (r"\bexceeds? (its|the) (operating |design )?(limit|value)\b", 1.6),
        (r"\bcan (i|we) (run|operate) (this|it|the \w+( \w+)?) at\b", 2.4), (r"\boperating at \d", 2.0), (r"\bdifference between the normal .* and (maximum|design)\b", 1.2),
    ],
    TaskType.SAFETY: [
        (r"\bsafety precautions?\b|\bprecautions?\b", 2.2), (r"\bisolation requirements?\b|\bisolat(e|ion|ed) .* (before|for|prior to) (maintenance|work)\b", 2.4),
        (r"\bhazards?\b|\bhazardous\b", 1.8), (r"\bppe\b|\bprotective equipment\b", 2.4), (r"\bsafeguards?\b|\binterlocks?\b|\btrips? (associated|on|of)\b|\bprotect(s|ion)? .* from unsafe\b", 1.8),
        (r"\b(bypass|defeat|override|inhibit|disable|jumper|force)\b.{0,80}\b(protection|interlock|trip|alarm|safeguard|psv|relief valve|it)\b|\bbypass (this|it) temporarily\b", 3.2),
        (r"\bis (this|it) safe\b|\bsafe to (open|work|operate|start)\b|\bbefore working on\b|\bbefore maintenance on\b|\bopening this equipment\b", 2.2),
        (r"\bwork permit\b|\bpermit to work\b|\bauthori[sz]ation\b", 1.4), (r"\bwhen handling (this )?chemical\b|\bchemical\b.*\bhazard", 1.4),
    ],
    TaskType.COMPARISON: [
        (r"\bcompare\b|\bcomparison\b", 4.0), (r"\bdifference between\b|\bwhat is different\b|\bdifferences?\b", 1.8), (r"\bversus\b|\bvs\.?\b", 2.0),
        (r"\bwhat changed between\b|\bchanged between (the )?(current|previous|rev)", 1.2), (r"\bwhat changes for\b|\bwhat is different for\b", 1.8),
        (r"\b(normal|temporary) (and|versus|vs) (temporary|normal) (operating )?(configurations?|operation)\b", 2.0),
    ],
    TaskType.CONFLICT: [
        (r"\b(two|2|several|multiple|different|conflicting) (different )?(values|figures|numbers|normal flow values)\b", 2.6), (r"\bwhich (one )?(should i|do i|to) trust\b", 2.6),
        (r"\bwhy do .* (give|state|show) different\b", 2.6), (r"\bconflict(ing|s)?\b", 2.0), (r"\bcurrently authoritative\b|\bwhich value is (currently )?(correct|authoritative|valid)\b", 2.2),
    ],
    TaskType.PROVENANCE: [
        (r"\bshow (me )?(all|the) (documented )?(values|evidence|sources)\b", 2.6), (r"\bevidence (for|supporting|behind)\b|\bsupporting (this|the) answer\b", 2.4),
        (r"\bwhat revision\b|\bwhich revision\b|\brevision introduced\b", 2.4), (r"\b(their|the) sources?\b|\bwhere does (this|that) (value|number|limit) come from\b", 2.0),
        (r"\bis this value from\b|\bfrom the (equipment )?specification or\b", 2.4), (r"\bprovenance\b|\btraceab", 2.0), (r"\bwhich document is the authoritative source\b", 2.0),
    ],
    TaskType.PLANNING: [
        (r"\b(prepare|create|make|build|generate|develop|draw up|put together) (me )?(a |an |the )?(complete |detailed |step-by-step )?(work ?plan|plan|checklist|investigation plan|restart plan|engineering investigation)\b", 4.2),
        (r"\bwhat should i do\b.*\b(prepare|maintenance|out of service)\b|\bprepare the (unit|section|plant) for\b", 2.4), (r"\bwhat information do i need before\b", 2.2),
        (r"\bstep-by-step (investigation|plan)\b|\binvestigation plan\b", 2.4), (r"\bdetermine the correct .* (and|,) .* (requirements|documents)\b", 1.4),
        (r"\bidentify prerequisites and dependencies\b|\bbuild a complete\b", 1.8), (r"\bwork plan for\b|\bplan for (inspecting|inspection|maintenance)\b", 2.4),
    ],
    TaskType.REPORT: [
        (r"\b(prepare|generate|create|write|produce|make) (me )?(a |an |the )?(\w+ ){0,3}(report|summary of all|engineering report|troubleshooting report|comparison report)\b", 4.2),
        (r"\breport (on|about|explaining|for)\b", 2.0), (r"\bsummary of (all )?(major )?equipment\b|\bsummar(y|ize|ise) (the|all)\b", 1.6),
    ],
    TaskType.CROSS_DOCUMENT: [
        (r"\bwhich documents?\b|\bwhat documents?\b", 4.5), (r"\bfind all (the )?sections\b|\bwhich sections\b|\bsections that (mention|describe|cover)\b|\ball sections\b", 4.5),
        (r"\breferenced procedure\b|\brefers to another\b|\bfind that referenced\b|\breferenced (document|section)\b", 2.4), (r"\bstanding instructions?\b", 2.4),
        (r"\bauthoritative source\b|\bwhich document is\b", 1.8), (r"\bacross (the )?documents\b|\bcross-?document\b|\bin (other|all) documents\b", 2.0),
    ],
    TaskType.MULTI_HOP: [
        (r"\btrace\b", 4.0), (r"\b(upstream|downstream) (path|of|equipment|flow)\b", 2.2), (r"\bflow (path|from .* to|route)\b|\bfrom the .* to the\b", 1.6),
        (r"\bpass(es)? through\b|\bbefore entering\b|\bafter leaving\b", 2.0), (r"\bwhich equipment (receives|does|is|gets)\b", 2.2),
        (r"\bwhich instruments? (monitor|measure|are associated|are installed|control)\b|\binstruments? (associated with|on|monitoring)\b", 2.4),
        (r"\bwhat (process )?variables do they measure\b", 1.6), (r"\bstandby (for|pump|equipment)\b|\bact as (a )?standby\b|\bwhich pumps? can\b", 2.0),
        (r"\bwhere is .* (located|installed|in the .* process)\b|\blocation of\b|\bwhat feeds\b|\bfed (from|by)\b|\bconnected to\b", 1.8), (r"\bwhat (equipment|products?) (does|is|are) .* (produce|pass|go)", 1.2),
    ],
    TaskType.EXPLANATION: [
        (r"^\s*why (is|are|does|do|was|were|must|should) (?!.*\b(low|high|dropping|rising|falling|increasing|decreasing|not|fail|trip|poor|different|lower|higher)\b)", 2.4),
        (r"\bwhy is .* (heated|cooled|installed|routed|used|required|needed|provided|placed|located|before|after)\b", 2.4), (r"\bpurpose of\b|\bwhat is the (role|function|purpose|reason)\b|\bwhat does .* do\b", 2.0),
        (r"\bexplain (why|how .* works?|the (working|principle|operation) of)\b|\bprinciple of\b", 2.0), (r"\bwhy (is|are) (steam|reflux|stripping steam|chemical|caustic|ammonia|demulsifier|corrosion inhibitor) (used|injected|added)\b", 2.4),
    ],
    TaskType.LOOKUP: [
        (r"\bwhat (is|are) the (normal|design|rated|maximum|minimum|operating|mechanical|allowable|trip|alarm)? ?(flow ?rate|flow|pressure|temperature|capacity|rating|speed|level|duty|head|npsh|power)\b", 2.4),
        (r"\bwhat (is|are) (the )?(design|normal|rated) (pressure|temperature|flow|capacity)\b", 2.4), (r"\bwhat products? (are|is) (produced|made)\b|\bwhat does .* produce\b", 2.0),
        (r"\bwhich pumps? (are|is) available\b|\bwhat pumps? (are|is) (available|used)\b|\bavailable for\b", 2.6), (r"^\s*what is [0-9]{1,3}-[a-z]{1,4}-[0-9]{1,5}[a-z/]*\s*\??\s*$", 3.0),
        (r"\bwhat (is|are) (its|the) (normal|design)\b", 2.2), (r"\b(tag|name|type|service|duty|capacity|specifications?|spec|datasheet|data sheet) (of|for)\b", 1.4),
        (r"^\s*what (is|are) (the |an? )?[\w /-]{3,40}\??\s*$", 1.0),
    ],
}
COMPILED = {t: [(re.compile(p, re.IGNORECASE), w) for p, w in rules] for t, rules in RULES.items()}
SAFETY_HINT_RE = re.compile(r"\b(safety|isolat|hazard|ppe|interlock|trip|bypass|permit|maintenance|open(ing)? (the |this )?equipment|emergency|fire|toxic|h2s|relief)\b", re.IGNORECASE)


class LLMClassification(BaseModel):
    task_type: TaskType
    secondary: list[TaskType] = Field(default_factory=list)
    intent: str = Field(description="One short sentence: what the engineer wants")
    confidence: float = Field(ge=0, le=1)


def rule_scores(text: str) -> dict[TaskType, tuple[float, list[str]]]:
    scores: dict[TaskType, tuple[float, list[str]]] = {}
    for t, rules in COMPILED.items():
        total = 0.0
        hits: list[str] = []
        for rx, w in rules:
            m = rx.search(text)
            if m:
                total += w
                hits.append(m.group(0)[:40])
        if total > 0:
            scores[t] = (total, hits)
    return scores


def classify_by_rules(text: str) -> ClassifierOutput:
    scores = rule_scores(text)
    if not scores:
        return ClassifierOutput(task_type=TaskType.LOOKUP, confidence=0.2, method="rules", signals=["no pattern matched; defaulting to lookup"])
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    per_type = {t.value: round(s, 2) for t, (s, _) in ranked}
    best_t, (best_s, best_hits) = ranked[0]
    second_s = ranked[1][1][0] if len(ranked) > 1 else 0.0
    # confidence: strong when the best score is high and clearly ahead
    margin = (best_s - second_s) / max(best_s, 1e-6)
    confidence = min(0.98, 0.35 + 0.15 * min(best_s, 3.0) + 0.3 * margin)
    secondary = [t for t, (s, _) in ranked[1:] if s >= 0.5 * best_s and s >= 1.5][:3]
    # compound requests: planning/report words + a diagnostic/procedure body -> keep the body as primary, planning/report as secondary
    if best_t in (TaskType.PLANNING, TaskType.REPORT) and secondary and secondary[0] in (TaskType.TROUBLESHOOTING, TaskType.PROCEDURE) and best_s - second_s < 1.0:
        pass  # planner/report templates already gather diagnostic/procedure content
    return ClassifierOutput(task_type=best_t, secondary=secondary, intent="", confidence=round(confidence, 2), method="rules", signals=best_hits, rule_scores=per_type)


class TaskClassifierAgent(BaseAgent):
    name = "task_classifier"
    phase = "0/1 Understanding"
    description = "Classifies the request into a task type (rules first, LLM when unsure)."

    def classify(self, request: UserRequest, result: AgentResult, session_task: str | None = None) -> ClassifierOutput:
        text = request.text.strip()
        out = classify_by_rules(text)
        # follow-up fragments ("and the shutdown?") inherit the session task when the rules are weak
        if out.confidence < 0.5 and session_task and len(text.split()) <= 6:
            out.signals.append(f"short follow-up; inheriting session task {session_task}")
            try:
                out.task_type = TaskType(session_task)
                out.confidence = 0.55
            except ValueError:
                pass
        use_llm = self.cfg.llm.use_llm_for_classification and out.confidence < self.cfg.llm.classifier_confidence_threshold
        if use_llm:
            llm = self.llm_json("task_classifier", LLMClassification, result, max_tokens=160, purpose="classify",
                                request=text, rule_guess=f"{out.task_type.value} (confidence {out.confidence})",
                                task_types=", ".join(f"{t.value}" for t in TaskType))
            if llm is not None:
                if llm.confidence >= 0.6 and llm.task_type != TaskType.AMBIGUOUS:
                    if llm.task_type != out.task_type and out.task_type not in llm.secondary and out.confidence >= 0.4:
                        llm.secondary = [out.task_type, *llm.secondary]
                    out = ClassifierOutput(task_type=llm.task_type, secondary=[t for t in llm.secondary if t != llm.task_type][:3], intent=llm.intent,
                                           confidence=round(max(out.confidence, llm.confidence * 0.9), 2), method="rules+llm", rule_scores=out.rule_scores,
                                           signals=out.signals + ["llm agreed" if llm.task_type == out.task_type else "llm overrode rules"])
                else:
                    out.intent = out.intent or llm.intent
                    out.method = "rules+llm"
        if not out.intent:
            out.intent = _default_intent(out.task_type, text)
        result.content["classification"] = out.model_dump(mode="json")
        result.summary = f"{out.task_type.value} ({out.confidence:.2f}, {out.method})"
        return out

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        # Not used as a plan step: the orchestrator calls classify() directly in Phase 0/1.
        out = self.classify(request.original, result)
        result.summary = f"classified as {out.task_type.value}"


def _default_intent(t: TaskType, text: str) -> str:
    return {
        TaskType.LOOKUP: "Retrieve a documented property", TaskType.MULTI_HOP: "Traverse the process topology", TaskType.PROCEDURE: "Retrieve an ordered procedure with prerequisites",
        TaskType.TROUBLESHOOTING: "Diagnose a deviation from documented causes and checks", TaskType.LIMITS: "Compare against the documented operating envelope",
        TaskType.EXPLANATION: "Explain the documented reasoning", TaskType.SAFETY: "Retrieve documented safety requirements", TaskType.COMPARISON: "Compare documented values side by side",
        TaskType.CONFLICT: "Resolve differing documented values", TaskType.PROVENANCE: "Show sources and revisions", TaskType.PLANNING: "Build a work / investigation plan",
        TaskType.REPORT: "Generate an engineering report", TaskType.CROSS_DOCUMENT: "Locate documents and sections", TaskType.AMBIGUOUS: "Clarify the request",
    }.get(t, text[:80])
