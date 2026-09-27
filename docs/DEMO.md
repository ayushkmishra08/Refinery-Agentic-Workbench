# Demo script — Refinery Engineering AI Workbench

Nineteen questions, one per capability, in the order they are best presented. Each shows the full agent trace in
the terminal and writes a JSON thinking trace to `data/workbench/thinking/`.

## Before you start: sign-in and preparation

Access control is on by default and most of these questions are answered from the CDU operating manual, which
is tagged `SECRET` (admin only). Signed in as `manager` or `user`, they are refused. Do this once before the demo:

```powershell
python -m workbench setup-security        # creates admin / manager / user and tags the six documents
python -m workbench login --user admin    # seeded password Admin#2026 unless you set RWB_ADMIN_PASSWORD first
python -m workbench whoami                # confirm: admin, reads SECRET
```

For a rehearsal without accounts, `$env:RWB_AUTH = "off"` removes the gate for that shell.

Models (see `SETUP.md`): on the 4 GB reference machine (`gpu_4gb` profile) pull `qwen3:4b` and `qwen3.5:2b`
and have Ollama running; the explanation (6), planning (11) and effort (19) demos need them. The model router is
on by default and picks among whichever of the seven registry models are installed, so the model names shown in
the trace depend on what you have pulled (`python -m workbench models`; `RWB_ROUTING=off` pins every call to
the profile's text model). Cache `BAAI/bge-small-en-v1.5` and `BAAI/bge-reranker-base` while still connected:
the air-gap guard sets the Hugging Face libraries offline, so a missing embedding or reranker model fails
instead of downloading.

**About the timings in this script.** The default effort (`medium`) *does* call the model: the Answer Composer
writes the released answer (and the classifier or follow-up rewriter may be asked). With Ollama reachable that
adds seconds to every question. The millisecond figures quoted below are for the deterministic path, and are
reproduced with either `--effort low` (index only, no model, no vectors) or `$env:RWB_LLM = "off"` (default
effort, model disabled; this is how `bench` runs). If Ollama is not running at all, the workbench falls back
to the same deterministic answer. Times are per request as shown in the trace; starting the command also loads
the indexes, which takes a few seconds.

## Running

Run them exactly as written:

```powershell
python -m workbench ask "<question>"
```

Useful flags while presenting:

| flag | effect |
|---|---|
| *(none)* | `medium` effort: phase-by-phase thinking, then the answer (written by the Answer Composer through the model when Ollama is up), then the saved trace path |
| `--effort low` | index only (rules, claims, BM25), no model and no vectors — every question answers in well under a second |
| `--effort high` | wider retrieval + reranker + model-written narrative + model plan refinement |
| `--effort ultra` | everything on; minutes per request on a 4 GB card |
| `--no-thinking` | the answer only |
| `--detail` | every render block (values, topology, evidence, confidence) instead of the composed answer |
| `--json` | the raw `FinalResponse`, including the confidence and audit blocks the CLI hides |
| `python -m workbench trace` | list the saved traces, newest first |
| `python -m workbench trace --show` | replay the newest trace as text |
| `python -m workbench repl` | interactive; type `btw <question>` while a run is in progress |

The CLI answer carries the engineering content and its citations. Confidence scores, the grounding check, the
executed DAG and the audit id are in the thinking above it and in the saved JSON trace — `--json` returns all
of them for the frontend.

**If the GPU is busy, run the whole set with `--effort low`.** Every question still answers from the indexes;
the model-composed answer, the model-written prose, the model-refined plan and vector search drop out, and the
answer is assembled deterministically from the same evidence.

---

## The questions

### 1. Lookup — a documented value

```powershell
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
```

The cheapest possible path, and the one to open with: rules classify it as `lookup` with no model call, the
alias index resolves "crude charge pump" to **Crude Feed Pump (11-PM-01)** and shows the three other tags that
share that name, and the `claims` route answers from the claim index with **no text search at all**. One agent,
two steps, ~25-30 ms with the model off (`--effort low` or `RWB_LLM=off`; 29 ms in the latest benchmark run).
At the default effort the Answer Composer then writes the answer through the model, which adds seconds.

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
python -m workbench ask "Why is the crude heated before entering the atmospheric column?" --effort high
```

The question in the set that shows the explanation agent's own model call. The agent-written narrative
(`why_summary`) is a `high`-effort feature; at the default effort the explanation agent returns the quoted
passages and the Answer Composer writes the released answer from them through the model. Only with the model
off (`--effort low` or `RWB_LLM=off`) does the question come back as the quoted passages alone in ~30 ms. The
`⟨calling <model> — why_summary⟩` line appears where the call happens, and the step footer reports the model and
how long it took (~20 s on a GTX 1650). The model named there is the router's choice for a summarization call
among the installed models (`python -m workbench models` shows which one wins summarization on your machine);
with `RWB_ROUTING=off` it is always the profile's text model, `qwen3:4b` on `gpu_4gb`.

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
python -m workbench ask "Create a work plan for inspecting the crude charge pump." --effort high
```

The show-piece, and the reason `--effort high` exists. At that level the planner calls the LLM to refine the
DAG, and the added steps appear in the Execution DAG named after the agent they call (`graph_x`,
`cross_document_x`) next to the template steps. Nine steps, six agents, then gap analysis and assembly.
Governance requires human review. At the default effort the planner does not refine the plan: the same
question runs the seven-step template, and the only model call is the Answer Composer's. With the model off
(`--effort low` or `RWB_LLM=off`) the template run takes under a second.

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

### 16. Inventory — what is in here at all

```powershell
python -m workbench ask "What are all the equipments in the refinery?"
```

No equipment is named, and that is the point: the question is about the corpus, not about an item. It
classifies as `inventory`, takes the `inventory` route — which **lists** the entity index rather than searching
it — and answers in ~30 ms with the model off (`--effort low` or `RWB_LLM=off`; at the default effort the
Answer Composer adds a model call) with a class table, the most-referenced items with their tag and page range,
then the documents, chapters and standing instructions in scope.

The count depends on who is asking, because the entity index is filtered to the documents the caller may read.
Signed in as `admin`, with the six current documents, it is **520 plant-tagged items across 14 classes**, 516
of them from the CDU operating manual. `manager` and `user` cannot read the CDU manual (`SECRET`), and the
other documents carry few plant-numbered tags, so for them the table shows only the 6 tagged items in the
API560 comparison document.

**Talking point:** this is the question that used to come back "which equipment do you mean?". A survey
question has no single subject, so asking for one was the wrong move, not a missing detail.

### 17. Inventory, narrowed — one class, one section

```powershell
python -m workbench ask "List all the pumps"
python -m workbench ask "How many columns are there?"
python -m workbench ask "What equipment is in the vacuum section?"
```

"pumps" is read as the *class* to list, not as an entity to resolve — the resolver drops it from the entity
list and sets it as the subject type instead. The section question filters on the plant number the manual itself
uses (11- atmospheric, 12- vacuum).

**Talking point:** the counts and the table come from the same filter, and the filter is the manual's own tag
convention — plant-numbered tags only. That is why it answers (as `admin`) "6 columns" and not "13": `C-1` … `C-6` are rows
of an inspection checklist that happen to match the column tag pattern, and `D-86` is the ASTM distillation
method, not a drum.

### 18. Out of scope — knowing what it cannot answer

```powershell
python -m workbench ask "What is the capital of France?"
python -m workbench ask "Write me a poem about pumps"
python -m workbench ask "hello"
```

None of these can be answered from an operating manual, and none of them is a missing detail. The resolver
marks them out of scope and the answer states what the workbench does answer, with examples built from the
documents actually loaded.

**Talking point:** run this straight after question 14. Same "I can't answer that" situation, two different
and correct responses: ask for the missing equipment, or explain the scope.

### 19. Effort — the same question at three depths

```powershell
python -m workbench ask "The crude charge pump discharge pressure is dropping. What should I check?" --effort low
python -m workbench ask "The crude charge pump discharge pressure is dropping. What should I check?" --effort high
```

Identical nine-step plan, identical agents. `low` answers from the indexes in ~0.7 s with 10 citations and no
model call; `high` adds vector search, the reranker, a wider net and the model calls (narrative, composed
answer), and returns 20 citations in ~22 s on the GTX 1650. The default (`medium`, no flag) sits between them:
vectors but no reranker, and the Answer Composer's model call. Both timings were measured with Ollama running
and depend on the model the router picks.

**Talking point:** effort buys evidence and model involvement, not a different pipeline — and `low` is the
setting to fall back on if the GPU is busy mid-demo.

---

## Suggested 10-minute running order

0. Sign in as `admin` first (see *Before you start*), or the CDU questions are refused.
1. **16 (inventory)** — "what is in here at all", answered in ~30 ms with the model off (`--effort low`). Sets
   up the corpus before anything else.
2. **3 (procedure)** — the full five-phase trace and a real answer.
3. **5 (limits)** — deterministic arithmetic, four agents, fast.
4. **4 (troubleshooting)** — the nine-step DAG; explain the dependency arrows here.
5. **11 (planning, `--effort high`)** — the LLM extending the plan; point at `graph_x` / `cross_document_x`.
6. **14 (ambiguous)**, **18 (out of scope)**, **15 (restricted)** — three refusals, three different right answers.
7. `python -m workbench trace --show` — the saved reasoning, for the "how do we audit this" question.

## Follow-ups worth having ready

- `python -m workbench ask "..." --json | ...` — the same run as the contract the web UI consumes.
- `python -m workbench bench` — 70 canonical prompts in 16 categories, run with the model and access control
  off, ~30 s, deterministic. The latest run (2026-09-16 20:20, `data/workbench/reports/benchmark-20260916-202058.md`)
  scored 1.0 on every metric (task accuracy, agents, entities, blocks, safety, answered), mean 434 ms per
  prompt, 30.6 s total, 0 LLM calls. `--llm` runs it with the model; `--category` / `--limit` run a subset.
- `python -m workbench agents` — the agent registry with each agent's phase and responsibility.
- `python -m workbench status` — detected VRAM, chosen profile, model, backend, documents loaded.

## New capabilities demo (September 2026)

Exact commands; the operator manual (`docs/manual/index.html` §12) has the 15-minute running order. These
commands also need a signed-in account (or `RWB_AUTH=off`); run them as `admin`. The JSON after `--args` is
written for Windows PowerShell 5.1, which strips unescaped double quotes when it passes an argument to a native
program: keep the `\"` escapes there (in PowerShell 7.3+ or a POSIX shell, drop the backslashes).

```powershell
# multi-model routing
python -m workbench models
python -m workbench route "Read the scanned P&ID, calculate the margin between 482 and 520 m3/h and write a short note"

# named local tools and the agent loop
python -m workbench tools
python -m workbench tool calculate --args '{\"expression\": \"(520-482)/482*100\"}'
python -m workbench tool search_documents --args '{\"query\": \"crude charge pump normal flow\", \"k\": 5}'
python -m workbench agent "calculate 2*(3+4)"

# the sandbox: no egress, bounded, ephemeral, verified by tests
# Caveat for the presenter: without Docker the subprocess backend is used, and its isolation is Python-level
# patches only. Outbound sockets are refused as shown, but file reads are not confined, so do not present it
# as protecting data/ from the code it runs.
@'
import socket
try:
    socket.create_connection(("8.8.8.8", 53), timeout=2)
except PermissionError as e:
    print("blocked:", e)
print(sum(range(10)))
'@ | Set-Content -Encoding utf8 demo_sandbox.py
@'
def test_sum():
    assert sum(range(10)) == 45
'@ | Set-Content -Encoding utf8 demo_tests.py
python -m workbench sandbox-run demo_sandbox.py --tests demo_tests.py

# multimodal intake (OCR on device; one vision call)
python -m workbench intake "data/sample docs/test.pdf" --max-pages 3 --vision-calls 1
python -m workbench intake "data/sample docs/test.pdf" --no-vision

# deliverables under human sign-off
python -m workbench ask "The crude charge pump is operating at 520 m3/h. Is this acceptable?"
python -m workbench responses
python -m workbench export <response_id> --format xlsx      # then docx, pptx
python -m workbench drafts -v
python -m workbench draft-signoff <draft_id>                  # blocked (exit 2) while any figure is flagged
python -m workbench draft-resolve <draft_id> <figure_id> corrected --value 24.45 --note "checked p.2"
python -m workbench draft-signoff <draft_id>

# vault: envelope encryption per branch
# seal needs an administrator ("sealing needs an administrator" otherwise); rotate and revoke do not
# currently check the role in the CLI, so run the whole block signed in as admin
python -m workbench vault seal
python -m workbench vault status
python -m workbench vault rotate manager
python -m workbench vault revoke "Crude desalter" manager
$env:RWB_VAULT = "on"; python -m workbench serve --port 8077   # branches decrypt per session; 8077 is the port the web front end proxies to (serve defaults to 8000)

# sovereignty: air-gap proof
python -m workbench sovereignty
python -m workbench sovereignty --watch 5        # pull the uplink cable; watch DISCONNECTED / interface_down
python -m workbench audit-verify

# signed model packages
python -m workbench packages keygen release-2026
python -m workbench packages sign .\my-model-pkg --private-key data\workbench\security\signers\release-2026.key --key-id release-2026 --name mymodel --version 1
python -m workbench packages trust data\workbench\security\signers\release-2026.pub release-2026
python -m workbench packages verify .\my-model-pkg
python -m workbench packages import .\my-model-pkg --dry-run

# TLS / mutual TLS between components
python -m workbench serve --port 8077 --mtls
```

