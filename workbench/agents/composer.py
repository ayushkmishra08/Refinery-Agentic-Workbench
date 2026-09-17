"""Answer Composer (Phase 5) — writes the answer the engineer actually reads.

Everything upstream produces *material*: claims with page numbers, relationship edges, ordered
procedure steps, quoted passages. Handing that material straight to the engineer is what the
workbench used to do, and it reads like a search result rather than an answer — the same
passage quoted three times, twelve inferred instrument edges, an evidence list longer than
the question.

This agent turns the material into prose. It builds a compact brief — the question, the
conversation it belongs to, the equipment, and only the facts that bear on it — and asks the
model to write a formal reply from that brief and nothing else. The knowledge is context; the
answer is composed.

Three things keep it honest:

- the call is *structured* (a JSON schema with an ``answer`` field). A grammar-constrained
  decode cannot open with "Hmm, the user wants me to...", which is exactly what these small
  models do when asked for free text, and it is why the narrative was unusable before;
- every number and every equipment tag in the composed answer must appear in the brief. One
  that does not earns a re-ask naming it, and then a fall back to the deterministic wording;
- with no model at hand — effort ``low``, Ollama down, the test suite — the deterministic
  composer writes the same shape of answer from the same material. The prose is plainer; it
  is never a block dump.
"""
from __future__ import annotations

import difflib
import re

from pydantic import BaseModel, Field

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest, TaskType
from workbench.core.result import AgentResult

NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])")
TAG_RE = re.compile(r"\b\d{1,3}-[A-Z]{1,4}-\d{1,5}(?:[A-Z](?:/[A-Z])*)?\b", re.IGNORECASE)
# A tag and the name the answer attaches to it, written either way round:
# "12-F-01 (vacuum heater)" and "the vacuum heater (12-F-01)".
_TAG = r"\d{1,3}-[A-Z]{1,4}-\d{1,5}(?:[A-Z](?:/[A-Z])*)?"
GLOSSED_TAG_RE = re.compile(rf"\b({_TAG})\s*\(([^)]{{3,60}})\)", re.IGNORECASE)
GLOSSED_NAME_RE = re.compile(rf"\b((?:[A-Za-z][A-Za-z/-]*\s+){{0,3}}[A-Za-z][A-Za-z/-]*)\s*\(({_TAG})\)", re.IGNORECASE)
# words in a parenthetical that say nothing about which equipment it is
GLOSS_STOPWORDS = {"normal", "design", "rated", "minimum", "maximum", "page", "operating", "documented",
                   "above", "below", "approximately", "about", "train", "stream", "service", "spare", "standby"}
# Qualifiers that cannot both be true of one item. A refinery has an atmospheric section and a
# vacuum section, and calling the vacuum heater "the atmospheric furnace" sends someone to the
# wrong side of the unit — a word-overlap check alone will not catch it, because both are
# furnaces.
CONTRADICTORY_QUALIFIERS: list[tuple[str, str]] = [
    ("atmospheric", "vacuum"), ("suction", "discharge"), ("inlet", "outlet"),
    ("upstream", "downstream"), ("overhead", "bottom"), ("cold", "hot"),
]
CHARS_PER_TOKEN = 3.6
META_OPENERS = re.compile(
    r"^\s*(hmm\b|okay\b|ok\b|so,? the (user|engineer)|the (user|engineer|colleague) (is asking|is enquiring|wants|asked|has asked|enquired)|"
    r"let me\b|i (will|should|need to|must)\b|we are given\b|the (task|question) is\b|as an ai\b|"
    r"here is (the|my) (answer|summary)|this (answer|response) (will|is)\b|based on the (brief|provided|given)\b|"
    r"the brief\b|according to the brief\b)",
    re.IGNORECASE,
)

