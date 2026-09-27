# MRPL AI Workstation — web front end

The browser client for the Refinery Engineering AI Workbench. It talks to the workbench's FastAPI
service and to nothing else: no database, no server of its own, no telemetry. That service is one
app built from two files: `workbench/app/api.py` (auth, runs and SSE, sessions, uploads, access
requests, knowledge tree, security, reviews) and `workbench/app/api_ext.py`, whose routers `api.py`
mounts (`/models`, `/sovereignty`, `/vault`, `/tools`, `/sandbox`, `/intake`, `/deliverables`,
`/drafts`). Roughly half of what the pages call lives in `api_ext.py`.

Naming note: this package calls itself "MRPL AI Workstation" (this title, `package.json` name
`mrpl-ai-workstation`); the rest of the repository calls the system the "Refinery Engineering AI
Workbench". Both names refer to the same system.

```bash
npm install
npm run dev          # http://127.0.0.1:5173, proxying /api -> http://127.0.0.1:8077
```

The backend has to be running on port 8077 (`serve` defaults to 8000, which the proxy does not use):

```bash
# from the repository root
.venv/Scripts/python -m workbench serve --port 8077
# or: .venv/Scripts/python -m uvicorn workbench.app.api:app --host 127.0.0.1 --port 8077
```

Where requests go:

- **Default (no `VITE_WORKBENCH_URL`)**: the client calls same-origin `/api/...`
  (`src/lib/api.ts`), and the Vite dev server proxies `/api` to `http://127.0.0.1:8077`, stripping
  the `/api` prefix (`vite.config.ts`).
- **`VITE_WORKBENCH_URL` set** (in `.env` or in the shell) is compiled into the bundle as
  `import.meta.env.VITE_WORKBENCH_URL`, and the browser then calls that URL **directly**, bypassing
  the proxy (cross-origin; the API currently allows any origin). It is not a proxy setting:
  `vite.config.ts` reads the proxy target from `process.env.VITE_WORKBENCH_URL` without calling
  `loadEnv`, so a value in `.env` never reaches the proxy, and a shell value makes the client skip
  the proxy anyway.
