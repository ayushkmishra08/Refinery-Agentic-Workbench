# Workbench API — frontend integration contract

This document describes the HTTP interface of the Refinery Engineering AI Workbench as implemented in
`workbench/app/api.py` (the question path, sign-in, sessions) and `workbench/app/api_ext.py` (the extended
surface, section 8). Everything below is what the code does today, including behaviour that is plainly a
defect — those places are marked **Note: currently …**. Nothing is planned-only unless marked **pending**.

## 1. Starting the server

```powershell
# from the repo root, with the .venv active
python -m workbench serve --host 127.0.0.1 --port 8077     # the WebPage dev proxy expects 8077; add --tls or --mtls for local TLS
```

- `serve` defaults to port **8000** when `--port` is not given; the WebPage dev proxy expects **8077**, so pass it.
- The server is FastAPI + uvicorn (API version `0.3.0`). Interactive docs are at `http://127.0.0.1:8077/docs` (OpenAPI).
- CORS is open (`allow_origins=["*"]`, all methods and headers), so a browser front end on any port can call it directly.
- On start-up the orchestrator loads the knowledge index (about 3 s from cache), starts the network monitor, and,
  because `RWB_WARM_START` defaults to `1`, warms the CPU embedder and reranker in a background thread (the
  reranker takes roughly 29 s on CPU). Requests are accepted immediately; the first semantic query may simply be
  slower if the warm-up has not finished.
- Health check: `GET /health` (no sign-in) returns (example values)

```json
{"status":"ok","backend":"files","llm":"qwen3:4b","llm_available":true,"profile":"gpu_4gb","effort":"medium",
 "documents":["API560-Comparison-All-in-One-05.12.17-Rev+C","API610 pump operation manual",
              "Bulletin-5EH-Steam-Jet-Ejectors","CDU operating manual","Crude desalter","ESBWR"],
 "resources":{"active_requests":0,"llm":"qwen3:4b","llm_loaded":false,"keep_warm":false,"idle_unload_seconds":45,
              "embedder_loaded":true,"reranker_loaded":true,"recent":[]},
 "active_runs":0,
 "access_control":true,"answer_style":"brief","compose_answers":true,
 "routing":true,"resident_model":null,"airgap_enforced":true,"network_monitor":true,
 "workbench_external_connections":0,"vault":true,"sandbox":true,"tools":12,"tls":false,"mtls":false}
```

