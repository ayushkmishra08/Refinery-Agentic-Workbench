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
| POST | `/auth/login` | username + password -> `{token, role, readable_tags, readable_documents}` |
| POST | `/auth/logout` | revoke the bearer token |
| GET | `/auth/whoami` | the caller's role, tags, readable documents and second-factor state |
| GET | `/auth/mfa` | whether this role needs an authenticator and whether one is enrolled |
| POST | `/auth/mfa/enrol` | start enrolment -> `{secret, otpauth_uri}`; nothing is stored until confirmed |
| POST | `/auth/mfa/confirm` | prove the app works with a code; that code is then spent |
| DELETE | `/auth/mfa` | remove an authenticator (your own; an admin may remove anyone's) |
| GET | `/stats` | counts for the caller's workspace, summed over what this role may read |
| GET | `/knowledge/tree` | the knowledge layer branch by branch, with this role's reach on each |
| POST | `/security/documents/{document_id}/roles` | pin who reads one branch (admin only) |
| GET | `/security` | the role schema, the document tags, and the caller's place in both |
| POST | `/access-requests` | ask a higher role to release what one question needs |
| GET | `/access-requests` | your own requests and their state |
| GET | `/approvals` | your approval queue, with the material each request would release |
| POST | `/approvals/{id}/approve` | approve and receive the one-time key (shown once) |
| POST | `/approvals/{id}/deny` | refuse a request |
| GET | `/security-log` | the security audit trail (manager and above) |
| POST | `/ask` | run a request synchronously, returns `FinalResponse` |
| POST | `/runs` | start a request in the background, returns `{run_id, session_id}` |
| GET | `/runs` | all recorded runs (without the final response and event log) |
| GET | `/runs/{run_id}` | live state of one run (plan progress, final response when done) |
| GET | `/runs/{run_id}/events` | Server-Sent Events stream of progress |
| POST | `/runs/{run_id}/btw` | ask the status agent about one run |
| POST | `/btw` | ask the status agent about the latest run of a session |
| GET | `/sessions/{session_id}` | session memory (turns, uploads, pending clarification) |
| POST | `/upload` | attach a PDF or an image to **this conversation** (parsed, indexed, session-only) |
| DELETE | `/upload?session_id=` | forget everything attached to this conversation |
| POST | `/knowledge/documents/{id}/promote` | move an attachment into the shared knowledge layer (manager+) |
| GET | `/reviews` | pending human-in-the-loop items |
| POST | `/reviews/{response_id}` | approve / reject a flagged response |
| GET | `/audit/{session_id}?audit_id=` | audit trail records of a session |

### 2.0 Authentication — sign in before anything else

Every document carries a tag and a caller without a token is cleared for nothing, so `POST /ask`
answers `401`. Three roles — `user` < `manager` < `admin` — read three tags — `INTERNAL` <
`CONFIDENTIAL` < `SECRET`. See **[`docs/SECURITY.md`](SECURITY.md)** for the model, the escalation
flow and how an access key is verified.

```http
POST /auth/login
{"username": "manager", "password": "Manager#2026", "label": "web"}

200 {"token": "y7Qd...", "username": "manager", "role": "manager", "readable_tags": ["INTERNAL", "CONFIDENTIAL"],
     "expires": 1789459200.0, "must_change_password": true,
     "readable_documents": ["CDU operating manual"], "withheld_documents": []}
```

`401` on a wrong password, `423` while the account is locked (five failures inside fifteen minutes lock
it for fifteen). `must_change_password: true` means the account still carries its seeded password —
surface that in the UI.

Send the token back on every subsequent call, either as a header or in the body:

```http
Authorization: Bearer y7Qd...
```
```json
{"text": "...", "session_id": "web-user-42", "auth_token": "y7Qd..."}
```

`GET /auth/whoami` returns the same shape as the login response plus the full document listing with
each document's classification and the reason for it — enough to build a "you are signed in as ... and
may read ..." panel. `POST /auth/logout` revokes the token; the token also expires after eight hours,
and changing an account's role invalidates every token minted under the old one. Tokens are stored as
SHA-256 digests, so a lost token cannot be recovered from the server — issue a new one.

Access decisions are audited: `GET /audit/{session_id}` carries a `kind: "access"` record for every
request, allowed or denied.

### 2.1 `POST /ask` — synchronous answer

Request body (`AskBody`):

```json
{"text": "What is the normal flow rate of the crude charge pump?",
 "session_id": "web-user-42", "user_role": "engineer", "auth_token": "y7Qd...",
 "options": {"want_report": false}, "attachments": []}
```

`options.want_report: true` makes the planner append a report step to any task. `attachments` is a list of
`{name, path, media_type}` objects that already exist on the server (normally you use `/upload` instead).
`user_role` is a display hint only — the access policy reads the token and nothing else.

`401` means the caller is not cleared for any loaded document; the body names the document, its
classification and the role that would open it.

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

### 2.5b Attachments — a document that belongs to one conversation

`POST /upload` (multipart: `file`, `session_id`, `note`) parses a PDF with the knowledge layer's
own pipeline and indexes it **for the conversation that uploaded it**. It is not added to the
shared corpus, not classified, and nothing is written into `data/knowledge`; the parse is cached
under `data/workbench/uploads/_cache/<hash>/` so the same file is not parsed twice.

Who can read it: the person who uploaded it, in that conversation, whatever their role — it is
their own file. Nobody else, in any other conversation, by any phrasing.

Parsing is slow the first time a file is seen — minutes for a large document — so the call
returns immediately:

```jsonc
{ "status": "indexing", "kind": "pdf", "run_id": "run-ab12", "document_id": "GNH-eng" }
```

Poll `GET /runs/{run_id}` until `finished` is set: `final_status` is `indexed` or `failed`,
`phase` walks through `ingest:parsing` → `ingest:normalizing` → `ingest:indexing`, and `progress`
carries real counts from the parser:

```jsonc
{ "done": 12, "total": 32, "unit": "pages", "percent": 31 }
```

Those are pages the parser has actually finished, reported as each window lands — not an estimate.
Reading the pages is weighted at 85% of `percent` because that is where the wall clock goes; the
stages after it carry the remainder. Pages recovered from a checkpoint count as done.

**Do not let a question be asked before `final_status` is `indexed`.** It will be answered from
every document *except* the one being read, so it comes back as a confident answer about the wrong
thing — which is indistinguishable from the feature being broken.

An answer drawn from an attachment names it in `security.attached_documents`, and the attachment
contributes no classification: nobody has classified it, so it does not stamp the answer with a
tier it does not belong to.

`DELETE /upload?session_id=` forgets them. `POST /knowledge/documents/{id}/promote`
(`{session_id, tag?}`) is the deliberate opposite: it re-parses into the knowledge layer's own
directories, adds the document to the shared corpus and classifies it (`CONFIDENTIAL` unless a
`tag` is given). That needs **manager or above**, because the person doing it is deciding what
everyone else will be able to read.

### 2.6 The knowledge layer and who reads it

`GET /knowledge/tree` returns one **branch** per document — the unit access is granted in — with
the caller's reach marked on each:

```jsonc
{
  "role": "user", "readable": 4, "locked": 2,
  "branches": [
    { "document_id": "API 610 pump standard", "tag": "INTERNAL",
      "roles": ["user", "manager", "admin"], "compartmented": false, "readable": true,
      "pages": 82, "counts": { "entities": 8, "claims": 0, "procedures": 2, "chunks": 72 },
      "chapters": [ { "number": 1, "title": "Product description" } ] },

    // a locked branch reports its name, its tag and who to ask — and nothing else. No chapters,
    // no page total, no counts: that would measure the tier above the caller.
    { "document_id": "CDU operating manual", "tag": "SECRET",
      "roles": ["admin"], "compartmented": false, "readable": false, "ask": "admin" }
  ]
}
```

`POST /security/documents/{document_id}/roles` with `{"roles": ["manager"]}` pins an explicit
reader allowlist — a **compartment**, read by exactly those roles however senior anyone else is.
`{"roles": null}` hands the document back to the tag ladder. Administrators only (`403`
otherwise, `404` for a document that is not loaded). The tag is never changed by this call: it
still says how sensitive the material is, and the banners, refusals and audit trail all read it.

### 2.7 Audit

`GET /audit/{session_id}` (optionally `?audit_id=`) returns the JSONL records written during runs: `request`,
`structured_request`, `plan`, `replan`, `step_result`, `final`, `error`. The `audit_trail_id` in every
`FinalResponse` selects one run.

## 3. Sessions and follow-ups

- `session_id` is chosen by the client (any string). Memory is kept in `data/workbench/sessions/<id>.json`
  (last 20 turns): what was typed, what it was read as, task type, resolved entities, parameter, scenario,
  status, any tag corrections, and the composed answer.
- Pronouns and generic words ("it", "this pump", "the heater") are resolved against the previous turns'
  entities, so "What is its design pressure?" after a question about the vacuum column works.
- A turn that depends on the one before it is **rewritten into a standalone question** before the pipeline
  sees it. "What if we use 11-E-01 instead?" becomes a comparison against whatever the previous turn was
  about, and comes back with `task_type: "comparison"` and both subjects in `entities`. `GET /sessions/{id}`
  shows `rewritten_request` and `followup_kind` (`new`, `substitution`, `elaboration`, `continuation`) per
  turn — useful if the UI wants to show "read as: ..." under the question.
- A tag the documents do not contain is matched against the ones they do. When one candidate is clearly the
  item meant, the answer opens by saying so and goes on to answer about it; when two are equally close the
  response is a `clarification` block whose `options` name them. Either way the turn's `corrections` record
  what was typed and what it was read as.
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
| `status` | `answered`, `clarification` (ask the user, see the `clarification` block), `restricted` (request to bypass a protection; no instructions given), `unauthorized` (the caller is not cleared for any loaded document; returned as `401` by `/ask`), `needs_review` (answered but confidence below 0.35 — show as a lead, not an answer), `failed` |
| `task_type` / `secondary_task_types` | one of `lookup, multi_hop, procedure, troubleshooting, limits, explanation, safety, comparison, conflict, provenance, planning, report, cross_document, ambiguous` |
| `answer_markdown` | **the released answer**: the composed prose, the one or two blocks prose cannot carry (ordered steps, a limit gauge, a comparison), any documented DANGER, and a source line. This is what a chat-style UI shows; `blocks` is for a panelled one. `RWB_ANSWER_STYLE=full` makes it the whole block rendering instead |
| `blocks` | ordered render blocks (section 5). `blocks[0]` is the composed answer as a `text` block with `id: "answer"` on an answered request |
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
