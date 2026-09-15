"""Status agent for the "btw" side channel.

While a request is running (or after it finished) the user can ask "btw, what's going on?",
"which agent is running?", "what have you found so far?", "why is it slow?". This agent answers
from the RunState only — plan, step statuses, events, partial summaries, resource events. It
uses no LLM, so it never competes with the main run for the GPU and answers in milliseconds.
"""
from __future__ import annotations

import re
import time

from workbench.core.blocks import Block, CalloutBlock, PlanBlock, PlanTask, TableBlock, TextBlock
from workbench.orchestration.runs import RunState


def _elapsed(rs: RunState) -> float:
    end = rs.finished or time.time()
    return end - rs.started


def status_reply(rs: RunState, question: str = "") -> list[Block]:
    q = (question or "").lower()
    blocks: list[Block] = []
    elapsed = _elapsed(rs)
    done = [s for s in rs.step_status if s["status"] in ("done", "skipped", "failed")]
    total = len(rs.step_status)
    current = rs.current_step
    head = f"**Run {rs.run_id}** — *{rs.request_text[:90]}*  \nPhase: **{rs.phase}**"
    if rs.finished:
        head += f" · finished in {elapsed:.1f}s · status **{rs.final_status or rs.error or '?'}**"
    else:
        head += f" · running for {elapsed:.1f}s"
        if current:
            head += f" · now: **{current['agent']}** — {current['goal']}"
    if total:
        head += f"  \nSteps: {len(done)}/{total} complete"
    blocks.append(TextBlock(id="status", markdown=head))

    if re.search(r"found|so far|result|learn|know|discover", q):
        rows = [[s["step_id"], s["agent"], s["status"], (s.get("summary") or "")[:110]] for s in rs.step_status if s.get("summary")]
        blocks.append(TableBlock(id="found", title="What has been found so far", columns=["Step", "Agent", "Status", "Summary"], rows=rows) if rows
                      else CalloutBlock(id="found", level="info", markdown="Nothing has been produced yet; the run is still in the understanding / retrieval phases."))
    elif re.search(r"slow|long|time|how much longer|eta|wait", q):
        llm = rs.llm_calls
        remaining = total - len(done)
        est = remaining * (elapsed / max(1, len(done))) if done else None
        md = f"Elapsed {elapsed:.1f}s. LLM calls so far: {llm}" + (f" ({rs.llm_seconds:.1f}s in the model)" if rs.llm_seconds else "") + f". Remaining steps: {remaining}."
        if est is not None and not rs.finished:
            md += f" Rough estimate: ~{est:.0f}s more at the current pace."
        if llm and not rs.finished:
            md += " Most of the time goes to the local language model; deterministic steps take milliseconds."
        blocks.append(TextBlock(id="timing", markdown=md))
    elif re.search(r"plan|steps|dag|order", q):
        pass  # plan block below
    elif re.search(r"safety|risk|danger|flag", q):
        blocks.append(TextBlock(id="safety", markdown=f"Safety status of the request: **{rs.safety_status or 'unknown'}**. " + (f"Flags so far: {rs.safety_flags}." if rs.safety_flags else "No safety flags raised yet.")))
    elif re.search(r"entit|equipment|about what|which (pump|column|heater)", q):
        blocks.append(TextBlock(id="entities", markdown="Resolved entities: " + (", ".join(rs.entities) if rs.entities else "none yet") + (f". Task type: {rs.task_type}." if rs.task_type else "")))
    elif re.search(r"resource|vram|gpu|memory|model|loaded", q):
        blocks.append(TextBlock(id="resources", markdown=f"Model: {rs.resources.get('llm', '?')} (loaded: {rs.resources.get('llm_loaded')}), embedder loaded: {rs.resources.get('embedder_loaded')}, reranker loaded: {rs.resources.get('reranker_loaded')}. Idle unload after {rs.resources.get('idle_unload_seconds')}s."))
    if rs.step_status:
        tasks = [PlanTask(id=s["step_id"], title=s["goal"], agent=s["agent"], depends_on=s.get("depends_on", []), status=s["status"], summary=(s.get("summary") or "")[:120] or None) for s in rs.step_status]
        blocks.append(PlanBlock(id="plan", title="Plan progress", goal=rs.goal or rs.request_text[:80], tasks=tasks))
    if rs.recent_events:
        rows = [[f"{e['t']:.1f}s", e["event"], e.get("agent") or "", (e.get("message") or "")[:100]] for e in rs.recent_events[-8:]]
        blocks.append(TableBlock(id="events", title="Recent events", columns=["t", "Event", "Agent", "Message"], rows=rows))
    return blocks
