# Workbench API — frontend integration contract

This document describes the HTTP interface of the Refinery Engineering AI Workbench as implemented in
`workbench/app/api.py`. Everything below is what the code does today; nothing is planned-only unless marked
**pending**.

## 1. Starting the server

```powershell
# from the repo root, with the .venv active
python -m workbench serve --host 127.0.0.1 --port 8000
```

- The server is FastAPI + uvicorn. Interactive docs are at `http://127.0.0.1:8000/docs` (OpenAPI).
- CORS is open (`allow_origins=["*"]`, all methods and headers), so a browser front end on any port can call it directly.
- On start-up the orchestrator loads the knowledge index (about 3 s from cache) and, because `RWB_WARM_START` defaults to `1`, warms the CPU embedder and reranker in a background thread (the reranker takes roughly 29 s on CPU). Requests are accepted immediately; the first semantic query may simply be slower if the warm-up has not finished.
- Health check: `GET /health` returns

```json
{"status":"ok","backend":"files","llm":"qwen3:4b","llm_available":true,"profile":"gpu_4gb","effort":"medium",
 "documents":["CDU operating manual"],"resources":{"active_requests":0,"llm":"qwen3:4b","llm_loaded":false,
 "keep_warm":false,"idle_unload_seconds":45,"embedder_loaded":true,"reranker_loaded":true,"recent":[]},
 "active_runs":0}
```

Useful environment variables (read in `workbench/config.py`): `RWB_LLM=off` (run without the language model),
`RWB_PROFILE=gpu_4gb|gpu_12gb|...`, `RWB_LLM_MODEL`, `RWB_OLLAMA_URL`, `RWB_RERANKER=off`, `RWB_KNOWLEDGE_BACKEND=files|mock|neo4j`,
`RWB_KEEP_WARM=1` (never unload the model between requests).

## 2. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness, backend, model, effort level, resource state |
| GET | `/agents` | list of agents (key, class, phase, description) |
| GET | `/schema` | JSON schemas of `FinalResponse`, `Block`, `UserRequest` |
| POST | `/ask` | run a request synchronously, returns `FinalResponse` |
| POST | `/runs` | start a request in the background, returns `{run_id, session_id}` |
| GET | `/runs` | all recorded runs (without the final response and event log) |
| GET | `/runs/{run_id}` | live state of one run (plan progress, final response when done) |
| GET | `/runs/{run_id}/events` | Server-Sent Events stream of progress |
| POST | `/runs/{run_id}/btw` | ask the status agent about one run |
| POST | `/btw` | ask the status agent about the latest run of a session |
| GET | `/sessions/{session_id}` | session memory (turns, uploads, pending clarification) |
| POST | `/upload` | upload a PDF or an image into a session |
| GET | `/reviews` | pending human-in-the-loop items |
| POST | `/reviews/{response_id}` | approve / reject a flagged response |
| GET | `/audit/{session_id}?audit_id=` | audit trail records of a session |

### 2.1 `POST /ask` — synchronous answer

Request body (`AskBody`):

```json
{"text": "What is the normal flow rate of the crude charge pump?",
 "session_id": "web-user-42", "user_role": "engineer",
 "options": {"want_report": false}, "attachments": []}
```

`options.want_report: true` makes the planner append a report step to any task. `attachments` is a list of
`{name, path, media_type}` objects that already exist on the server (normally you use `/upload` instead).

Response: a `FinalResponse` (section 4). A lookup takes tens of milliseconds without the LLM; a
troubleshooting or procedure request with the LLM on can take 10–60 s on the 4 GB card. For anything but
lookups prefer the asynchronous flow below so the UI can show progress.

### 2.2 `POST /runs` + `GET /runs/{run_id}/events` — asynchronous with progress

1. `POST /runs` with the same body as `/ask`. Response: `{"run_id": "run-3f9a1c2b", "session_id": "web-user-42"}`.
2. Open `GET /runs/{run_id}/events` with `EventSource`. The stream first **replays** events that already happened
   (each replayed item has `"replay": true`), then streams live events, sends `: keep-alive` comment lines every
   150 ms while idle, and ends with one `final` event whose `data` is the full `FinalResponse` JSON. After `final`
   the server closes the stream; the client should `close()` the EventSource.
3. `GET /runs/{run_id}` at any time returns the `RunState` (below), including `final` once finished.

Event names (defined in `workbench/core/events.py` and emitted by `workbench/orchestration/orchestrator.py` and
the executor):