# Which parts of the brief matter most, per task type. The brief is trimmed from the bottom
# of this list up, so a procedure question keeps its steps and loses its instrument edges.
SECTION_PRIORITY: dict[TaskType, list[str]] = {
    TaskType.LOOKUP: ["values", "identity", "passages", "connections", "procedures"],
    TaskType.LIMITS: ["limits", "values", "identity", "passages", "connections"],
    TaskType.PROCEDURE: ["steps", "identity", "safety", "passages", "values"],
    TaskType.TROUBLESHOOTING: ["diagnosis", "values", "passages", "identity", "connections", "steps"],
    TaskType.MULTI_HOP: ["connections", "identity", "passages", "values"],
    TaskType.EXPLANATION: ["passages", "identity", "connections", "values"],
    TaskType.SAFETY: ["safety", "steps", "passages", "identity", "values"],
    TaskType.COMPARISON: ["comparison", "values", "identity", "connections", "passages"],
    TaskType.CONFLICT: ["values", "identity", "passages"],
    TaskType.PROVENANCE: ["values", "identity", "passages"],
    TaskType.PLANNING: ["steps", "safety", "values", "identity", "passages"],
    TaskType.REPORT: ["values", "identity", "connections", "steps", "passages", "safety"],
    TaskType.CROSS_DOCUMENT: ["documents", "identity", "passages"],
    TaskType.INVENTORY: ["inventory", "identity"],
    TaskType.AMBIGUOUS: ["identity", "passages"],
}
DEFAULT_PRIORITY = ["identity", "values", "connections", "steps", "passages", "safety", "diagnosis", "comparison", "limits", "documents", "inventory"]


class ComposedAnswer(BaseModel):
    """What the model returns. Small schemas hold up far better on a 2-4B model than large ones."""
    answer: str = Field(description="The reply, in formal flowing prose. No bullet lists, no headings, no page citations.")
    assumptions: list[str] = Field(default_factory=list, description="Anything read into the question rather than stated by it")
    not_documented: list[str] = Field(default_factory=list, description="Parts of the question the documents do not answer")