| field | meaning |
|---|---|
| `backend`, `llm`, `llm_available`, `profile`, `effort` | knowledge backend in use, default text model, whether Ollama answers, hardware profile, effort level |
| `documents` | ids of every loaded document (not filtered by the caller's role — `/health` is unauthenticated) |
| `resources`, `active_runs` | model/embedder residency and the number of unfinished runs |
| `access_control` | `false` when `RWB_AUTH=off` |
| `answer_style` | `brief` or `full` (`RWB_ANSWER_STYLE`) |
| `compose_answers` | whether the Answer Composer may call the model (`RWB_LLM_ANSWER`) |
| `routing`, `resident_model` | multi-model router active; the model it currently holds resident |
| `airgap_enforced`, `network_monitor`, `workbench_external_connections` | egress guard on, monitor thread running, external connections attributed to the workbench process |
| `vault`, `sandbox`, `tools` | vault service present, sandbox present, number of registered tools |
| `tls`, `mtls` | whether the server was started with `--tls` / `--mtls` |

Useful environment variables (read in `workbench/config.py`): `RWB_LLM=off` (run without the language model),
`RWB_PROFILE=gpu_4gb|gpu_12gb|...`, `RWB_LLM_MODEL`, `RWB_OLLAMA_URL`, `RWB_RERANKER=off`, `RWB_EFFORT=low|medium|high|ultra`,
`RWB_KNOWLEDGE_BACKEND=auto|files|mock|neo4j` (default `auto`: `files` when knowledge-layer artefacts exist, else `mock`;
**`neo4j` is a stub that raises `NotImplementedError`**), `RWB_KEEP_WARM=1` (never unload the model between requests),
`RWB_AUTH=off` (disable access control: every caller becomes an `admin` principal named `unrestricted`).

## 2. Endpoints

**Auth** column: *none* means the route performs no sign-in check at all (a guest gets through; some
routes then return a guest-scoped view, which is noted); *signed in* means any authenticated role (`401`
otherwise); a role name is the minimum. With `RWB_AUTH=off` every check passes.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | liveness, backend, model, effort level, resource and security-subsystem state (§1) |
| GET | `/agents` | none | list of agents (key, class, phase, description) |
| GET | `/schema` | none | JSON schemas of `FinalResponse`, `Block`, `UserRequest` |
| POST | `/auth/login` | none | username + password (+ `code`) -> token and clearance (§2.0) |
| POST | `/auth/logout` | token | revoke the bearer token -> `{"revoked": bool}` (no error without a token) |
| GET | `/auth/whoami` | none (guest view) | the caller's role, tags, readable documents and second-factor state |
| GET | `/auth/mfa` | none (guest view) | whether this role needs an authenticator and whether one is enrolled |
| POST | `/auth/mfa/enrol` | signed in | start enrolment -> `{username, secret, uri, digits, period}`; nothing is stored until confirmed |
| POST | `/auth/mfa/confirm` | signed in | `{code}`; prove the app works; that code is then spent |
| DELETE | `/auth/mfa?username=` | signed in; admin for another account | remove an authenticator |
| GET | `/stats` | none (guest view) | counts for the caller's workspace, summed over what this role may read |
| GET | `/knowledge/tree` | none (guest view) | the knowledge layer branch by branch, with this role's reach on each |
| POST | `/security/documents/{document_id}/roles` | admin | pin who reads one branch |
| GET | `/security` | none (guest view) | the role schema, the document tags, and the caller's place in both |
| POST | `/access-requests` | signed in | ask a higher role to release what one question needs |
| GET | `/access-requests` | none (guest gets `[]`) | your own requests and their state |
| GET | `/approvals?show_all=` | signed in | your approval queue, with the material each request would release |
| POST | `/approvals/{id}/approve` | signed in, a decider for that request | approve and receive the one-time key (shown once) |
| POST | `/approvals/{id}/deny` | signed in, a decider for that request | refuse a request |
| GET | `/security-log` | manager | the security audit trail (a manager sees only their own entries) |
| POST | `/ask` | none (answers `401` when the caller is cleared for nothing) | run a request synchronously, returns `FinalResponse` |
| POST | `/runs` | none (never `401`) | start a request in the background, returns `{run_id, session_id}` |
| GET | `/runs` | **none — unauthenticated** | every recorded run on the server, any owner (without the final response and event log) |
| GET | `/runs/{run_id}` | **none — unauthenticated** | live state of one run, including the full `final` response |
| GET | `/runs/{run_id}/events` | **none — unauthenticated** | Server-Sent Events stream of progress |
| POST | `/runs/{run_id}/btw` | **none — unauthenticated** | ask the status agent about one run |
| POST | `/btw` | **none — unauthenticated** | ask the status agent about the latest run of a session |
| GET | `/sessions` | none (guest gets `[]`) | the caller's own conversations, newest first (title, turns, when, attachments) |
| GET | `/sessions/{session_id}` | none (guest-namespaced) | one conversation: turns with the full released answer and its security envelope, attachments |
| DELETE | `/sessions/{session_id}` | signed in | forget one of the caller's conversations, attachments included |
| GET | `/logs/conversations` | manager (`403`, also when not signed in) | conversations of ranks *strictly below* the caller; rows carry `owner`, `owner_role` |
| GET | `/logs/conversations/{owner}/{session_id}` | manager, owner strictly below | one such conversation in full; every read is written to the security audit |
| POST | `/upload` | signed in | attach a PDF or an image to **this conversation** (parsed, indexed, session-only) |
| DELETE | `/upload?session_id=` | none (drops the caller's own namespace; a guest drops the guest's) | forget everything attached to this conversation |
| POST | `/knowledge/documents/{id}/promote` | manager (`403`, also when not signed in) | move an attachment into the shared knowledge layer |
| GET | `/reviews` | manager | pending human-in-the-loop items |
| POST | `/reviews/{response_id}` | manager | approve / reject a flagged response |
| GET | `/audit/{session_id}?audit_id=` | signed in (see §2.8 for the non-admin defect) | run-trace records of a session |

Section 8 lists the extended routes with the same column.

### 2.0 Authentication — sign in before anything else

Every document carries a tag and a caller without a token is cleared for nothing, so `POST /ask`
answers `401`. Three roles — `user` < `manager` < `admin` — read three tags — `INTERNAL` <
`CONFIDENTIAL` < `SECRET` (a document may instead carry an explicit role allowlist, §2.6). An
unauthenticated caller is the `guest` role, which reads nothing. See **[`docs/SECURITY.md`](SECURITY.md)**
for the model, the escalation flow and how an access key is verified.

On first run three accounts are seeded — `admin` / `Admin#2026`, `manager` / `Manager#2026`, `user` /
`User#2026` — unless `RWB_ADMIN_PASSWORD`, `RWB_MANAGER_PASSWORD` or `RWB_USER_PASSWORD` supply the
password, in which case that account is not flagged `must_change`.

Request body (`LoginBody`): `username` (default `"lead"` — **Note: currently** no `lead` account exists, so a
body without `username` always fails with `401`), `password`, `label` (default `"web"`, recorded with the
token), `code` (optional authenticator code).

```http
POST /auth/login
{"username": "manager", "password": "Manager#2026", "label": "web"}

200 {"token": "y7Qd...", "username": "manager", "role": "manager", "level": 2,
     "readable_tags": ["INTERNAL", "CONFIDENTIAL"], "expires": 1789459200.0, "must_change_password": true,
     "mfa_enrolled": false, "mfa_satisfied": true, "mfa_enrolment_pending": false,
     "readable_documents": ["API560-Comparison-All-in-One-05.12.17-Rev+C", "...", "Crude desalter", "ESBWR"],
     "withheld_documents": ["CDU operating manual"]}
```

`mfa_satisfied` is `true` when the role does not require a code, or when a code was verified.
`mfa_enrolment_pending` is `true` when the role requires a code but the account has not enrolled one (such an
account is still let in without a code).

Error responses — every one is FastAPI's `{"detail": ...}`:

| code | when | `detail` |
|---|---|---|
| `401` | wrong password, unknown user, or a wrong/replayed authenticator code | a message string |
| `423` | the account is locked (five failures inside fifteen minutes lock it for fifteen) | a message string |
| `428` | the password was right, the role requires a second factor and the account is enrolled, but no `code` was sent | an object: `{mfa_required: true, username, role, digits, period, seconds_remaining, detail}` |

At `428` nothing is issued; re-post the same body with `code` filled in. With `RWB_OTP_DEMO=1` the 428 object
also carries `demo_code`, `demo_code_valid_in_seconds`, `demo_code_expires_in_seconds` (when a code is found)
and `demo_notice` — a demonstration switch, never for a deployment. Which roles need a code is
`RWB_MFA` (empty by default; `on` = manager and admin; or a comma list).

**Note: currently** the 401 messages differ: an unknown user gets "Username or password is not correct.",
a known user with a wrong password gets the same sentence plus "N attempt(s) left before lockout." — so the
response reveals whether an account exists. Wrong authenticator codes do not count toward the lockout.

`must_change_password: true` means the account still carries its seeded password — surface that in the UI.

Send the token back on every subsequent call, either as a header or (on the `AskBody` routes) in the body:

```http
Authorization: Bearer y7Qd...
```
```json
{"text": "...", "session_id": "web-user-42", "auth_token": "y7Qd..."}
```

`GET /auth/whoami` does **not** return the login shape. It returns
`{username, role, authenticated, level, readable_tags, access_control, readable_documents, withheld_documents,
documents, mfa_enrolled, mfa_enrolment_pending, mfa_required_for_role}` — no token, `expires` or
`must_change_password`. `documents` is the full catalogue, one row per loaded document:
`{document_id, title, tag, min_role, reason, assigned_by, roles, compartmented}` plus `pages` for documents
this caller can read (page counts of locked documents are omitted). Without a token it answers as `guest`.

`POST /auth/logout` revokes the token and returns `{"revoked": true|false}`; the token also expires after
eight hours (`token_ttl_seconds`), and changing an account's role invalidates every token minted under the
old one. Tokens are stored as SHA-256 digests, so a lost token cannot be recovered from the server — issue a new one.

**Second factor (TOTP).**

- `GET /auth/mfa` -> `{username, role, required_for_role, enrolled, enrolment_pending, required_roles, demo_codes}`.
- `POST /auth/mfa/enrol` (no body) -> `{username, secret, uri, digits, period}`; `uri` is the `otpauth://` QR
  payload. The secret is held in memory only until confirmed. `401` when not signed in.
- `POST /auth/mfa/confirm` `{"code": "123456"}` -> `{username, enrolled: true}`. `400` for a wrong code or when
  no enrolment was started; `401` when not signed in.
- `DELETE /auth/mfa` (optionally `?username=`) -> `{username, enrolled: false}`. Removing another account's
  authenticator needs an administrator (`403`); `401` when not signed in. **Note: currently** an unknown
  `username` is not caught and surfaces as a `500`.

Access decisions are audited: each run writes a `kind: "access"` record to its run trace
(`GET /audit/{session_id}`, §2.8), and an `access_allowed` / `access_partial` / `access_denied` event to the
security audit (`GET /security-log`). **Note: currently** a non-admin cannot read the run-trace copy — see §2.8.

### 2.1 `POST /ask` — synchronous answer

Request body (`AskBody`):

```json
{"text": "What is the normal flow rate of the crude charge pump?",
 "session_id": "web-user-42", "user_role": "engineer", "auth_token": "y7Qd...", "access_key": null,
 "options": {"want_report": false}, "attachments": []}
```

| field | default | meaning |
|---|---|---|
| `text` | required | the question |
| `session_id` | `"web"` | conversation id chosen by the client; stored namespaced as `<username>__<session_id>` |
| `user_role` | `"engineer"` | display hint only — the access policy reads the token and nothing else |
| `auth_token` | `null` | used when present; otherwise the `Authorization: Bearer` header |
| `access_key` | `null` | an approved one-time key (`RGK.<grant>.<secret>.<signature>`) for this exact question (§2.7) |
| `options` | `{}` | `want_report: true` makes the planner append a report step to any task |
| `attachments` | `[]` | `{name, path, media_type}` objects that already exist on the server (normally use `/upload`) |

`AskBody` is not the exported `UserRequest` schema: `UserRequest` defaults `session_id` to `"default"` and adds
`asked_text` (the typed wording when a follow-up was rewritten).

`401` means the caller is not cleared for any loaded document (and has no attachment in this conversation).
The body is `{"detail": "<markdown>"}`, where the markdown says why, lists every loaded document with its tag
and the lowest role that reads it, and says what to do next. A refused access key is not an error: the run
continues at the caller's own role and `security.access_key_error` says why the key did not apply.

Response: a `FinalResponse` (section 4). A lookup takes tens of milliseconds without the LLM; a
troubleshooting or procedure request with the LLM on can take 10–60 s on the 4 GB card. For anything but
lookups prefer the asynchronous flow below so the UI can show progress.

### 2.2 `POST /runs` + `GET /runs/{run_id}/events` — asynchronous with progress

1. `POST /runs` with the same body as `/ask`. Response: `{"run_id": "run-3f9a1c2b", "session_id": "web-user-42"}`
   (`session_id` is echoed raw, not namespaced). This route never answers `401`: an uncleared caller's run
   finishes with `final_status: "unauthorized"` and a `FinalResponse` whose `status` is `unauthorized`.
2. Open `GET /runs/{run_id}/events` with `EventSource` (`404` for an unknown run). The stream first **replays**
   what already happened, then streams live events, sends `: keep-alive` comment lines every 150 ms while idle,
   and ends by closing the connection after a server-built `final` event.
3. `GET /runs/{run_id}` at any time returns the `RunState` (below), including `final` once finished.

**Two `final` events, not one.** The orchestrator emits its own `final` progress event at the end of a
successful run, and it is forwarded like any other event — its `data` is only
`{"response_id": ..., "status": ...}`. After it, once the run is marked finished and the queue is empty, the
server sends a second `event: final` whose `data` is the **bare `FinalResponse` JSON** (not wrapped in a
`ProgressEvent`), or `{"status": "failed", "error": ...}` if the run has no final response. A client that
treats the first `final` as the answer and closes the stream never receives the answer. Tell them apart by
shape: the progress-event `final` (live or replayed) has an `event` key; the terminal payload does not.

```js
es.addEventListener("final", e => {
  const d = JSON.parse(e.data);
  if (d.event === "final") { showStatus(d.data?.status); return; }   // the orchestrator's notice, or a replay
  renderResponse(d); es.close();                                       // the FinalResponse
});
```

Runs that end early emit no orchestrator `final`: an access refusal (`access_denied`) and a crash (`error`)
are followed directly by the terminal payload.

**Replayed items** are rebuilt from `RunState.recent_events` and carry only
`{"event", "agent", "message", "replay": true, "ts"}` — no `phase`, `step_id`, `data`, `thinking`, `model` or
`decision`, and `message` is truncated to 160 characters. A replayed `plan_created` therefore has no steps; a
client that joins late should read the plan from `GET /runs/{run_id}` (`step_status`) instead.

Event names (the `event` field of `ProgressEvent` is a free string; these are the ones the orchestrator,
the executor and the agents emit):

| event | when | `phase` | useful `data` |
|---|---|---|---|
| `access_denied` | the caller is cleared for no loaded document and has no attachment; the run stops | `0 Access` | `role`, `required_roles` |
| `warning` | some documents are withheld from this role; an access key was refused; a retrieval gap; answer composition skipped | `0 Access`, `2 Specialist retrieval`, `5 Governance` | – (`message` says what) |
| `phase_started` | a phase begins: `0/1 Understanding`, `2 Specialist retrieval`, `3 Planning`, `4 Execution`, `5 Governance`; also `0 Access` when an access key was verified or the conversation has uploads | as named | – |
| `phase_finished` | every phase above ends | as named | 0/1: `task_type`, `entities`, `safety`; 2: `route`, `gaps`; 3: `template`, `steps`; 4: `steps`, `replans`, `llm_calls`; 5: none |
| `plan_created` | the DAG is ready | `3 Planning` | `steps: [{id, agent, goal, depends_on, mode, optional, safety_sensitive}]` |
| `agent_started` | a plan step starts (`4 Execution`), and the non-plan agents: `task_classifier`, `context_resolver`, `context_builder`, `planner`, `verification`, `answer_composer`, `governance` | the agent's phase | executor steps: `mode`, `depends_on`; `message` = goal |
| `agent_finished` | the same agents end; also `context_resolver` (follow-up rewrite, attachment survey), `model_router` (routing plan) and `access_grant` (key verified), which have no `agent_started` | the agent's phase | `ok`, `duration_ms`, `llm_calls`, `blocks`, `evidence`, `missing` (executor steps add `llm_seconds`; `model_router` adds `subtasks`, `decisions`) |
| `llm_call` | an agent calls the local model | none | `prompt`, `max_tokens`; the model is the event's `model` field; `message` = purpose |
| `replan` | a required step failed and the plan was patched | `4 Execution` | – (`step_id`, `message`) |
| `error` | the run crashed (a failed `FinalResponse` follows), or the release gate withheld the answer (a `FinalResponse` with `status: "blocked"` follows) | none / `5 Governance` | – (`message`) |
| `final` | the orchestrator's end-of-run notice (see above), then the server's terminal payload | `5 Governance` | `response_id`, `status` |

Each live SSE `data` line is JSON with the `ProgressEvent` fields: `event`, `phase`, `agent`, `step_id`,
`message`, `data`, `thinking` (human-readable reasoning for the step), `model` (model name when one was
used), `decision` (the outcome label), `ts` (unix seconds). Example frame:

```
event: agent_finished
data: {"event":"agent_finished","phase":"4 Execution","agent":"lookup","step_id":"lookup",
       "message":"5 documented value(s) for Crude Charge Pump (11-P-01)",
       "data":{"ok":true,"duration_ms":31,"llm_calls":0,"blocks":1,"evidence":5,"missing":[],"llm_seconds":0.0},
       "thinking":"...","model":null,"decision":"5 documented value(s) for Crude Charge Pump (11-P-01)","ts":1789700000.12}
```

Minimal client:

```js
const {run_id} = await (await fetch("/runs", {method:"POST",
                         headers:{"Content-Type":"application/json", "Authorization":`Bearer ${token}`},
                         body: JSON.stringify({text, session_id})})).json();
const es = new EventSource(`/runs/${run_id}/events`);
es.addEventListener("plan_created", e => { const d = JSON.parse(e.data); if (d.data) renderPlan(d.data.steps); });
es.addEventListener("agent_finished", e => tickStep(JSON.parse(e.data)));
es.addEventListener("final", e => {
  const d = JSON.parse(e.data);
  if (d.event === "final") return;          // not the answer yet
  renderResponse(d); es.close();
});
```

`RunState` (`GET /runs/{id}`) fields: `run_id`, `session_id` (raw, as the client sent it), `request_text`,
`started`, `finished`, `phase`, `task_type`, `safety_status`, `principal` (`"<name> (<role>)"` or
`"unauthenticated"`), `followup` (`new`/`substitution`/`elaboration`/`continuation`), `entities`, `goal`,
`progress` (only during an upload ingest, §2.4), `step_status` (list of `{step_id, agent, goal, depends_on,
status, summary}`), `current_step`, `recent_events` (last 60, each `{t, event, agent, message}` with `t` in
seconds since `started`), `llm_calls`, `llm_seconds`, `safety_flags` (a count), `resources`, `final_status`,
`error`, `final` (a `FinalResponse` or `null`). `GET /runs` returns the same objects without `final` and
`recent_events`. The registry keeps the most recent 50 runs in memory; it is not persisted.

**Note: currently** none of the `/runs` read routes, the SSE stream or the btw routes check a token: anyone
who can reach the server can list every run (question text, owner, entities) and read any run's full answer.

### 2.3 "btw" side channel — `POST /runs/{run_id}/btw` and `POST /btw`

While a run is going (or after it finished) the user can ask about it without disturbing it. Body (`BtwBody`):
`{"text": "what is going on?", "session_id": "web-user-42"}` — `text` defaults to `"what is going on?"`,
`session_id` to `null`. `/btw` picks the latest run whose raw `session_id` matches; with no `session_id` it
picks the latest run on the server, whoever owns it. The `/runs/{id}/btw` form targets one run. Neither checks
a token (see the note above). The status agent (`workbench/orchestration/status_agent.py`) uses no LLM; it
answers from the run state in milliseconds. Response:

```json
{"run_id":"run-3f9a1c2b","blocks":[ {"type":"text","id":"status","markdown":"**Run run-3f9a1c2b** — *The crude charge pump ...*  \nPhase: **4 Execution** · running for 6.2s · now: **diagnostic** — Extract possible causes  \nSteps: 3/9 complete"},
  {"type":"plan","id":"plan","title":"Plan progress","goal":"...","tasks":[...]},
  {"type":"table","id":"events","title":"Recent events","columns":["t","Event","Agent","Message"],"rows":[...]} ]}
```

With no matching run the reply is one `callout` block, "No run is active or recorded for this session.", and
`run_id: null`. The wording of the question changes the extra block: "what have you found so far" → a table of
step summaries; "why is it slow / how long" → timing and LLM-call figures; "plan" → the plan only; "safety" →
safety status and flag count; "entities" → resolved equipment; "resources / gpu / model" → what is loaded.
Render these blocks exactly like the blocks of a normal answer.

### 2.4 `POST /upload` — a document or image that belongs to one conversation

`multipart/form-data` with fields `file`, `session_id` (default `"web"`), optional `note`. Signed in only
(`401` otherwise). The file is saved under `data/workbench/uploads/<username>/<session_id>/` and recorded
against the namespaced conversation `<username>__<session_id>`.

- **PDF**: indexed in a background thread through the knowledge layer's own parser (Docling), normalizer,
  chunker and claim extraction (`workbench/services/ingest.py`), **for the conversation that uploaded it only**.
  It is not added to the shared corpus, not classified, and nothing is written into `data/knowledge`; the parse
  is cached under `data/workbench/uploads/_cache/<hash>/` so the same file is not parsed twice. Response:

  ```jsonc
  { "status": "indexing", "kind": "pdf", "run_id": "run-ab12", "document_id": "GNH-eng",
    "detail": "poll /runs/{run_id}; the document joins this conversation when final_status is 'indexed'" }
  ```

  `document_id` here is the file's stem; the id the index finally uses is the one listed in
  `GET /sessions/{id}` → `uploaded_documents` (with its `stats`) and in `attached_documents`.
  Uploaded documents get authority rank 40 (knowledge-layer documents get 60), so their values rank
  below the manuals in conflict resolution.
- **Image** (`.png .jpg .jpeg .webp .bmp .gif .tif .tiff`): processed synchronously by the intake pipeline —
  on-device OCR, then at most one vision-model call (loaded on demand and freed straight after). The OCR text and
  the vision description are stored as session notes; the image is not added to any knowledge index. Response:

  ```jsonc
  { "status": "described", "kind": "image", "description": "...",
    "intake": { "kind": "...", "flagged": [ /* low-confidence OCR lines, at most 50 */ ], "ocr_lines": 14,
                "ocr_mean_confidence": 0.81, "vision_calls": 1, "chunks": 3, "duration_ms": 5200 },
    "draft": { /* a review draft of the figures found, or null */ } }
  ```

  If the intake pipeline fails the response falls back to `{status, kind, description, intake_error}`
  with a plain vision description.
- Other types: `{"status":"unsupported","detail":"..."}`.

Who can read an attachment: the person who uploaded it, in that conversation, whatever their role — it is
their own file. Nobody else, in any other conversation, by any phrasing. The index lives in process memory;
after a restart it is rebuilt from the cached parse the first time the conversation is touched again.

Parsing is slow the first time a file is seen — minutes for a large document — so poll `GET /runs/{run_id}`
until `finished` is set: `final_status` is `indexed` or `failed`, `phase` walks through `ingest:parsing` →
`ingest:normalizing` → `ingest:indexing`, and `progress` carries real counts from the parser:

```jsonc
{ "done": 12, "total": 32, "unit": "pages", "percent": 31 }
```

Those are pages the parser has actually finished, reported as each window lands — not an estimate.
Reading the pages is weighted at 85% of `percent` because that is where the wall clock goes; the
stages after it carry the remainder. Pages recovered from a checkpoint count as done.

**Note: currently** an ingest run never sets `RunState.final`, so the SSE stream for it ends with
`{"status": "failed", "error": null}` even when indexing succeeded. Poll `GET /runs/{run_id}` for uploads;
do not use the event stream.

**Do not let a question be asked before `final_status` is `indexed`.** It will be answered from
every document *except* the one being read, so it comes back as a confident answer about the wrong
thing — which is indistinguishable from the feature being broken.

An answer drawn from an attachment names it in `security.attached_documents`, and the attachment
contributes no classification: nobody has classified it, so it does not stamp the answer with a
tier it does not belong to. A question that points at the attachment ("this document", "the attached pdf")
is scoped to the attachment alone.

`DELETE /upload?session_id=` forgets them -> `{"dropped": <count>}`. It performs no sign-in check; it drops
the uploads of the caller's own namespace (a guest's call touches only `guest__<id>`).