| event | when | useful `data` |
|---|---|---|
| `phase_started` | a phase begins (`0/1 Understanding`, `2 Specialist retrieval`, `3 Planning`, `4 Execution`, `5 Governance`) | – |
| `phase_finished` | phase 0/1 and 2 finished | `task_type`, `entities`, `safety`, `route`, `gaps` |
| `plan_created` | the DAG is ready | `steps: [{id, agent, goal, depends_on}]` |
| `agent_started` | a plan step starts | `agent`, `step_id`, `message` = goal |
| `agent_finished` | a plan step ends | `ok`, `duration_ms`, `llm_calls`, `blocks`, `evidence`, `missing` |
| `llm_call` | an agent calls the local model | `agent`, `message` = purpose |
| `replan` | a required step failed and the plan was patched | `step_id`, `message` |
| `warning` | reserved (not emitted today) | – |
| `error` | the run crashed; a failed `FinalResponse` still follows | `message` |
| `final` | terminal event | the `FinalResponse` |

Each SSE `data` line is JSON with the `ProgressEvent` fields: `event`, `phase`, `agent`, `step_id`, `message`,
`data`, `ts` (unix seconds). Example frame:

```
event: agent_finished
data: {"event":"agent_finished","phase":"4 Execution","agent":"lookup","step_id":"lookup",
       "message":"5 documented value(s) for Crude Charge Pump (11-P-01)",
       "data":{"ok":true,"duration_ms":31,"llm_calls":0,"blocks":1,"evidence":5,"missing":[]},"ts":1789700000.12}
```

Minimal client:

```js
const {run_id} = await (await fetch("/runs", {method:"POST", headers:{"Content-Type":"application/json"},
                         body: JSON.stringify({text, session_id})})).json();
const es = new EventSource(`/runs/${run_id}/events`);
es.addEventListener("plan_created", e => renderPlan(JSON.parse(e.data).data.steps));
es.addEventListener("agent_finished", e => tickStep(JSON.parse(e.data)));
es.addEventListener("final", e => { renderResponse(JSON.parse(e.data)); es.close(); });
```

`RunState` (`GET /runs/{id}`) fields: `run_id`, `session_id`, `request_text`, `started`, `finished`, `phase`,
`task_type`, `safety_status`, `entities`, `goal`, `step_status` (list of `{step_id, agent, goal, depends_on,
status, summary}`), `current_step`, `recent_events` (last 60), `llm_calls`, `llm_seconds`, `safety_flags`,
`resources`, `final_status`, `error`, `final` (a `FinalResponse` or `null`).

### 2.3 "btw" side channel — `POST /runs/{run_id}/btw` and `POST /btw`

While a run is going (or after it finished) the user can ask about it without disturbing it. Body:
`{"text": "what is going on?", "session_id": "web-user-42"}` (`/btw` picks the latest run of that session; the
`/runs/{id}/btw` form targets one run). The status agent (`workbench/orchestration/status_agent.py`) uses no
LLM; it answers from the run state in milliseconds. Response:

```json
{"run_id":"run-3f9a1c2b","blocks":[ {"type":"text","id":"status","markdown":"**Run run-3f9a1c2b** — *The crude charge pump ...*  \nPhase: **4 Execution** · running for 6.2s · now: **diagnostic** — Extract possible causes  \nSteps: 3/9 complete"},
  {"type":"plan","id":"plan","title":"Plan progress","goal":"...","tasks":[...]},
  {"type":"table","id":"events","title":"Recent events","columns":["t","Event","Agent","Message"],"rows":[...]} ]}
```

The wording of the question changes the extra block: "what have you found so far" → a table of step
summaries; "why is it slow / how long" → timing and LLM-call figures; "plan" → the plan only; "safety" →
safety status and flag count; "entities" → resolved equipment; "resources / gpu / model" → what is loaded.
Render these blocks exactly like the blocks of a normal answer.

### 2.4 `POST /upload` — documents and images

`multipart/form-data` with fields `file`, `session_id`, optional `note`.

- **PDF**: the file is saved under `data/workbench/uploads/<session_id>/` and indexed in a background thread
  through the knowledge layer's own parser (Docling), normalizer, chunker and claim extraction
  (`workbench/services/ingest.py`). Response:
  `{"status":"indexing","kind":"pdf","run_id":"run-...","detail":"poll /runs/{run_id}; ..."}`. Poll
  `GET /runs/{run_id}`; when `final_status` is `"indexed"` the document is part of that session's knowledge
  (it is added to the composite knowledge service, so later `/ask` calls in the same server process search it too).
  Parsing is slow on a 4 GB card (minutes for a few dozen pages); show the `phase` field (`ingest:parsing`,
  `ingest:normalizing`, `ingest:indexing`) as progress. Uploaded documents get authority rank 40 (the manual is 60),
  so their values rank below the manual in conflict resolution.