class AnswerComposerAgent(BaseAgent):
    name = "answer_composer"
    phase = "5 Answer composition"
    description = "Writes the released answer in prose from the retrieved material; the knowledge is context, not the output."

    # ------------------------------------------------------------------ entry point
    def compose(self, request: StructuredRequest, context: ContextPackage, results: dict[str, AgentResult],
                result: AgentResult) -> None:
        material = self._material(request, context, results)
        brief, sections_used = self._brief(request, material)
        result.content["brief_chars"] = len(brief)
        result.content["brief_sections"] = sections_used
        # the pages that actually reached the brief — the source trail under the answer names
        # these rather than every page the run touched on its way here
        result.content["brief_pages"] = sorted({int(p) for p in re.findall(r"\(?p\.?\s*(\d{1,4})[-)\s]", brief)})

        composed = self._ask_model(request, brief, result) if self._model_allowed() else None
        if composed is None:
            answer = self._deterministic(request, material)
            result.content["source"] = "deterministic"
            assumptions: list[str] = []
            not_documented = material["missing"][:4]
        else:
            answer = composed.answer.strip()
            assumptions = [a for a in composed.assumptions if a.strip()][:3]
            not_documented = [n for n in composed.not_documented if n.strip()][:3] or material["missing"][:3]
            result.content["source"] = "model"

        answer = self._ensure_correction_stated(request, answer)
        result.content["answer"] = answer
        result.content["assumptions"] = assumptions
        result.content["not_documented"] = not_documented
        result.content["composed"] = True
        result.blocks.append(self.text_block(answer, block_id="answer"))
        # the composed answer is an inference over everything the run cited, so it is checked
        # against the union of that evidence rather than a single key
        keys = [e.key() for r in results.values() for e in r.evidence][:24]
        self.statement(result, answer, keys, kind="inference")
        result.summary = f"{len(answer.split())}-word answer composed from {len(sections_used)} brief section(s) ({result.content['source']})"
        result.confidence = self.confidence(
            0.8 if result.content["source"] == "model" else 0.65,
            "answer written from the retrieved material; every number and tag in it was checked against that material",
            ["the wording is the model's; the facts and page references are the documents'"] if result.content["source"] == "model" else [],
        )

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        self.compose(request, context, dict(self.s.prior_results), result)

    # ------------------------------------------------------------------ model call
    def _model_allowed(self) -> bool:
        return bool(self.cfg.llm.use_llm_for_answer and self.llm.available())

    def _ask_model(self, request: StructuredRequest, brief: str, result: AgentResult) -> ComposedAnswer | None:
        words = self.cfg.effort.answer_words
        allowed = self._allowed_tokens(brief)
        attempt = 0
        note = ""
        while attempt <= self.cfg.llm.answer_retries:
            out = self.llm_json("answer_composer", ComposedAnswer, result,
                                max_tokens=self.cfg.llm.num_predict_answer, purpose="compose_answer",
                                brief=brief,
                                length=f"Write roughly {words} words — enough to answer properly, not a page.",
                                correction_from_last_attempt=note or "none")
            attempt += 1
            if out is None:
                return None
            # A meta opening is a wasted sentence, not a wasted answer: the substance almost
            # always follows it. Cut the sentence and keep the reply.
            out.answer, cut = self._strip_meta_opening(out.answer or "")
            if cut:
                result.trace.append(f"Dropped {cut} opening sentence(s) that described the question instead of answering it.")
            problems = self._grounding_problems(out.answer, allowed)
            if self._repeats_previous_answer(out.answer):
                problems.append("the answer repeats the previous turn's answer instead of answering this question")
            if META_OPENERS.match(out.answer):
                # stripping could not save it: what follows the preamble is not an answer either
                problems.append("the answer describes the question instead of answering it")
            if len(out.answer.split()) < 12:
                problems.append("the answer is too short to be an answer")
            if not problems:
                result.trace.append(f"The answer was composed by the model in {attempt} attempt(s) and is fully grounded.")
                return out
            result.trace.append(f"Composed answer rejected (attempt {attempt}): {'; '.join(problems[:3])}.")
            note = ("The previous attempt was rejected: " + "; ".join(problems[:3])
                    + ". Use only figures and tags that appear in the brief, and answer the question directly.")
        result.content["grounding_failed"] = True
        return None

    def _repeats_previous_answer(self, answer: str) -> bool:
        """True when the model handed back the last turn's answer instead of writing one.

        A follow-up puts the previous answer in front of a small model, and a small model will
        sometimes copy it. That is never right: the engineer asked a new question.
        """
        session = getattr(self.s, "session", None)
        turns = getattr(session, "turns", []) if session is not None else []
        if not turns or not turns[-1].answer_preview:
            return False
        previous = re.sub(r"[^a-z0-9 ]+", " ", turns[-1].answer_preview.lower()).split()
        current = re.sub(r"[^a-z0-9 ]+", " ", (answer or "").lower()).split()
        if len(current) < 10 or len(previous) < 10:
            return False
        return difflib.SequenceMatcher(None, " ".join(previous[:120]), " ".join(current[:120])).ratio() > 0.75

    @staticmethod
    def _strip_meta_opening(answer: str) -> tuple[str, int]:
        """Remove leading sentences that talk about the question rather than answer it.

        Returns the trimmed answer and how many sentences went. Nothing is dropped when the
        answer would be left too thin to stand — a whole reply of preamble is a real failure
        and should reach the re-ask.
        """
        sentences = re.split(r"(?<=[.!?])\s+", (answer or "").strip())
        cut = 0
        while cut < len(sentences) and META_OPENERS.match(sentences[cut]):
            cut += 1
        remaining = " ".join(sentences[cut:]).strip()
        if cut and len(remaining.split()) >= 25:
            return remaining, cut
        return (answer or "").strip(), 0

    @staticmethod
    def _allowed_tokens(brief: str) -> tuple[set[str], set[str]]:
        nums = {AnswerComposerAgent._norm_num(n) for n in NUM_RE.findall(brief)}
        tags = {t.upper() for t in TAG_RE.findall(brief)}
        return nums, tags

    @staticmethod
    def _norm_num(s: str) -> str:
        try:
            return f"{float(s.replace(',', '')):g}"
        except ValueError:
            return s

    def _grounding_problems(self, answer: str, allowed: tuple[set[str], set[str]]) -> list[str]:
        """Numbers, tags and equipment names in the answer that the documents do not support."""
        nums, tags = allowed
        text = answer or ""
        bad_nums = sorted({n for n in (self._norm_num(x) for x in NUM_RE.findall(text)) if n not in nums})
        bad_tags = sorted({t.upper() for t in TAG_RE.findall(text) if t.upper() not in tags})
        problems = []
        if bad_nums:
            problems.append(f"figure(s) not in the evidence: {', '.join(bad_nums[:4])}")
        if bad_tags:
            problems.append(f"tag(s) not in the evidence: {', '.join(bad_tags[:4])}")
        problems += self._misnamed_tags(text)
        return problems

    def _misnamed_tags(self, answer: str) -> list[str]:
        """Tags the answer glosses with a name the documents do not give them.

        "12-F-01 (atmospheric furnace inlet)" passes a figures-and-tags check and is still
        wrong: 12-F-01 is the vacuum heater. Attaching the wrong name to a right tag is the
        kind of error that sends someone to the wrong side of the unit, so it is checked.
        """
        problems = []
        pairs = [(t, g) for t, g in GLOSSED_TAG_RE.findall(answer or "")]
        pairs += [(t, g) for g, t in GLOSSED_NAME_RE.findall(answer or "")]
        for tag, gloss in pairs:
            words = {w for w in re.findall(r"[a-z]{4,}", gloss.lower()) if w not in GLOSS_STOPWORDS}
            if not words:
                continue                                  # "(A/B)", "(normal)", "(p. 60)"
            records = self.knowledge.resolve_entity(tag, limit=1)
            if not records:
                continue                                  # an unknown tag is already reported above
            rec = records[0]
            name = (rec.name or "").split(" (")[0]
            documented = " ".join([rec.name or "", rec.entity_type or "", *(rec.aliases or [])]).lower()
            # one shared word is weak: "boiler feed water tank" and "crude feed pump" share
            # "feed" and are not the same object. Most of the gloss has to be documented.
            if sum(w in documented for w in words) * 2 < len(words):
                problems.append(f"{tag} is described as '{gloss}'; the documents call it '{name}'")
                continue
            contradiction = next((f"{a}/{b}" for a, b in CONTRADICTORY_QUALIFIERS
                                  if (a in words and b in documented and a not in documented)
                                  or (b in words and a in documented and b not in documented)), None)
            if contradiction:
                problems.append(f"{tag} is called '{gloss}' but the documents call it '{name}' ({contradiction} are not the same)")
        return problems[:2]

    # ------------------------------------------------------------------ material
    def _material(self, request: StructuredRequest, context: ContextPackage, results: dict[str, AgentResult]) -> dict:
        """Everything the run produced, flattened into the few shapes the brief can express."""
        prior = [r for r in results.values() if r.agent not in ("verification", "governance", "answer_composer")]
        facts: list[str] = []
        for r in prior:
            for st in r.statements:
                if st.kind in ("fact", "calculation") and st.text.strip():
                    facts.append(self._one_line(st.text))
        claims, claim_sentences = [], []
        ordered_claims = self._ordered_claims(request, context)
        on_topic = self._on_topic_count(request, ordered_claims)
        for c in ordered_claims[:14]:
            role = f" ({c.parameter_role})" if c.parameter_role else ""
            where = f" at the {c.location}" if c.location else ""
            case = f" in {c.scenario}" if c.scenario else ""
            claims.append(f"{c.subject}{where}: {c.predicate.replace('_', ' ')} = {c.value} {c.unit or ''}{role}{case} (p.{c.page})".replace("  ", " "))
            claim_sentences.append(self._claim_sentence(c))
        relations = [f"{self._named(r.source_name)} {r.rel_type.replace('_', ' ').lower()} {self._named(r.target_name)} (p.{r.page})"
                     for r in context.relations[:10] if r.source == "rule" or r.confidence >= 0.8]
        steps, procedure_title = [], ""
        for p in context.procedures[:1]:
            procedure_title = f"{p.title} ({p.procedure_type}, pp.{p.page_start}-{p.page_end})"
            steps = [f"{s.sequence}. {self._one_line(s.text)}" for s in p.steps[:12]]
        passages = [self._one_line(ch.text, 340) + f" (p.{ch.page_start})" for ch in context.chunks[:5]]
        safety = []
        for r in prior:
            safety += [f"{f.severity.upper()}: {self._one_line(f.message, 180)}" for f in r.safety_flags]
        diagnosis = []
        diag = results.get("diagnose") or next((r for r in prior if r.agent == "diagnostic"), None)
        if diag is not None:
            for key in ("causes", "checks", "actions"):
                for item in (diag.content.get(key) or [])[:4]:
                    label = item.get("text") if isinstance(item, dict) else str(item)
                    if label:
                        diagnosis.append(f"{key[:-1]}: {self._one_line(label, 160)}")
        verdict = ""
        gauge = next((b for r in prior for b in r.blocks if b.type == "limit_gauge" and b.value is not None), None)
        if gauge is not None:
            markers = ", ".join(f"{m.label} {m.value:g} {gauge.unit}" for m in gauge.markers)
            verdict = (f"{gauge.value:g} {gauge.unit} of {gauge.parameter.replace('_', ' ')} on {gauge.entity} is "
                       f"{gauge.verdict.replace('_', ' ')}. Documented envelope: {markers}."
                       + (f" {gauge.message}" if gauge.message else ""))
        missing = list(dict.fromkeys([m for r in prior for m in r.missing]))
        inventory = [f"{k}: {v}" for k, v in list(context.entity_counts.items())[:12]]
        documents = [f"{d.title or d.document_id}, revision {d.revision or 'n/a'}, {d.total_pages or '?'} pages"
                     for d in self.knowledge.documents()]
        return {"facts": list(dict.fromkeys(facts))[:12], "claims": claims, "claim_sentences": claim_sentences,
                "on_topic_claims": on_topic, "relations": relations, "steps": steps,
                "procedure_title": procedure_title, "passages": passages, "safety": list(dict.fromkeys(safety))[:5],
                "diagnosis": diagnosis, "missing": missing, "inventory": inventory, "documents": documents,
                "verdict": verdict}

    def _named(self, label: str) -> str:
        """A bare tag with the name the documents give it, so the model has no name to invent.

        Rule-derived relations carry endpoints as they were written in the sentence — often the
        tag alone ("11-P-01 A/B discharges to 12-F-01"). Left that way, a model asked to write
        readable prose will supply a plausible name for 12-F-01, and a plausible name is not a
        documented one.
        """
        text = (label or "").strip()
        if not text or "(" in text or not TAG_RE.search(text):
            return text
        cache = self._name_cache
        if text not in cache:
            records = self.knowledge.resolve_entity(TAG_RE.search(text).group(0), limit=1)
            name = (records[0].name or "").split(" (")[0] if records else ""
            cache[text] = f"{text} ({name})" if name and name.lower() not in text.lower() else text
        return cache[text]

    @property
    def _name_cache(self) -> dict[str, str]:
        if not hasattr(self, "_names"):
            self._names: dict[str, str] = {}
        return self._names

    @staticmethod
    def _ordered_claims(request: StructuredRequest, context: ContextPackage) -> list:
        """Claims that answer the question first.

        Retrieval returns everything documented about a piece of equipment. Asked for a flow
        rate, an engineer does not want the suction pressure recited first, so a claim whose
        predicate matches the parameter in the question is moved to the front — and when the
        question named a parameter at all, the unrelated ones are kept only as filler.
        """
        if not request.parameter:
            return list(context.claims)
        wanted = request.parameter.lower().replace("_", " ")
        on_topic = [c for c in context.claims if wanted in c.predicate.lower().replace("_", " ")
                    or c.predicate.lower().replace("_", " ") in wanted]
        rest = [c for c in context.claims if c not in on_topic]
        return on_topic + rest

    @staticmethod
    def _on_topic_count(request: StructuredRequest, ordered: list) -> int:
        if not request.parameter:
            return len(ordered)
        wanted = request.parameter.lower().replace("_", " ")
        return sum(1 for c in ordered if wanted in c.predicate.lower().replace("_", " ")
                   or c.predicate.lower().replace("_", " ") in wanted)

    @staticmethod
    def _claim_sentence(c) -> str:
        """A documented value as a sentence rather than a record, for the rule-written answer."""
        parameter = c.predicate.replace("_", " ")
        role = f"{c.parameter_role} " if c.parameter_role and c.parameter_role not in parameter else ""
        where = f" at the {c.location}" if c.location else ""
        case = f" under {c.scenario}" if c.scenario else ""
        mode = f" in {c.operating_mode.replace('_', ' ')}" if c.operating_mode else ""
        unit = f" {c.unit}" if c.unit else ""
        page = f" (p. {c.page})" if c.page else ""
        return f"The manual gives the {role}{parameter} of {c.subject}{where}{case}{mode} as {c.value}{unit}{page}."

    @staticmethod
    def _one_line(text: str, width: int = 240) -> str:
        """One clean line of prose from a retrieved passage.

        Chunks keep their source markup — "### 2.1 NORMAL OPERATING CONDITIONS", bullet dashes,
        table pipes — because the knowledge layer stores the document faithfully. Dropped into a
        sentence unchanged, that produced answers reading "The primary purpose is to lower the
        viscosity. ## DETERMINING DESALTER PROCESS VARIABLES ...". The markup goes; a heading that
        is left stranded becomes a clause rather than a shout.
        """
        lines = [ln.strip() for ln in (text or "").replace("|", " ").splitlines()]
        body = [ln for ln in lines if ln and not AnswerComposerAgent._is_heading(ln)]
        # a passage that is *only* a heading still has to say something, so keep it then
        kept = body or [ln for ln in lines if ln]
        t = " ".join(kept)
        t = re.sub(r"^\s*[-*•]\s+", "", t)                               # a leading bullet marker
        t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)                           # bold
        t = re.sub(r"\s+", " ", t).strip()
        return t if len(t) <= width else t[:width].rsplit(" ", 1)[0] + " ..."

    @staticmethod
    def _is_heading(line: str) -> bool:
        """A section heading rather than a sentence.

        Headings are how the document is organised, not what it says, and splicing one into prose
        produces "...settling rate of water droplets. DETERMINING DESALTER PROCESS VARIABLES The
        primary variables include...". They are dropped and the body kept.
        """
        if line.startswith("#"):
            return True
        stripped = line.strip(" .:")
        if not stripped or len(stripped.split()) > 12:
            return False
        if re.match(r"^\d+(\.\d+)*[.)]?\s", stripped) and stripped.rstrip()[-1] not in ".?!":
            return True                                    # "2.1 Normal operating conditions"
        letters = [c for c in stripped if c.isalpha()]
        upper_ratio = sum(c.isupper() for c in letters) / len(letters) if letters else 0
        return upper_ratio > 0.7 and line.rstrip()[-1:] not in ".?!"

    # ------------------------------------------------------------------ brief
    def _brief(self, request: StructuredRequest, material: dict) -> tuple[str, list[str]]:
        """The prompt body: the question, its conversation, and the material, trimmed to budget."""
        sections: dict[str, str] = {}
        sections["question"] = self._question_section(request)
        sections["identity"] = self._identity_section(request)
        if material["claims"]:
            sections["values"] = "## Documented values\n" + "\n".join(f"- {c}" for c in material["claims"])
        if material["verdict"]:
            sections["limits"] = ("## The limit check already performed\n- " + material["verdict"]
                                  + "\n(State this verdict in the first sentence of the answer.)")
        elif material["facts"]:
            sections["limits"] = "## Established facts\n" + "\n".join(f"- {f}" for f in material["facts"])
        if material["relations"]:
            sections["connections"] = "## Documented connections\n" + "\n".join(f"- {r}" for r in material["relations"])
        if material["steps"]:
            sections["steps"] = f"## Procedure — {material['procedure_title']}\n" + "\n".join(material["steps"])
        if material["passages"]:
            sections["passages"] = "## Passages from the manual\n" + "\n".join(f"- {p}" for p in material["passages"])
        if material["safety"]:
            sections["safety"] = "## Documented safety points\n" + "\n".join(f"- {s}" for s in material["safety"])
        if material["diagnosis"]:
            sections["diagnosis"] = "## Documented causes, checks and actions\n" + "\n".join(f"- {d}" for d in material["diagnosis"])
        if material["inventory"]:
            sections["inventory"] = "## Equipment counts in the documents\n" + "\n".join(f"- {i}" for i in material["inventory"])
        if material["documents"]:
            sections["documents"] = "## Documents loaded\n" + "\n".join(f"- {d}" for d in material["documents"])
        if request.task_type == TaskType.COMPARISON and request.baseline_entities:
            sections["comparison"] = ("## What is being compared\n"
                                      + f"- proposed: {', '.join(self._label(e) for e in request.entities if e not in request.baseline_entities) or 'the item named in the question'}\n"
                                      + f"- against (from the previous turn): {', '.join(self._label(e) for e in request.baseline_entities)}")
        if material["missing"]:
            sections["gaps"] = "## Not in the documents\n" + "\n".join(f"- {m}" for m in material["missing"][:5])

        order = ["question", "identity"] + [s for s in SECTION_PRIORITY.get(request.task_type, DEFAULT_PRIORITY) if s in sections] \
            + [s for s in DEFAULT_PRIORITY if s in sections] + ["gaps"]
        seen, ordered = set(), []
        for key in order:
            if key in sections and key not in seen:
                seen.add(key)
                ordered.append(key)

        budget = int(self.cfg.llm.answer_brief_tokens * CHARS_PER_TOKEN)
        out, used, total = [], [], 0
        for key in ordered:
            body = sections[key]
            if total + len(body) > budget and key not in ("question", "identity", "gaps"):
                continue
            out.append(body)
            used.append(key)
            total += len(body) + 2
        return "\n\n".join(out), used

    def _question_section(self, request: StructuredRequest) -> str:
        lines = ["## The engineer's question", request.original.spoken_text.strip()]
        if request.original.asked_text and request.original.text != request.original.asked_text:
            lines.append(f"(read in context as: {request.original.text.strip()})")
        session = getattr(self.s, "session", None)
        turns = getattr(session, "turns", []) if session is not None else []
        if turns:
            history = ["## Conversation so far (background only — never repeat an earlier answer)"]
            for t in turns[-2:]:
                history.append(f"- the engineer asked: {t.request.strip()}")
                if t.answer_preview:
                    # a short gist, not the answer: a long excerpt here is something a small
                    # model will copy back out as its reply
                    history.append(f"  which was answered about: {self._gist(t.answer_preview)}")
            lines = [*history, "", *lines]
        return "\n".join(lines)

    def _gist(self, answer: str) -> str:
        """The first sentence of an earlier answer, short enough not to be worth copying."""
        first = re.split(r"(?<=[.!?])\s+", (answer or "").strip())[0]
        return self._one_line(first, 140)

    def _identity_section(self, request: StructuredRequest) -> str:
        lines = ["## Equipment and scope"]
        for e in request.entities:
            marker = " — carried over from the previous question" if e in request.baseline_entities else ""
            lines.append(f"- {self._label(e)}, a {(e.entity_type or 'item').lower()}{marker}")
        for c in request.corrections:
            # the substitution notice is prepended verbatim after composition, so the model is
            # told to answer about the documented item and to leave the notice alone
            lines.append(f"- NOTE: the engineer wrote '{c.mention}', which is not a tag in these documents. "
                         + (f"Answer about {c.adopted_label} throughout, and do not mention the tag substitution — "
                            "it is stated to the engineer separately." if c.adopted
                            else f"Close matches: {'; '.join(c.alternatives[:3])}."))
        if request.parameter:
            lines.append(f"- the parameter asked about: {request.parameter.replace('_', ' ')}")
        if request.scenario:
            lines.append(f"- operating case: {request.scenario}")
        if request.operating_mode:
            lines.append(f"- operating mode: {request.operating_mode.replace('_', ' ')}")
        if request.symptom:
            lines.append(f"- reported symptom: {request.symptom.variable} {request.symptom.direction}")
        if len(lines) == 1:
            lines.append("- the question names no single piece of equipment")
        return "\n".join(lines)

    @staticmethod
    def _label(e) -> str:
        name = (e.name or "").split(" (")[0]
        return f"{e.canonical_tag} ({name})" if e.canonical_tag and name else (e.canonical_tag or name or e.mention)

    # ------------------------------------------------------------------ deterministic composer
    def _deterministic(self, request: StructuredRequest, material: dict) -> str:
        """The same answer without a model: plainer prose, same shape, same honesty.

        Used at effort ``low``, when Ollama is down, and whenever the model's attempt could not
        be grounded. It is prose deliberately — the point of this agent is that the engineer
        never receives a pile of blocks as the answer.
        """
        subjects = [self._label(e) for e in request.entities]
        is_scope = not subjects and bool(request.scope)
        if is_scope:
            # a scope word ("document", "refinery") is a place, not a thing: the sentence has to
            # open "Across the loaded documents", never "On document, the documents record"
            subjects = ["the loaded documents" if request.scope == "document" else f"the {request.scope}"]
        subject = " and ".join(subjects[:2]) if subjects else "the documents"
        lead_in = f"Across {subject}," if is_scope else f"On {subject}, the documents record the following."
        opening = []
        for c in request.corrections:
            opening.append(c.sentence())

        body: list[str] = []
        gap = self._coverage_gap(request, material)
        if gap:
            body.append(gap)
        if material["verdict"]:
            body.append(material["verdict"])          # the answer to a limit question is the verdict
        if material["claim_sentences"] and not material["verdict"]:
            # when the question named a parameter, recite only the values for that parameter
            keep = min(4, material["on_topic_claims"] or 4)
            body.append(" ".join(material["claim_sentences"][:keep]))
        elif material["facts"]:
            body.append(f"{lead_in} " + " ".join(self._sentence(f) for f in material["facts"][:5]))
        if material["relations"]:
            body.append("In the documented line-up, " + "; ".join(material["relations"][:4]) + ".")
        if material["steps"]:
            body.append(f"The manual gives a documented procedure — {material['procedure_title']} — of "
                        f"{len(material['steps'])} step(s); the steps are set out below exactly as written.")
        if material["diagnosis"]:
            body.append("For this symptom the manual documents: " + "; ".join(material["diagnosis"][:5]) + ".")
        if material["safety"]:
            body.append("The documents attach these precautions: " + "; ".join(material["safety"][:3]) + ".")
        if not body and material["facts"]:
            body.append(f"{lead_in} " + " ".join(self._sentence(f) for f in material["facts"][:5]))
        if not body and material["passages"]:
            body.append(f"The documents do not state this about {subject} directly. The nearest passage reads: "
                        + material["passages"][0])
        if not body:
            body.append(f"The loaded documents do not answer this question about {subject}. "
                        "Nothing has been inferred in place of a documented answer.")
        if material["missing"]:
            body.append("Not documented: " + "; ".join(material["missing"][:3]) + ".")
        return " ".join(opening + body).strip()

    def _coverage_gap(self, request: StructuredRequest, material: dict) -> str:
        """A sentence saying the question's own subject is absent, when it is.

        Retrieval returns whatever it has about the equipment, so asked for a bearing vibration
        limit it will happily return four flow rates. Reciting them as though they were the
        answer is the failure this catches: say the thing asked for is not documented, then give
        what is.
        """
        wanted = {w for w in (request.parameter, getattr(request.symptom, "variable", None)) if w}
        if not wanted:
            return ""
        haystack = " ".join(material["claims"] + material["facts"] + material["passages"]
                            + material["relations"] + material["diagnosis"]).lower()
        missing = sorted({w.replace("_", " ") for w in wanted if w.replace("_", " ").lower() not in haystack})
        if not missing:
            return ""
        subject = " and ".join(self._label(e) for e in request.entities[:2]) or "the equipment named"
        return (f"The documents do not record the {' or '.join(missing)} of {subject}. "
                f"What they do record about it follows.")

    @staticmethod
    def _sentence(text: str) -> str:
        t = text.strip()
        if not t:
            return ""
        t = t[0].upper() + t[1:]
        return t if t.endswith((".", "!", "?", ":")) else t + "."

    # ------------------------------------------------------------------ guarantees
    @staticmethod
    def _ensure_correction_stated(request: StructuredRequest, answer: str) -> str:
        """A near-miss tag is never silently swallowed, and it is said first.

        Which equipment the answer is about is the most important thing on the page when the
        engineer named one that does not exist. It is stated deterministically rather than left
        to the model, so the wording cannot drift and cannot end up in the last sentence.
        """
        prefix = [c.sentence() for c in request.corrections if c.adopted]
        if not prefix:
            return answer
        opening = " ".join(re.split(r"(?<=[.!?])\s+", answer.strip())[:2]).lower()
        already = all(c.mention.lower() in opening and (c.adopted_label or "").split(" (")[0].lower() in opening
                      for c in request.corrections if c.adopted)
        return answer if already else (" ".join(prefix) + " " + answer).strip()
