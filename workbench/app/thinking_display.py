"""Terminal rendering of the agentic reasoning, phase by phase, as the run happens.

Subscribes to the orchestrator's ProgressEvents and prints, for each phase, which agent is
working, what it is reasoning about (the ``thinking`` text the narration module produced),
which model it used, what it decided and how long it took. The final answer is printed
afterwards, clearly separated from the reasoning.

Rendering only: the text comes from ``workbench.orchestration.narration`` and the structured
copy is kept by ``ThinkingTraceCollector``. Colour is used when the terminal supports it and
dropped silently when it does not, so the layout is identical either way.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import time

from workbench.core.events import ProgressEvent
from workbench.services.thinking_store import ThinkingTraceCollector

# ------------------------------------------------------------------ colour support


def _enable_ansi() -> bool:
    """True when ANSI escapes are safe to emit; enables virtual terminal mode on Windows."""
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32                      # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)                    # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            return True
        except Exception:
            return False
    return True


_COLOR = _enable_ansi()
_CODES = {"dim": "2", "bold": "1", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36"}


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _c(style: str, text: str) -> str:
    return f"\033[{_CODES[style]}m{text}\033[0m" if _COLOR and text else text


def _visible_len(text: str) -> int:
    """Printed width of a string that may carry ANSI escapes."""
    return len(ANSI_RE.sub("", text))


# ------------------------------------------------------------------ layout constants

AGENT_ICONS = {
    "task_classifier": "[cls]", "context_resolver": "[ctx]", "context_builder": "[ret]", "planner": "[pln]",
    "lookup": "[val]", "graph": "[grf]", "procedure": "[prc]", "diagnostic": "[dia]", "calculation": "[cal]",
    "comparison": "[cmp]", "revision_conflict": "[rev]", "explanation": "[exp]", "cross_document": "[doc]",
    "safety": "[saf]", "report": "[rep]", "verification": "[ver]", "governance": "[gov]",
    "answer_composer": "[ans]",
}
PHASE_TITLES = {
    "0/1 Understanding": "Phase 0/1 · Understanding",
    "2 Specialist retrieval": "Phase 2 · Specialist Retrieval",
    "3 Planning": "Phase 3 · Planning",
    "4 Execution": "Phase 4 · Execution / Engineering Analysis",
    "5 Governance": "Phase 5 · Governance & Response",
}
STATUS_STYLE = {"answered": "green", "needs_review": "yellow", "clarification": "yellow", "restricted": "red", "failed": "red"}
BODY_INDENT = "     "


def _width() -> int:
    try:
        return min(max(shutil.get_terminal_size((88, 25)).columns, 60), 110)
    except Exception:
        return 88


def _ms(value: int) -> str:
    return f"{value / 1000:.1f} s" if value >= 1000 else f"{value} ms"


class ThinkingDisplay:
    """Renders the thinking of one run to the terminal and records it for the JSON trace."""

    def __init__(self, query: str, *, llm_model: str = "", backend: str = "", profile: str = "", effort: str = "") -> None:
        self.query = query
        self.llm_model = llm_model
        self.backend = backend
        self.profile = profile
        self.effort = effort
        self.collector = ThinkingTraceCollector(query, llm_model=llm_model, backend=backend, profile=profile, effort=effort)
        self.width = _width()
        self._phase_start = 0.0
        self._step_no = 0
        self._step_total = 0
        self._header_printed = False

    # ------------------------------------------------------------------ event sink
    def on_event(self, ev: ProgressEvent) -> None:
        self.collector.on_event(ev)
        if not self._header_printed:
            self.print_header()
        handler = getattr(self, f"_on_{ev.event}", None)
        if handler:
            handler(ev)

    # ------------------------------------------------------------------ primitives
    def _wrap(self, text: str, prefix: str, style: str | None = None, repeat_prefix: bool = True) -> None:
        """Print text wrapped to the terminal width.

        ``repeat_prefix`` keeps the prefix on continuation lines (a gutter such as "│ ");
        turn it off for labelled lines, where continuations are indented under the label.
        """
        room = max(30, self.width - _visible_len(prefix))
        cont = prefix if repeat_prefix else " " * _visible_len(prefix)
        for para in text.split("\n"):
            para = para.rstrip()
            if not para:
                continue
            lead = " " * (len(para) - len(para.lstrip()))
            for i, line in enumerate(textwrap.wrap(para.strip(), room) or [""]):
                body = (lead if i == 0 else lead + "  ") + line
                print((prefix if i == 0 else cont) + (_c(style, body) if style else body))

    def _rule(self, title: str = "", char: str = "─", style: str = "dim") -> None:
        if title:
            bar = f"{char * 2} {title} "
            print(_c(style, bar + char * max(0, self.width - len(bar))))
        else:
            print(_c(style, char * self.width))

    # ------------------------------------------------------------------ header / footer
    def print_header(self) -> None:
        self._header_printed = True
        runtime = " · ".join(x for x in [f"effort {self.effort}" if self.effort else "", f"backend {self.backend}" if self.backend else "",
                                         f"llm {self.llm_model}" if self.llm_model else "", f"profile {self.profile}" if self.profile else ""] if x)
        print()
        self._rule(char="━", style="cyan")
        print(_c("bold", "  Refinery Engineering AI Workbench"))
        self._wrap(f'"{self.query}"', "  ", "cyan")
        if runtime:
            print(_c("dim", f"  {runtime}"))
        self._rule(char="━", style="cyan")

    def print_answer(self, resp, answer_markdown: str) -> None:
        """Print the released answer, then the one-line run summary.

        The footer carries what an operator must act on — the status and whether a human has
        to sign off. Confidence, grounding scores and the audit id live in the thinking above
        and in the saved trace, so the answer itself reads like an answer.
        """
        print()
        self._rule(char="━", style="green")
        print(_c("bold", "  ANSWER"))
        self._rule(char="━", style="green")
        print()
        print(answer_markdown.strip())
        print()
        self._rule()
        style = STATUS_STYLE.get(resp.status, "dim")
        parts = [_c(style, _c("bold", resp.status.upper()))]
        if resp.requires_human_review:
            parts.append(_c("yellow", "human review required"))
        if resp.safety_flags:
            parts.append(f"{len(resp.safety_flags)} safety flag(s)")
        parts += [f"{len(resp.evidence)} citation(s)", _ms(resp.timing_ms)]
        print("  " + " · ".join(parts))
        if resp.review_reason:
            self._wrap(f"Review reason: {resp.review_reason}", "  ", "yellow", repeat_prefix=False)

    def print_trace_saved(self, path) -> None:
        print(_c("dim", f"  Thinking trace: {path}"))
        print()

    # ------------------------------------------------------------------ phases
    def _on_phase_started(self, ev: ProgressEvent) -> None:
        self._phase_start = time.time()
        print()
        self._rule(PHASE_TITLES.get(ev.phase or "", ev.phase or ""), char="─", style="cyan")

    def _on_phase_finished(self, ev: ProgressEvent) -> None:
        elapsed = int((time.time() - self._phase_start) * 1000) if self._phase_start else 0
        print(_c("dim", f"  phase complete · {_ms(elapsed)}"))

    # ------------------------------------------------------------------ agents
    def _on_agent_started(self, ev: ProgressEvent) -> None:
        agent = ev.agent or "agent"
        icon = AGENT_ICONS.get(agent, "[ * ]")
        label = agent if not ev.step_id or ev.step_id == agent else f"{agent} · {ev.step_id}"
        if ev.phase == "4 Execution":
            self._step_no += 1
            label = f"{label}   ({self._step_no}/{self._step_total})" if self._step_total else label
        print()
        print(f"  {_c('dim', icon)} {_c('bold', label)}")
        self._wrap(ev.thinking or ev.message, BODY_INDENT, "dim", repeat_prefix=False)

    def _on_agent_finished(self, ev: ProgressEvent) -> None:
        data = ev.data or {}
        if ev.thinking:
            self._wrap(ev.thinking, BODY_INDENT + _c("dim", "│ "))
        ok = data.get("ok", True)
        arrow = _c("green", "└▸") if ok else _c("red", "└✗")
        decision = ev.decision or ev.message or ("done" if ok else "failed")
        self._wrap(decision, BODY_INDENT[:-2] + arrow + " ", "bold" if ok else "red", repeat_prefix=False)
        meta = [_ms(data.get("duration_ms", 0))]
        if data.get("evidence"):
            meta.append(f"{data['evidence']} evidence")
        if data.get("llm_calls"):
            secs = data.get("llm_seconds") or 0
            meta.append(f"{data['llm_calls']} LLM call(s)" + (f" in {secs} s" if secs else "") + (f" · {ev.model}" if ev.model else ""))
        print(BODY_INDENT + _c("dim", " · ".join(meta)))

    # ------------------------------------------------------------------ plan / LLM / problems
    def _on_plan_created(self, ev: ProgressEvent) -> None:
        steps = (ev.data or {}).get("steps", [])
        self._step_total = len(steps)
        self._step_no = 0
        if not steps:
            return
        print()
        print(BODY_INDENT + _c("bold", "Execution DAG"))
        width_agent = max((len(s.get("agent", "")) for s in steps), default=8)
        for i, s in enumerate(steps, 1):
            deps = f"  ← {', '.join(s['depends_on'])}" if s.get("depends_on") else ""
            marks = "".join(["!" if s.get("safety_sensitive") else "", "?" if s.get("optional") else ""])
            head = f"{i}. {s.get('id', ''):<10} {s.get('agent', ''):<{width_agent}} {marks:<2} "
            self._wrap(s.get("goal", "") + deps, BODY_INDENT + "  " + _c("cyan", head), repeat_prefix=False)
        legend = [key for key, flag in (("! safety-sensitive", "safety_sensitive"), ("? optional (a failure does not trigger a replan)", "optional")) if any(s.get(flag) for s in steps)]
        if legend:
            print(BODY_INDENT + _c("dim", "  " + "   ".join(legend)))

    def _on_llm_call(self, ev: ProgressEvent) -> None:
        model = ev.model or self.llm_model or "llm"
        print(BODY_INDENT + _c("magenta", f"⟨calling {model} — {ev.message}⟩"))

    def _on_replan(self, ev: ProgressEvent) -> None:
        print()
        self._wrap(ev.message, "  " + _c("yellow", "↻ replan: "), "yellow")
        self._step_no = 0

    def _on_warning(self, ev: ProgressEvent) -> None:
        self._wrap(ev.message, BODY_INDENT + _c("yellow", "! "), "yellow")

    def _on_error(self, ev: ProgressEvent) -> None:
        print()
        self._wrap(ev.message, "  " + _c("red", "✗ error: "), "red")

    def _on_access_denied(self, ev: ProgressEvent) -> None:
        """The run stopped at the door; say who was asking and what would open it."""
        data = ev.data or {}
        print()
        self._wrap(ev.message, "  " + _c("red", "🔒 access denied: "), "red", repeat_prefix=False)
        roles = ", ".join(data.get("required_roles", [])) or "a cleared role"
        print(BODY_INDENT + _c("dim", f"signed in as {data.get('role', 'guest')} · needs {roles} · sign in with 'workbench login'"))