- **Image** (`.png .jpg .jpeg .webp .bmp .gif .tif .tiff`): described synchronously by the vision model (loaded
  on demand and freed straight after). Response `{"status":"described","kind":"image","description":"..."}`. The
  description is stored as a session note; it is not added to the knowledge index.
- Other types: `{"status":"unsupported", ...}`.

`GET /sessions/{session_id}` lists `uploaded_documents` with their `stats`.

### 2.5 Reviews (human in the loop)

Responses with `requires_human_review: true` are recorded. `GET /reviews` returns pending items
(`response_id`, `session_id`, `audit_id`, `reason`, `request`, `task_type`). `POST /reviews/{response_id}` with
`{"decision":"approved"|"rejected","reviewer":"name","note":""}` records the decision. Nothing is blocked
automatically: the answer was already delivered with the flag; the review is a record.

### 2.6 Audit

`GET /audit/{session_id}` (optionally `?audit_id=`) returns the JSONL records written during runs: `request`,
`structured_request`, `plan`, `replan`, `step_result`, `final`, `error`. The `audit_trail_id` in every
`FinalResponse` selects one run.

## 3. Sessions and follow-ups

- `session_id` is chosen by the client (any string). Memory is kept in `data/workbench/sessions/<id>.json`
  (last 20 turns): request text, task type, resolved entities, parameter, scenario, status.
- Pronouns and generic words ("it", "this pump", "the heater") are resolved against the previous turns'
  entities, so "What is its design pressure?" after a question about the vacuum column works.
- When a request cannot be anchored (no equipment, no unit, no parameter) the response has
  `status: "clarification"` and a `clarification` block with `missing` and `options`. Send the user's answer as a
  new request in the same session (for example "the crude charge pump, m3/h"); the resolver uses the session to
  complete it. `GET /sessions/{id}` shows `pending_clarification` while one is open.
- Use one session per conversation thread; use a fresh session when you do not want carry-over.

## 4. `FinalResponse`

```json
{"response_id":"resp-9b1c...","session_id":"web-user-42","created_at":"2026-09-15T04:17:02+00:00",
 "task_type":"limits","secondary_task_types":[],"status":"answered",
 "answer_markdown":"...plain markdown fallback...",
 "blocks":[...],"evidence":[...],
 "confidence":{"score":0.69,"basis":"agents 0.84 × verification 1.00","uncertainties":[]},
 "safety_flags":[{"severity":"warning","message":"...","evidence":[...],"requires_authorization":false,"source_agent":"safety"}],
 "requires_human_review":true,"review_reason":"The given value lies outside the documented design envelope.",
 "plan":{"plan_id":"plan-...","goal":"...","steps":[{"step_id":"claims","agent":"lookup","goal":"...","status":"done",...}]},
 "audit_trail_id":"20260915-041702-a1b2c3","entities":["Crude Charge Pump (11-P-01)"],
 "timing_ms":58,"llm_calls":0,"backend":"files","warnings":[]}
```

| field | meaning |
|---|---|
| `status` | `answered`, `clarification` (ask the user, see the `clarification` block), `restricted` (request to bypass a protection; no instructions given), `needs_review` (answered but confidence below 0.35 — show as a lead, not an answer), `failed` |
| `task_type` / `secondary_task_types` | one of `lookup, multi_hop, procedure, troubleshooting, limits, explanation, safety, comparison, conflict, provenance, planning, report, cross_document, ambiguous` |
| `answer_markdown` | the same content rendered to Markdown by `blocks_to_markdown` (no evidence/audit); use only if you cannot render blocks |
| `blocks` | ordered render blocks (section 5) |
| `evidence` | the labelled evidence list; `ref` is the `[n]` label used by blocks |
| `confidence` | `score` 0–1, `basis` (formula in words), `uncertainties`; level is `high ≥ 0.75`, `medium ≥ 0.45`, else `low` |
| `safety_flags` | de-duplicated flags from all agents (`severity` info/caution/warning/danger) |
| `requires_human_review` / `review_reason` | set by the governance agent (restricted request, value outside design, emergency/isolation/changeover/shutdown procedure, DANGER flag, any planning answer) |
| `plan` | the executed DAG with per-step `status` (`pending/running/done/failed/skipped`) and `note` |
| `warnings` | context gaps and "not documented: ..." items |

## 5. Block types (`workbench/core/blocks.py`)