- **Production**: `npm run build` emits a static bundle in `dist/`. With `VITE_WORKBENCH_URL` unset
  it calls `/api/...` on its own origin, so whatever serves `dist/` must reverse-proxy `/api/*` to the
  workbench **and strip the `/api` prefix** (the API's routes have none). `npm run preview` has no
  such proxy configured.

| script | what it does |
| --- | --- |
| `npm run dev` | Vite dev server with HMR |
| `npm run build` | typecheck, then a production bundle in `dist/` |
| `npm run preview` | serve the built bundle |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run lint` | `eslint .` — **currently fails**: `eslint.config.js` imports `@eslint/js`, `globals`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`, `typescript-eslint` and `eslint-config-prettier`, and none of them (nor `eslint` itself) is in `devDependencies` |

---

## What the screens are for

| route | who sees it | what it is |
| --- | --- | --- |
| (any URL) | signed out | Sign-in is not a route: while signed out every URL renders the SignIn screen (password, then an authenticator-code step when the server asks for one), with an explanation of the access model before you need it |
| `/` | everyone | Redirects to `/chat` (so does any unknown path) |
| `/chat` | everyone | Ask a question. Thinking, answer blocks, `btw`, effort, access keys; attach a PDF or an image; export an answer as Word / Excel / PowerPoint |
| `/overview` | everyone | Your workspace: what you can read, what is running, what is waiting on you |
| `/documents` | everyone | Every document, its classification, and who reads it |
| `/knowledge` | everyone | The knowledge layer branch by branch, with your reach marked on each one |
| `/access` | everyone | Requests you raised, and — for approvers — the queue waiting on you |
| `/tools` | everyone | Named local tools (files, sandboxed code, spreadsheets, document search, calculation, OCR, vision, Word/Excel/PowerPoint), the agent loop over them, the sandbox, your workspace and the chained tool log |
| `/review` | everyone | Deliverable drafts pending human sign-off (`/drafts`): every figure with its provenance, flagged items to resolve, sign-off blocked until they are; export recent answers as Word / Excel / PowerPoint. This is **not** the HITL review queue behind `GET /reviews`; no page shows that queue yet |
| `/models` | everyone | The model capability registry, what is installed, which model wins each task kind, the routing log, "route a request"; managers and admins verify signed model packages; admins also register models and run a dry-run package import |
| `/sovereignty` | everyone | Live network monitor, egress guard, interface state (pull the cable and watch it), tamper-evident log chains, TLS state |
| `/logs` | manager, admin | Recent conversations of the people ranked below you, read-only; each view is audited |
| `/vault` | manager, admin | Envelope encryption of the knowledge branches: sealed branches, roles with keys, what is decrypted in memory, key rotation and revocation (admin) |
| `/security` | manager, admin | The access model as the running system is enforcing it |
| `/account` | everyone | Your clearance, and optional authenticator enrolment |

Navigation is filtered by role, but that is a convenience and not a control: the API refuses on
its own, so typing a URL you are not cleared for gets you an error, not data.

Smaller features worth knowing about:

- **Chat answer footer**: a "human review required" badge when governance flags the answer, a
  show/hide toggle for its citations, the models used and the number of routed calls, and
  per-answer Word / Excel / PowerPoint export (which creates a review draft).
- **Tools**: sample arguments per tool; a sandbox editor with preset attack snippets ("try
  egress", "try subprocess", "memory bomb", "infinite loop"); the sandbox run log with its
  chain-integrity chip.
- **Sovereignty** refreshes itself every three seconds (the live toggle pauses it); the TLS state
  it shows comes from the `/sovereignty` report.
- **Knowledge**: administrators can edit a branch's role allowlist in place.

Not wired to any page: the wrappers `reviews` / `decideReview` (the HITL queue), `requestAccess`
(access requests are raised automatically when an answer is refused), `intake`, `audit`, `agents`
and `egress` exist in `src/lib/api.ts` but no page calls them, and there is no wrapper for
`/vault/tls`, `/security-log`, `/schema`, `GET /runs` or the promote route. Image attachments use
`/upload`, which runs intake server-side, rather than `/intake`.

---

## The three things worth knowing before reading the code

**The tab is the session.** The token is mirrored to `sessionStorage` — never `localStorage`,
never a cookie — so a reload or a navigation picks the same signed-in session back up, and closing
the tab ends it. On start-up the stored token is presented to the API and dropped the moment the
server stops recognising it; it decides nothing on its own.

**Conversations live on the server.** (The header comment in `pages/Chat.tsx` still says they are
held in memory for the life of the tab; that comment is out of date.) Each exchange is written as it lands, with the full answer
and the security envelope it was released with. The sidebar lists your conversations
(`GET /sessions`); opening one redraws it from the server (`GET /sessions/{id}`), attachments
included, and you carry on in it. The browser keeps only *which* conversation a tab has open —
`?c=<id>` in the URL, mirrored to `sessionStorage`.

**The security model is data, not prose.** Every answer carries a `security` envelope —
classification, source documents, what was withheld, which role can release it, the request id,
the grant id. The UI draws banners and buttons from those fields. It never scrapes the answer
text for them, so a wording change in the backend cannot silently break a control.

**Markdown is rendered by hand.** `src/components/Markdown.tsx` is a small parser for the subset
the workbench emits (headings, bullets, ordered steps, tables, quotes, code, bold/italic/code
spans, `[1]` citations, bare URLs). It exists rather than a library because every leaf is emitted
as a React text node and **no HTML is ever interpreted** — the text comes out of PDFs the
workbench did not write, and an answer built from classified documents should not pass through a
dependency that can execute or inject markup.

---

## Layout

```
src/
  main.tsx          routes: SignIn while signed out, `/` -> `/chat`, each page in an error boundary
  lib/
    api.ts          one request() with the bearer token, ApiError, MfaRequiredError, streamRun()
    types.ts        a mirror of the backend contract — blocks, envelopes, requests, grants
    types_ext.ts    the September subsystems' contract (mirrors api_ext.py)
    format.ts       ms, countdown, relativeTime, plural, initials
    cn.ts           class merge
  store/
    auth.tsx          AuthProvider: sign-in, the two-step challenge, expiry clock, role helpers
    conversations.tsx the server-side conversation list; which one this tab has open (`?c=`)
  ui/index.tsx      the kit: Panel, Button, Input, OtpInput, Badge, Dialog, Tabs, toasts, Stat
  components/
    Markdown.tsx      the renderer described above
    AnswerBlocks.tsx  one component per block kind the backend can emit
    ThinkingTrace.tsx collapsed "Thinking · <phase>", expanding to the per-agent trace
    SecurityBanner.tsx classification strip, withheld notice, key-refused notice, clearance chip
    RecentConversations.tsx the sidebar's conversation list and "new conversation"
    Bits.tsx          chain-integrity chip, key/value list, monospace output box
    ErrorBoundary.tsx a render error on one page does not blank the whole app
    LinkButton.tsx    a router link styled as a button
  layouts/AppShell.tsx  sidebar, clearance chips, session expiry
  pages/            one file per route in the table above, plus SignIn.tsx
```

### Streaming

`streamRun()` reads `GET /runs/{id}/events` with `fetch` rather than `EventSource`, because the
stream needs an `Authorization` header and `EventSource` cannot send one. The endpoint replays
past events before streaming live ones; replayed frames arrive in a reduced shape without
`phase`, `step_id` or `thinking`, so they are dropped (`payload.replay`) and only live frames
reach the trace. Without that the trace shows every phase twice.

---

## The access model, as the UI presents it

A document is read either by every role at or above its classification, or — where an
administrator has pinned one — by exactly the roles named on it. The Knowledge page shows both:
an open branch lists its chapters and counts, a locked branch shows its name, its classification
and the role to ask, and **nothing about its size**. Knowing a document exists is what makes an
access request possible; knowing how large it is would measure the tier above yours.

When a question needs material you cannot read, the answer is still given from what you can, and
the envelope carries the rest: how much restricted material bears on it, which branch it is in,
and a request raised with the lowest role that can release it. That approver previews the actual
passages before deciding. Approval issues a one-time key; pasting it under the answer re-asks the
same question and opens those records and no others.

A key that is refused — stale, spent, someone else's, or for a different question — is reported
as such. Without that, a dead key and a genuinely undocumented value look identical on screen.

### Attaching a document to a conversation

The composer's attach button accepts `.pdf`, `.png`, `.jpg` and `.jpeg`. Every file goes to
`POST /upload` and is stored under `data/workbench/uploads/<username>/<session_id>/`.

**An image** is not parsed as a document: the server runs the intake pipeline on it (on-device OCR
with a confidence per line, then the local vision model). The OCR and vision text become notes on
the conversation, lines below the confidence threshold are flagged, and a review draft is created;
the chat shows the line count, the flags and an "open in Review" link.

**A PDF** is parsed with the knowledge layer's own pipeline and indexed **for that conversation
only**. It is not added to the shared corpus, not classified, and
nothing is written into `data/knowledge` — the parse is cached under
`data/workbench/uploads/_cache/<hash>/` so the same file is never parsed twice.

The person who uploaded it can read it whatever their role: it is their own file. Nobody else can,
in any conversation, by any phrasing.

Parsing is slow the first time a file is seen — minutes for a large document — so the composer is
**disabled** until the run reports `indexed`, and a progress bar shows how far the parser has got:
real page counts reported by the parser as each window lands (`read 12 of 32 pages`), not an
invented estimate. Reading the pages owns 85% of the bar because that is where the time goes; the
stages after it move it the rest of the way.

Blocking the composer is the point, not decoration. A question asked mid-parse is answered from
everything *except* the document being read, so it comes back as a confident non sequitur about
the corpus — which is exactly what it looks like when the feature is broken.

A question that points at the document — "what does this catalogue cover", "what is in the
attached pdf" — is answered from the attachment alone, not from the corpus. An answer that used
the attachment shows an `attachment` chip in the footer instead of a classification, because
nobody has classified it.

"remove" beside the attachment chips drops them. Nothing else changes, because they were never
anywhere else. Moving one into the shared knowledge layer is a separate, deliberate act
(`POST /knowledge/documents/{id}/promote`) and it takes a manager.

### Authenticator codes

Two-step verification is available on the Account page and **off by default**: the confidentiality
this workstation is built for lives in the knowledge layer, not at the door. Set `RWB_MFA=on` on
the backend to ask managers and administrators for a code at sign-in, or `RWB_MFA=admin` for a
single role. With `RWB_OTP_DEMO=1` the expected code is shown on screen, which is for
demonstrations on a machine with no phone enrolled and never for a deployment.