`POST /knowledge/documents/{id}/promote` with `{"session_id": "web", "tag": null}` is the deliberate opposite:
it re-parses into the knowledge layer's own directories, adds the document to the shared corpus and
classifies it (`CONFIDENTIAL` unless `tag` — `INTERNAL`, `CONFIDENTIAL` or `SECRET` — is given), then drops the
private copy. Response: `{document_id, tag, roles, min_role, stats}`. That needs **manager or above** (`403`
otherwise, including when not signed in), because the person doing it is deciding what everyone else will be
able to read. `404` when the document was not uploaded into that conversation or its file is gone; `400` for
a bad tag.

### 2.5 Reviews (human in the loop)

Responses with `requires_human_review: true` are recorded in `data/workbench/audit/hitl.jsonl`. Both routes need
**manager or above** (`401` / `403`).

`GET /reviews` returns pending items: `{ts, response_id, session_id, audit_id, reason, request, status:
"pending", task_type}` (`session_id` is the raw id the client sent). `POST /reviews/{response_id}` with
`{"decision":"approved"|"rejected","reviewer":"name","note":""}` records the decision and returns
`{ts, response_id, status, reviewer, note}`; any other `decision` is `400`. **Note: currently** `reviewer`
defaults to the literal `"reviewer"`, so the signed-in manager's name is recorded only when the client sends
an empty string; the `response_id` is not checked to exist; and the decision is written to `hitl.jsonl` only,
not to the security audit. Nothing is blocked automatically: the answer was already delivered with the flag;
the review is a record.