Every block has `type`, `id`, optional `title`, and `citations: ["[1]", "[3]"]`. Render a block's `citations`
as small superscript links to the evidence list; some blocks also carry per-item `citation` fields or
`row_citations`. All labels are `"[n]"` strings; `n` indexes `FinalResponse.evidence` in order (`evidence[n-1].ref == "[n]"`).

| type | fields | how to render |
|---|---|---|
| `text` | `markdown` | Markdown paragraph(s); the report agent uses `## Heading` lines here |
| `callout` | `level` (info/success/warning/danger), `markdown` | coloured banner; the first block of most answers is the one-line lead callout |
| `kpi` | `items[]`: `label, value, unit, qualifier, citation` | row of value tiles (e.g. "volumetric flow rate — 482 m3/h (normal)") |
| `table` | `columns[]`, `rows[][]` (string/number/null), `row_citations[][]`, `caption` | data table; put the row's citations at the end of the row |
| `steps` | `procedure_id, procedure_type, document_id, section_path, page_start, page_end, prerequisites[]`, `steps[]` of `{sequence, text, page, citation, is_prerequisite, warnings[], mentions[]}` | ordered list; show `prerequisites` as a checklist above; render `warnings` as an inline badge on the step; `mentions` are equipment tags you can make clickable |
| `graph` | `nodes[]` `{id, label, type, is_focus, properties}`, `edges[]` `{source, target, label, citation, inferred}`, `layout` (left-right/top-down/radial), `mermaid` | draw with a graph library from nodes/edges (highlight `is_focus`, dash `inferred` edges) or simply render the `mermaid` string |
| `plan` | `goal`, `tasks[]` `{id, title, agent, depends_on[], status, summary, safety_sensitive}`, `mermaid` | DAG / checklist with status badges; the executed plan is titled "How this answer was produced"; a work plan from the planner is titled "Proposed work plan" |
| `evidence` | `items[]` `{ref, document_id, page, chunk_id, claim_id, section_path, text, source, revision}` | numbered list at the end; anchor targets for the `[n]` labels; `source` is table/procedure/narrative/rule/... |
| `comparison` | `subjects[]`, `attributes[]`, `cells[][]` (rows = attributes, columns = subjects) of `{value, unit, citation, note}`, `differences[]` | matrix with a highlighted "differences" list; `value: null` means not documented |
| `limit_gauge` | `entity, parameter, unit, value` (may be null), `markers[]` `{label, value, citation}` (minimum/normal/maximum/design/trip...), `verdict` (within_normal/within_design/outside_design/unknown), `message` | a horizontal gauge with the markers and a needle at `value`; colour by verdict |
| `conflict` | `subject, parameter, claims[]` `{value, unit, document_id, revision, page, source, context, citation, temporal_status}`, `status` (corroborated/different_context/potential_conflict/resolved/unresolved), `preferred_index`, `resolution` | card listing each value with its source; mark `claims[preferred_index]` as preferred; show `resolution` text |
| `safety` | `flags[]` `{severity, message, citation, requires_authorization}` | list with severity colours; `requires_authorization` shows a lock icon |
| `confidence` | `score, level, basis, uncertainties[]` | meter plus a tooltip with `basis`; list `uncertainties` |
| `clarification` | `question, missing[], options[]` | prompt with clickable `options` (each option can be sent back as the next request text) |
| `image` | `url, caption, page` | image (not produced by the agents yet; reserved for figure crops) |
| `audit` | `audit_id, phases[]` `{name, agent, status, duration_ms, note}`, `llm_calls, backend` | collapsible footer |

Ordering rule used by the governance agent: lead callout → primary blocks in plan order (or the report's
own order for reports) → safety blocks → verification notes (only when there are issues) → executed plan (when
the run had more than 3 steps) → human-review callout → confidence → evidence → audit.

## 6. Schemas

`python -m workbench schema` writes `docs/schema/final_response.schema.json`, `block.schema.json`,
`user_request.schema.json` and `progress_event.schema.json` (JSON Schema draft 2020-12 from the pydantic
models). `GET /schema` serves the first three live. Generate TypeScript types from them
(for example with `json-schema-to-typescript`) rather than hand-writing interfaces.

## 7. Notes for the UI

- Show progress from the SSE stream; a run with the LLM on can take a minute on a 4 GB GPU, while lookups
  return in well under a second.
- Always render `safety` blocks and the human-review callout prominently; never hide them behind a toggle.
- `status: "restricted"` answers contain no operating instructions by design; show them as they are.
- `answer_markdown` is a fallback only; the blocks carry more structure (citations per row, gauge markers, DAG edges).
