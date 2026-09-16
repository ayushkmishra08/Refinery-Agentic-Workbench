"""Follow-up resolution: turning "what if we use 11-E-01 instead?" into a question that stands alone.

The pipeline downstream — classifier, resolver, retrieval, planning — reads one sentence and
has no memory. A follow-up is therefore rewritten here, before any of it runs, into the
question the engineer would have typed if they had not been mid-conversation:

    turn 1: "what does pump 12-P-01 do?"
    turn 2: "what if we use 11-E-01 instead?"
      ->    "What if 11-E-01 were used instead of 12-P-01 (Quench Pumps)?
             Compare 11-E-01 with 12-P-01."

Four shapes are recognised, because they need different rewrites:

``substitution``  "instead", "rather than", "in place of", "what if we used X"
                  -> a comparison against whatever the last turn was about
``elaboration``   "why?", "and the shutdown?", "what about at start-up?"
                  -> the previous subject with the new angle attached
``continuation``  a pronoun or a bare equipment word with no subject of its own
                  -> the previous subject spelled out
``new``           anything that names its own subject; left untouched

Rules do the work. The model is asked only when the sentence is clearly dependent — it has
no subject the rules can find — and the rules could not supply one, which on a 4 GB card is
worth about one short structured call.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

SUBSTITUTION_RE = re.compile(
    r"\b(instead(?: of)?|rather than|in place of|in lieu of|replace[ds]? (?:it|that|this) with|"
    r"swap(?:ped)? (?:it |that )?(?:for|with)|switch(?:ed)? to|what if (?:we |i |you )?(?:use[ds]?|used|run|ran|take|took|go with)|"
    r"(?:use|using|run|running) .{0,30}? instead)\b",
    re.IGNORECASE,
)
ELABORATION_RE = re.compile(
    r"^\s*(and |but |so |then |also |what about|how about|and what about|ok(?:ay)?[, ]|"
    r"why\b|why not\b|how come\b|what else\b|anything else\b|is that\b|are those\b|does it\b|do they\b)",
    re.IGNORECASE,
)
PRONOUN_ONLY_RE = re.compile(
    r"^\s*(?:(?:and|but|so|then|ok(?:ay)?)[, ]+)?(?:what|how|why|when|where|which|is|are|does|do|can|should|would)?\s*"
    r"\b(it|that|this|these|those|the same|they|them)\b",
    re.IGNORECASE,
)
# A subject of its own: a tag, or a named piece of equipment, or a plural class word
SELF_CONTAINED_RE = re.compile(
    r"\b\d{1,3}\s*-?\s*[A-Za-z]{1,4}\s*-\s*\d{1,5}[A-Za-z/]*\b|"
    r"\b(crude|vacuum|atmospheric|desalter|stabili[sz]er|naphtha|kerosene|diesel|preheat|column|heater|furnace|"
    r"exchanger|pump|drum|vessel|tank|ejector|stripper|condenser|reboiler|cooler)\b",
    re.IGNORECASE,
)
ANGLE_WORDS = [
    (r"\bstart-?up\b|\bstarting\b", "during start-up"), (r"\bshut-?down\b|\bshutting down\b", "during shutdown"),
    (r"\bemergency\b", "in an emergency"), (r"\bsafety\b|\bprecaution", "from a safety standpoint"),
    (r"\bmaintenance\b|\bisolat", "for maintenance and isolation"), (r"\bupset\b|\btrip\b", "during an upset"),
]


class FollowUp(BaseModel):
    """What the resolver decided about one turn."""
    kind: str = "new"                       # new | substitution | elaboration | continuation
    rewritten: str = ""                     # the standalone question; empty when nothing was rewritten
    baseline_entities: list[dict] = Field(default_factory=list, description="ResolvedEntity dumps carried forward from the previous turn")
    baseline_labels: list[str] = Field(default_factory=list)
    carried_parameter: str | None = None
    note: str = ""                          # one line for the thinking trace and the audit
    method: str = "rules"                   # rules | rules+llm | none

    @property
    def is_followup(self) -> bool:
        return self.kind != "new"


class LLMRewrite(BaseModel):
    standalone_question: str = Field(description="The follow-up rewritten so it can be read on its own, naming the equipment explicitly")
    refers_to_previous: bool = Field(default=True, description="False when the question introduces a subject of its own")


def _labels(entities: list[dict]) -> list[str]:
    out = []
    for e in entities:
        name = (e.get("name") or "").split(" (")[0]
        tag = e.get("canonical_tag") or ""
        out.append(f"{tag} ({name})" if tag and name and name.lower() != tag.lower() else (tag or name or e.get("mention", "")))
    return [x for x in out if x]


def _angle(text: str) -> str:
    for rx, phrase in ANGLE_WORDS:
        if re.search(rx, text, re.IGNORECASE):
            return phrase
    return ""


def classify_followup(text: str, session) -> str:
    """Which of the four shapes this turn is, from the wording and the session alone."""
    if session is None or not getattr(session, "turns", None):
        return "new"
    if SUBSTITUTION_RE.search(text):
        return "substitution"
    has_subject = bool(SELF_CONTAINED_RE.search(text))
    if PRONOUN_ONLY_RE.match(text) and not has_subject:
        return "continuation"
    if ELABORATION_RE.match(text) and not has_subject:
        return "elaboration"
    if ELABORATION_RE.match(text) and has_subject and len(text.split()) <= 9:
        return "elaboration"                # "and the shutdown of the vacuum column?"
    if not has_subject and len(text.split()) <= 8:
        return "continuation"
    return "new"


def resolve_followup(text: str, session, *, llm=None, use_llm: bool = False, result=None) -> FollowUp:
    """Rewrite a dependent turn into a standalone question, using the session's last subject."""
    kind = classify_followup(text, session)
    if kind == "new":
        return FollowUp(kind="new", method="none")

    prev = session.turns[-1]
    prior_entities = [e for e in (prev.entities or []) if e.get("entity_uid")] or session.last_entities(3)
    labels = _labels(prior_entities)
    fu = FollowUp(kind=kind, baseline_entities=prior_entities, baseline_labels=labels,
                  carried_parameter=prev.parameter or session.last_parameter())
    if not labels:
        fu.note = "the previous turn resolved no equipment, so there is nothing to carry forward"
        fu.kind = "new"
        return fu
    subject = labels[0]
    angle = _angle(text)

    if kind == "substitution":
        # the new subject is whatever this sentence names; the old one becomes the baseline
        fu.rewritten = (f"{text.strip().rstrip('?.')} — instead of {subject}. "
                        f"Compare the proposed item with {subject} and say what changes if it is used in its place.")
        fu.note = f"substitution against the previous subject ({subject})"
    elif kind == "elaboration":
        stem = text.strip().rstrip("?.")
        fu.rewritten = f"{stem} — for {subject}" + (f", {angle}" if angle else "") + "?"
        fu.note = f"elaboration on {subject}"
    else:  # continuation
        stem = re.sub(r"\b(it|that|this|these|those|they|them|the same)\b", subject, text.strip(), flags=re.IGNORECASE)
        fu.rewritten = stem if stem != text.strip() else f"{text.strip().rstrip('?.')} — about {subject}?"
        fu.note = f"pronoun resolved to {subject}"

    if use_llm and llm is not None and getattr(llm, "available", lambda: False)():
        rewritten = _llm_rewrite(text, session, llm, result)
        if rewritten:
            # the model's version is kept only when it actually names the carried subject; a
            # rewrite that drops the referent is worse than the rule-based one
            if any(lbl.split(" (")[0].lower() in rewritten.lower() for lbl in labels):
                fu.rewritten = rewritten if kind != "substitution" else f"{rewritten} Compare it with {subject}."
                fu.method = "rules+llm"
                fu.note += "; the model spelled the question out"
    return fu


def _llm_rewrite(text: str, session, llm, result) -> str | None:
    from workbench.llm.client import LLMOutputError, LLMUnavailable

    history = "\n".join(
        f"- engineer asked: {t.request}" + (f"\n  answer covered: {t.answer_preview[:160]}" if t.answer_preview else "")
        for t in session.turns[-2:]
    )
    system = ("You rewrite a follow-up question so it can be read on its own. Name the equipment explicitly, "
              "using the tags from the conversation. Change nothing else: do not answer it, do not add facts, "
              "do not add equipment that was never mentioned.")
    user = f"## conversation\n{history}\n\n## follow-up\n{text}"
    try:
        out = llm.structured(system, user, LLMRewrite, max_tokens=140, purpose="followup_rewrite")
    except (LLMUnavailable, LLMOutputError):
        return None
    if result is not None:
        result.llm_calls += 1
    if not out or not out.refers_to_previous:
        return None
    q = (out.standalone_question or "").strip()
    return q if 3 <= len(q.split()) <= 60 else None