### 2.6 The knowledge layer and who reads it

`GET /knowledge/tree` returns one **branch** per document — the unit access is granted in — with
the caller's reach marked on each. No sign-in check: a guest sees every branch locked.

```jsonc
{
  "role": "user", "principal": "user", "access_control": true, "vault": true,
  "readable": 4, "locked": 2,
  "branches": [
    { "document_id": "API610 pump operation manual", "title": "SECTION ONE - PRODUCT DESCRIPTION",
      "tag": "INTERNAL", "reason": "published standard or vendor reference material",
      "roles": ["user", "manager", "admin"], "compartmented": false, "readable": true, "assigned_by": "setup",
      "pages": 82, "counts": { "entities": 8, "claims": 0, "procedures": 2, "chunks": 72 },
      "chapters": [ { "number": 1, "title": "Product description" } ],
      "sealed": false, "in_memory": false },

    // a locked branch reports its name, its tag and who to ask — and nothing else. No chapters,
    // no page total, no counts: that would measure the tier above the caller.
    { "document_id": "CDU operating manual", "title": "Operating Manual", "tag": "SECRET",
      "reason": "...", "roles": ["admin"], "compartmented": false, "readable": false, "assigned_by": "setup",
      "ask": "admin", "sealed": false, "in_memory": false }
  ]
}
```

