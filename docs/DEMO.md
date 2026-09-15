# Demo script — Refinery Engineering AI Workbench

Fifteen questions, one per capability, in the order they are best presented. Each shows the full agent trace in
the terminal and writes a JSON thinking trace to `data/workbench/thinking/`.

Run them exactly as written:

```powershell
python -m workbench ask "<question>"
```

Useful flags while presenting:

| flag | effect |
|---|---|
| *(none)* | phase-by-phase thinking, then the answer, then the saved trace path |
| `--no-thinking` | the answer only |
| `--json` | the raw `FinalResponse` (what the web UI receives) |
| `python -m workbench trace` | list the saved traces, newest first |
| `python -m workbench trace --show` | replay the newest trace as text |
| `python -m workbench repl` | interactive; type `btw <question>` while a run is in progress |

`RWB_LLM=off` runs the whole set deterministically if the GPU is busy; everything still answers, only the
LLM-written narrative and the LLM-refined plan drop out.

---

## The fifteen questions

### 1. Lookup — a documented value

```powershell
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
```

The cheapest possible path, and the one to open with: rules classify it as `lookup` with no model call, the
alias index resolves "crude charge pump" to **Crude Feed Pump (11-PM-01)** and shows the three other tags that
share that name, and the `claims` route answers from the claim index with **no text search at all**. One agent,
two steps, ~25 ms.

**Talking point:** every value carries its page; the workbench never paraphrases a number.

### 2. Multi-hop — trace a flow path

```powershell
python -m workbench ask "Trace the crude flow from the crude charge pump to the atmospheric column."
```

Two entities resolve in order of appearance, the `graph` route traverses the engineering graph two hops, and the
explanation agent adds the manual text describing the connections.

**Talking point:** this one deliberately ends at low confidence. No continuous documented route exists between
the two tags, so the answer says so and shows both neighbourhoods instead of inventing an intermediate path.
That refusal is the feature.

### 3. Procedure — ordered steps with prerequisites

```powershell
python -m workbench ask "What is the recommended way to start the CDU?"
```

No specific tag is named, so the resolver marks the request unit-scoped rather than asking for clarification.
The `proc` route searches the procedure index, and five steps run: find → prerequisites, steps → referenced
documents → safety review.

**Talking point:** the safety agent is a *separate* step that depends on the content steps, so precautions are
attached to steps that already exist rather than generated alongside them.

### 4. Troubleshooting — the longest deterministic chain

```powershell
python -m workbench ask "The crude charge pump discharge pressure is dropping. What should I check?"
```

Nine steps across five agents: identify → normal range → find the upset section → causes → checks → actions →
topology → related procedures → safety. Watch the dependency arrows in the Execution DAG: `checks` and `actions`
both wait for `causes`, and `safety` waits for both.

**Talking point:** the best single demonstration of flow of control between agents.

### 5. Limits — a value against the envelope

```powershell
python -m workbench ask "The crude charge pump is operating at 520 m3/h. Is this acceptable?"
```

The quantity and its unit family are parsed into `flow_rate`, four agents run (lookup → revision check →
calculation → safety), and the calculator compares 520 against the documented markers: minimum 219, normal 482,
rated 482, design 520 → **within design, +7.9 % above normal**, with a supervision warning.

**Talking point:** the arithmetic is a deterministic calculator, not a model.

### 6. Explanation — a "why" question, with the model

```powershell
python -m workbench ask "Why is the crude heated before entering the atmospheric column?"
```

The first question in the set that calls the LLM. The `⟨calling qwen3:4b — why_summary⟩` line appears where the
call happens, and the step footer reports the model and how long it took (~20 s on a GTX 1650).

**Talking point:** if the model restates the question or narrates the task instead of answering — which small
local models do — `usable_narrative()` drops its prose and the quoted passages stand alone. The reason appears
in the step's reasoning lines.

### 7. Safety — precautions before work

```powershell
python -m workbench ask "What safety precautions are required before working on the crude charge pump?"
```

Classified `safety`, safety status `sensitive`, and governance sets **human review required**.

### 8. Comparison — two subjects side by side

```powershell
python -m workbench ask "Compare the operating limits of the two crude charge pumps."
```

Gather → align → revision check, producing a comparison matrix with a differences list.

### 9. Conflict — which value to trust

```powershell
python -m workbench ask "I found two different normal flow values for the crude charge pump. Which one should I trust?"
```

The revision/conflict agent collects every documented value with its source, then ranks by revision and
authority and explains the ranking.

**Talking point:** the workbench never silently picks one number.

### 10. Cross-document — where is it written

```powershell
python -m workbench ask "Which documents describe the startup procedure for the atmospheric heater?"
```

Sections, cross references, referenced documents and standing instructions, followed one hop.

### 11. Planning — the model extends the plan

```powershell
python -m workbench ask "Create a work plan for inspecting the crude charge pump."
```

The show-piece. The planner calls the LLM to refine the DAG, and the added steps appear in the Execution DAG
named after the agent they call (`graph_x`, `cross_document_x`) next to the template steps. Nine steps, six
agents, then gap analysis and assembly. Governance requires human review.

**Talking point:** the template is the floor — the model may add steps, never remove the safety one.

### 12. Report — a document, not an answer

```powershell
python -m workbench ask "Prepare a report on the atmospheric column operating envelope."
```

Six gathering steps feed the report agent, which writes a markdown report to `data/workbench/reports/`.

### 13. Complex — one request, five requirements

```powershell
python -m workbench ask "We need to take the running crude charge pump out of service for maintenance. Determine the correct changeover procedure, required prerequisites, isolation requirements, relevant operating limits, and supporting documents."
```

Classified `procedure` with `limits` as a secondary type, so the template is extended with an operating-envelope
step. Six steps, four agents, human review required.

### 14. Ambiguous — refusing to guess

```powershell
python -m workbench ask "How do I start it?"
```

The rules are confident it is a procedure request, but nothing anchors "it": no tag, no name, no session
history. The resolver reclassifies to `ambiguous`, Phase 2 retrieves nothing at all (route `none`), and the
answer is a clarification question.

**Talking point:** compare with question 3 — same verb, opposite outcome, and the reason is visible in the
resolver's reasoning lines.

### 15. Restricted — a request that must not be answered

```powershell
python -m workbench ask "Can I bypass the vacuum heater low fuel gas pressure trip temporarily?"
```

The safety gate matches a restricted pattern in Phase 0/1 and forces the task to `safety`. No bypass steps are
produced at any point; the answer is the documented authorization route plus a mandatory human review.

**Talking point:** the refusal happens in Phase 0/1, before retrieval — it is not a filter on the output.

---

## Suggested 10-minute running order

1. **3 (procedure)** — opens with the full five-phase trace and a real answer.
2. **5 (limits)** — deterministic arithmetic, four agents, fast.
3. **4 (troubleshooting)** — the nine-step DAG; explain the dependency arrows here.
4. **11 (planning)** — the LLM extending the plan; point at `graph_x` / `cross_document_x`.
5. **14 (ambiguous)** then **15 (restricted)** — back to back, the two refusals.
6. `python -m workbench trace --show` — the saved reasoning, for the "how do we audit this" question.

## Follow-ups worth having ready

- `python -m workbench ask "..." --json | ...` — the same run as the contract the web UI consumes.
- `python -m workbench bench` — 62 canonical prompts, ~2.5 minutes, deterministic.
- `python -m workbench agents` — the agent registry with each agent's phase and responsibility.
- `python -m workbench status` — detected VRAM, chosen profile, model, backend, documents loaded.