`counts` holds whatever per-document statistics the backend reports (at most 60 `chapters` are listed).
`sealed` and `in_memory` appear only when the vault service is present; a branch sealed in the vault but not
loaded as a document is added with `sealed: true`, `readable: false` and `sealed_bytes`. Readable branches sort
first.

`POST /security/documents/{document_id}/roles` with `{"roles": ["manager"]}` pins an explicit
reader allowlist — a **compartment**, read by exactly those roles however senior anyone else is.
`{"roles": null}` hands the document back to the tag ladder. Administrators only (`403`
otherwise, including when not signed in; `404` for a document that is not loaded). Response:
`{document_id, tag, roles, compartmented, min_role}`. The tag is never changed by this call: it
still says how sensitive the material is, and the banners, refusals and audit trail all read it.

`GET /security` (no sign-in check) returns `{enabled, schema, documents, you, escalation}`: the role schema
(`roles[]` with level, description, readable tags and who each escalates to; `tags[]` with level and
readers; `rule`; `fallback_tag`), the same `documents` catalogue as `whoami`, the caller's
`{principal, role, authenticated, level, readable_tags, readable_documents, withheld_documents}`, and
`{enabled, your_approver, key_lifetime_minutes, uses_per_key}`.

`GET /stats` (no sign-in check) returns `{principal, role, documents: {readable, withheld, total, by_tag, pages},
knowledge: {entities, claims, relations, procedures, chunks}, activity: {runs_total, runs_active, runs_mine},
escalation: {my_open_requests, my_requests, awaiting_my_approval}, answers: {effort, llm, llm_available,
composed}}`, counted over what the role may read. **Note: currently** `runs_mine` is always `0`, because runs
carry the raw session id and the count looks for a `<username>__` prefix; `runs_total` counts every run on the server.

### 2.7 Access requests and approvals

- `POST /access-requests` takes an `AskBody` (only `text`, `session_id` and the token are used) and raises a
  request for the records above the caller's role that bear on the question. Response:
  `{request, approver_role, summary}`, where `request` is `{request_id ("AR-…"), requester, requester_role,
  question, question_fp, session_id, scope, approver_role, status, created, expires, decided_by, decided_at,
  note, grant_id}`. `400` when not signed in or when nothing is withheld from the caller. Requests expire after
  24 hours undecided. A signed-in caller's run also raises one automatically when restricted records bear on the
  question (`auto_raise_requests`), and puts its id in `security.access_request_id`.
- `GET /access-requests` -> the caller's own requests, newest first (`[]` for a guest).
- `GET /approvals?show_all=false` -> requests whose scope the caller's role can decide, each with a `preview`:
  up to 12 records `{id, kind, document, page, text}` of the actual material, so the approver reads it before
  deciding. `403` when not signed in. **Note: currently** `show_all=true` returns every request in every state
  to any signed-in caller, whatever their role; the preview is still gated, but by the lexically greatest tag
  name in the scope rather than the most restrictive, and it ignores compartment allowlists.
- `POST /approvals/{id}/approve` with optional `{"note": ""}` -> `{request, grant, key, expires_in_minutes,
  requester}`. `key` (`RGK.<grant>.<secret>.<signature>`) is shown only here. The key is bound to the
  requester and to the exact question, lasts 30 minutes and opens only the named records; it is spent when an
  answer (`answered` or `needs_review`) is released under it.
- `POST /approvals/{id}/deny` with optional `{"note": ""}` -> the updated request.

Approve and deny answer `403` for every refusal — not signed in, unknown request id, already decided,
expired, deciding one's own request, or a role that is not a decider for the scope.

`GET /security-log?event=&principal=&limit=100` (manager or above) returns the hash-chained security audit.
A manager's `principal` filter is forced to their own username; an administrator may filter freely.

### 2.8 Audit

`GET /audit/{session_id}` (optionally `?audit_id=`) returns the hash-chained JSONL records written during
runs, each `{seq, ts, prev_hash, hash, audit_id, kind, …}` with `kind` one of `request`, `access`,
`structured_request`, `plan`, `replan`, `step_result`, `final`, `error`. The `audit_trail_id` in every
`FinalResponse` selects one run. The `final` record carries `models_used`. `401` when not signed in.

**Note: currently** the run trace is written under the **raw** session id the client sent (`web`), while the
route only returns records to a non-admin when the requested id starts with `<username>__`. For a non-admin
the route therefore returns `[]` in practice; only an administrator can read run traces. Because the file is
keyed by the raw id, every caller who uses the same `session_id` writes into the same trace file.

### 2.9 Conversations — kept on the server, listed per person

Every exchange is written to the session file as it lands, with the **full released answer** and
the **security envelope it was released with**, so a conversation can be redrawn later exactly as
it was — banner included — without recomputing anything against what the caller may read *now*.

`GET /sessions` lists the caller's own conversations (owner is read from inside each record, not
from the filename), newest first: `{session_id, owner, owner_role, title, turns, created, updated,
attachments}` with a title taken from the first question. A guest gets `[]`. `GET /sessions/{id}`
returns the stored state (turns, `uploaded_documents`, `notes`, `pending_clarification`, …) plus
`attached_documents`; touching a conversation also brings its attachments back into memory if the
process that indexed them has since restarted (`attached_documents` says which are live). A guessed id
returns the caller's own (empty) conversation, never someone else's. `DELETE /sessions/{id}` ->
`{"deleted": bool}` (`401` when not signed in). The client keeps only *which* conversation a tab has open
(`?c=<id>`, mirrored to `sessionStorage`).

## 3. Sessions and follow-ups

- `session_id` is chosen by the client (any string). Memory is kept in
  `data/workbench/sessions/<owner>__<session_id>.json` (last 20 turns): what was typed, what it was read as,
  task type, resolved entities, parameter, scenario, status, any tag corrections, and the composed answer.
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
 "answer_markdown":"...the released answer...",
 "blocks":[...],"evidence":[...],
 "confidence":{"score":0.69,"basis":"agents 0.84 × verification 1.00","uncertainties":[]},
 "safety_flags":[{"severity":"warning","message":"...","evidence":[...],"requires_authorization":false,"source_agent":"safety"}],
 "requires_human_review":true,"review_reason":"The given value lies outside the documented design envelope.",
 "plan":{"plan_id":"plan-...","goal":"...","rationale":"...","template":"limits","iteration":0,"llm_refined":false,
         "steps":[{"step_id":"claims","agent":"lookup","goal":"...","inputs":{},"depends_on":[],
                   "safety_sensitive":false,"optional":false,"status":"done","note":"..."}]},
 "audit_trail_id":"20260915-041702-a1b2c3","entities":["Crude Charge Pump (11-P-01)"],
 "timing_ms":58,"llm_calls":0,"backend":"files","warnings":[],
 "security":{"access_control":true,"principal":"manager","role":"manager","authenticated":true,
             "classification":"CONFIDENTIAL","source_documents":["Crude desalter"],"readable_documents":["..."],
             "withheld_documents":["CDU operating manual"],"withheld_summary":"","withheld_records":0,
             "escalation_target":"admin","access_request_id":null,"attached_documents":[],"grant_id":null,
             "access_key_error":null,"released_records":0,"release_blocked":false}}
```

| field | meaning |
|---|---|
| `status` | `answered`; `clarification` (ask the user, see the `clarification` block); `restricted` (request to bypass a protection; no instructions given); `unauthorized` (the caller is not cleared for any loaded document; returned as `401` by `/ask`); `needs_review` (answered but confidence below 0.35 — show as a lead, not an answer); `blocked` (the release gate traced part of the answer to material the caller may not read, so the whole answer was withheld; `security.release_blocked` is `true`); `failed`. The schema types this as a free string |
| `session_id` | the raw id the client sent |
| `task_type` / `secondary_task_types` | one of `lookup, multi_hop, procedure, troubleshooting, limits, explanation, safety, comparison, conflict, provenance, planning, report, cross_document, inventory, ambiguous` |
| `answer_markdown` | **the released answer**: the composed prose, the one or two blocks prose cannot carry (ordered steps, a limit gauge, a comparison, a table), the first documented DANGER/WARNING, and a source line; the orchestrator then appends the classification line and, when material was withheld, the escalation note. This is what a chat-style UI shows; `blocks` is for a panelled one. `RWB_ANSWER_STYLE=full`, or a run the composer did not write, makes it the block rendering instead |
| `blocks` | ordered render blocks (section 5). On a composed answer `blocks[0]` is a `text` block with `id: "answer"` |
| `evidence` | the labelled evidence list: `{document_id, text, page, chunk_id, claim_id, section_path, source, revision, grounding, ref}`; `ref` is the `[n]` label used by blocks; `grounding` is `1.0` for text verbatim from the knowledge layer, below 1 when paraphrased or derived |
| `confidence` | `score` 0–1, `basis` (formula in words), `uncertainties`; level is `high ≥ 0.75`, `medium ≥ 0.45`, else `low` |
| `safety_flags` | de-duplicated flags from all agents (`severity` info/caution/warning/danger — a free string in the schema) |
| `requires_human_review` / `review_reason` | set by the governance agent (restricted request, value outside design, emergency/isolation/changeover/shutdown procedure, DANGER flag, any planning answer) |
| `plan` | the executed DAG: `plan_id, goal, rationale, template, iteration` (replan count), `llm_refined`, and `steps[]` of `{step_id, agent, goal, inputs, depends_on, safety_sensitive, optional, status (pending/running/done/failed/skipped), note}` |
| `warnings` | context gaps, "not documented: ..." items, release-check notes, withheld-record counts |
| `security` | the `SecurityEnvelope`: who the answer was released to (`principal`, `role`, `authenticated`), `classification` (highest tag among the documents the answer used; `null` if none), `source_documents`, `readable_documents`, `withheld_documents`, `withheld_summary`, `withheld_records`, `escalation_target` (lowest role that can release what was withheld), `access_request_id` (auto-raised request), `attached_documents`, `grant_id` and `released_records` (when an access key was honoured), `access_key_error` (why a key was refused), `release_blocked`, `access_control` |

## 5. Block types (`workbench/core/blocks.py`)

Every block has `type`, `id`, optional `title`, and `citations: ["[1]", "[3]"]`. Render a block's `citations`
as small superscript links to the evidence list; some blocks also carry per-item `citation` fields or
`row_citations`. All labels are `"[n]"` strings; `n` indexes `FinalResponse.evidence` in order (`evidence[n-1].ref == "[n]"`).

| type | fields | how to render |
|---|---|---|
| `text` | `markdown` | Markdown paragraph(s); the composed answer (`id: "answer"`) and the report agent's `## Heading` sections use it |
| `callout` | `level` (info/success/warning/danger), `markdown` | coloured banner; on a non-composed answer the first block is usually the one-line lead callout |
| `kpi` | `items[]`: `label, value, unit, qualifier, citation` | row of value tiles (e.g. "volumetric flow rate — 482 m3/h (normal)") |
| `table` | `columns[]`, `rows[][]` (string/number/null), `row_citations[][]`, `caption` | data table; put the row's citations at the end of the row |
| `steps` | `procedure_id, procedure_type, document_id, section_path, page_start, page_end, prerequisites[]`, `steps[]` of `{sequence, text, page, citation, is_prerequisite, warnings[], mentions[]}` | ordered list; show `prerequisites` as a checklist above; render `warnings` as an inline badge on the step; `mentions` are equipment tags you can make clickable |
| `graph` | `nodes[]` `{id, label, type, is_focus, properties}`, `edges[]` `{source, target, label, citation, inferred}`, `layout` (left-right/top-down/radial), `mermaid` | draw with a graph library from nodes/edges (highlight `is_focus`, dash `inferred` edges) or simply render the `mermaid` string |
| `plan` | `goal`, `tasks[]` `{id, title, agent, depends_on[], status, summary, safety_sensitive}`, `mermaid` | DAG / checklist with status badges; the executed plan is titled "How this answer was produced"; a work plan from the planner is titled "Proposed work plan" |
| `evidence` | `items[]` `{ref, document_id, page, chunk_id, claim_id, section_path, text, source, revision}` | numbered list at the end; anchor targets for the `[n]` labels; `source` is table/procedure/narrative/rule/...; (`grounding` is on `FinalResponse.evidence`, not on these items) |
| `comparison` | `subjects[]`, `attributes[]`, `cells[][]` (rows = attributes, columns = subjects) of `{value, unit, citation, note}`, `differences[]` | matrix with a highlighted "differences" list; `value: null` means not documented |
| `limit_gauge` | `entity, parameter, unit, value` (may be null), `markers[]` `{label, value, citation}` (minimum/normal/maximum/design/trip...), `verdict` (within_normal/within_design/outside_design/unknown), `message` | a horizontal gauge with the markers and a needle at `value`; colour by verdict |
| `conflict` | `subject, parameter, claims[]` `{value, unit, document_id, revision, page, source, context, citation, temporal_status}`, `status` (corroborated/different_context/potential_conflict/resolved/unresolved), `preferred_index`, `resolution` | card listing each value with its source; mark `claims[preferred_index]` as preferred; show `resolution` text |
| `safety` | `flags[]` `{severity, message, citation, requires_authorization}` | list with severity colours; `requires_authorization` shows a lock icon |
| `confidence` | `score, level, basis, uncertainties[]` | meter plus a tooltip with `basis`; list `uncertainties` |
| `clarification` | `question, missing[], options[]` | prompt with clickable `options` (each option can be sent back as the next request text) |
| `image` | `url, caption, page` | image (not produced by the agents yet; reserved for figure crops) |
| `audit` | `audit_id, phases[]` `{name, agent, status, duration_ms, note}`, `llm_calls, backend`, `models_used[]`, `routing[]` `{kind, purpose, model, reason}` (at most 40), `chained_audit_hash` | collapsible footer; `models_used` names every model that handled part of the run; `chained_audit_hash` is the head hash of the session's run-trace chain after this run was written |

Ordering rule used by the governance agent (the `audit` block is always last, and `evidence` just before it
when there is any):

- **Composed answer** (the usual case for an answered question): the composer's `text` block `answer` →
  the report's blocks (for a report) or every agent's blocks in plan order (clarification blocks excluded,
  safety blocks where their agent sits in the plan) → a `safety-summary` block if there are flags and no
  safety block yet → verification blocks → executed plan (when the plan had more than 3 steps and the status
  is `answered`) → human-review callout → confidence → evidence → audit. There is no lead callout.
- **Not composed** (composition skipped or failed): lead callout → primary blocks in plan order (or the
  report's own order for reports; for a `safety` task the safety blocks come first) → safety blocks →
  `safety-summary` if needed → verification blocks → executed plan (more than 3 steps, `answered`) →
  human-review callout → confidence → evidence → audit.
- A clarification or restricted run: (a restricted lead callout) → the clarification or safety blocks →
  the same tail. A run that ends `unauthorized` carries three blocks (lead callout, document listing, what
  to do next); one that ends `blocked` carries a single "Answer withheld" callout.

Duplicate blocks (same type and content) are dropped. When restricted material bears on the question and an
escalation is offered, an `escalation` callout is appended after the audit block. **Note: currently** that
leaves `audit` no longer last, so on those responses the audit block's `chained_audit_hash` is not filled in
and the run trace's `final` record has an empty `models_used`.

## 6. Schemas

`python -m workbench schema` writes `docs/schema/final_response.schema.json`, `block.schema.json`,
`user_request.schema.json` and `progress_event.schema.json` from the pydantic models (pydantic's JSON Schema
output, draft 2020-12 dialect; the files carry no `$schema` key). `GET /schema` serves the first three live.
Generate TypeScript types from them (for example with `json-schema-to-typescript`) rather than hand-writing
interfaces. Caveats:

- `FinalResponse.status`, `SafetyFlag.severity` and `ProgressEvent.event` are plain strings in the schema, with
  no enum; use the value lists in this document.
- `user_request.schema.json` is the internal `UserRequest`, not the HTTP body: `/ask` and `/runs` take
  `AskBody` (§2.1), which is not exported (it defaults `session_id` to `"web"`, `UserRequest` to `"default"`).
- `progress_event.schema.json` has no `replay` field; replayed SSE items add it (§2.2) and omit most others.

## 7. Notes for the UI

- Show progress from the SSE stream; a run with the LLM on can take a minute on a 4 GB GPU, while lookups
  return in well under a second. Wait for the `final` that has no `event` key (§2.2) before closing.
- Always render `safety` blocks and the human-review callout prominently; never hide them behind a toggle.
- `status: "restricted"` answers contain no operating instructions by design; show them as they are.
  `status: "blocked"` answers carry nothing from the withheld material; show the callout as it is.
- In the default `brief` style `answer_markdown` is the answer to show in a chat view; the blocks carry more
  structure (citations per row, gauge markers, DAG edges) for a panelled view.
- Colour a classification banner from `security.classification` and offer "track this request" from
  `security.access_request_id` rather than parsing the prose.

## 8. Extended surface (September 2026) — `workbench/app/api_ext.py`

Same bearer token, same access rules. In the Role column *signed in* means any authenticated role (`401`
otherwise), a role name is the minimum (`401` / `403`), and **none** means the route performs no sign-in check.

Not every write reaches the hash-chained security audit (`GET /security-log`). These do:
`/models/register`, `/models/packages/verify`, `/models/packages/import`, `/vault/seal`, `/vault/rotate/{role}`,
`/vault/revoke`, `/sandbox/run`, `/intake`, `POST /deliverables`, `/drafts/{id}/signoff` (including a blocked
sign-off) and `/drafts/{id}/reject`. These do not: `/tools/run` and `/tools/agent` (recorded in the chained
tool-call log instead), `/drafts/{id}/figures/{fid}/resolve` (recorded in the draft registry's own chained
log), `/sovereignty/verify`. On the core surface the security audit records sign-in, logout, MFA changes,
every run's access decision, access-request raise/approve/deny, key redemption/rejection, release blocks,
grant use, allowlist changes, promotions and supervised conversation reads — but not review decisions,
uploads, or session and upload deletions.

### Models and routing

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/models` | none | registered models with capability profiles, installed / fits-VRAM flags, resident model, best model per task kind |
| GET | `/models/routing?limit=&run_id=` | none | the chained routing log: chosen model, candidates, reason per call; `{entries, total, chain}` |
| POST | `/models/route` `{text, budget?}` | none | decompose a request into sub-tasks and show which model would take each (no model call); `503` when routing is off |
| POST | `/models/register` `{name, family?, size_gb?, context?, modalities?, min_vram_mb?, thinking?, capabilities, notes?}` | admin | plug a model in: written to `data/workbench/models/registry.local.yaml`, routable at once |
| GET | `/models/packages` | manager | trusted signers and the chained log of package verifications / imports |
| POST | `/models/packages/verify` `{path}` | manager | verify a signed package (checksums, signature, trusted signer) |
| POST | `/models/packages/import` `{path, dry_run}` | admin | verify then `ollama create` from local files; refused on any failure; `dry_run` defaults to `true` |

### Sovereignty

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/sovereignty?deep=false` | **none** | egress-guard state, live network-monitor status (external connections by host vs by workbench, interface state, `physically_disconnected`), chain verification of every log, TLS state; `deep=true` also walks every run audit |
| GET | `/sovereignty/connections?limit=&event=&external_only=` | **none** | the chained connection log |
| POST | `/sovereignty/verify` | manager | full verification of every chained log; `{chains, intact, checked}` |
| GET | `/sovereignty/egress?limit=` | **none** | blocked in-process egress attempts and the guard's allow-list |

### Vault

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/vault` | **none** | sealed branches, role key versions, branches decrypted in memory, session keyrings, `you.can_unwrap`, the last 30 key events. **Note: currently** no sign-in check: a guest receives the whole status, with `you` describing the guest |
| POST | `/vault/seal` `{shred}` | admin | seal every branch index for exactly the roles that may read it; optionally shred the plaintext cache |
| POST | `/vault/rotate/{role}` | admin | rotate a role's wrapping key (re-wrap content keys, wipe keyrings; no data re-encrypted) |
| POST | `/vault/revoke` `{branch, role}` | admin | delete one role's wrapped copy of a branch key |
| GET | `/vault/tls` | **none** | local CA / server / client certificate fingerprints, whether TLS / mTLS is active |

### Tools and sandbox

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/tools` | **none** | the named tools with argument schemas, and the sandbox limits |
| POST | `/tools/run` `{tool, args, session_id}` | signed in | run one tool in the caller's session workspace over the knowledge the caller may read |
| POST | `/tools/agent` `{goal, session_id, max_iterations}` | signed in | the agent loop (default 8 iterations): plan, call tools, iterate; every call chained |
| GET | `/tools/workspace?session_id=` | signed in | files in the session workspace |
| GET | `/tools/workspace/file?session_id=&path=` | signed in | download one workspace file (confined) |
| GET | `/tools/calls?limit=` | signed in | the chained tool-call log (own calls; everything for admin) |
| POST | `/sandbox/run` `{code, tests?, inputs?, task_id?}` | signed in | run code in the sandbox; `verified`, `limit_hit`, `egress_attempts`, `files_written`, `workdir_destroyed` |
| GET | `/sandbox/runs?limit=` | signed in | the chained sandbox run log (**Note: currently** not filtered by owner: every account's runs are returned) |
| GET | `/sandbox/manifest` | **none** | stdlib allow-list, banned imports, checksummed vendored files, manifest hash |

### Intake

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/intake` (multipart: `file`, `session_id`=`web`, `purpose`=`general`, `run_vision`=`true`, `max_pages`=`6`, `create_draft`=`true`) | signed in | on-device OCR + vision; flagged low-confidence lines; a review draft of the numbers found (`draft`). Saved under `data/workbench/uploads/<username>/<session_id>/intake/` |

### Deliverables and review

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/deliverables` `{response_id, format: docx\|pptx\|xlsx\|md, title?}` | signed in (answer's owner or admin) | export a released answer; registers a draft `pending_signoff`; returns the file details, `draft` and `download` path |
| GET | `/deliverables/responses?limit=` | signed in | released answers that can be exported (own; all for admin) |
| GET | `/deliverables/{draft_id}/download` | owner / manager+ | the file |
| GET | `/drafts?status=` | signed in | drafts with open flags and pending state (own; all for manager+), summary, chain verification |
| GET | `/drafts/{draft_id}` | owner / manager+ | one draft with every provenance-linked figure |
| POST | `/drafts/{draft_id}/figures/{figure_id}/resolve` `{action: accepted\|corrected\|removed, corrected_value?, note}` | owner / manager+ | resolve one flag |
| POST | `/drafts/{draft_id}/signoff` `{note}` | manager | sign off; **409** with `{detail, open_flags}` while any flag is unresolved |
| POST | `/drafts/{draft_id}/reject` `{note}` | manager | reject |

`GET /health` reports `routing`, `resident_model`, `airgap_enforced`, `network_monitor`,
`workbench_external_connections`, `vault`, `sandbox`, `tools`, `tls`, `mtls` (§1). The `audit` block on every
answer carries `models_used`, `routing` rows and `chained_audit_hash` (§5).
