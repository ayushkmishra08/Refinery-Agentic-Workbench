# MRPL AI Workstation — web front end

The browser client for the Refinery Engineering AI Workbench. It talks to the FastAPI service in
`workbench/app/api.py` and to nothing else: no database, no server of its own, no telemetry.

```bash
npm install
npm run dev          # http://127.0.0.1:5173, proxying /api -> http://127.0.0.1:8077
```

The backend has to be running:

```bash
# from the repository root
.venv/Scripts/python -m uvicorn workbench.app.api:app --host 127.0.0.1 --port 8077
```

Point the client somewhere else with `VITE_WORKBENCH_URL` (see `.env.example`).

| script | what it does |
| --- | --- |
| `npm run dev` | Vite dev server with HMR |
| `npm run build` | typecheck, then a production bundle in `dist/` |
| `npm run preview` | serve the built bundle |
| `npm run typecheck` | `tsc -b --noEmit` |

---

## What the screens are for

| route | who sees it | what it is |
| --- | --- | --- |
| `/` | signed out | Sign in, and an explanation of the access model before you need it |
| `/chat` | everyone | Ask a question. Thinking, answer blocks, `btw`, PDF upload, effort, access keys |
| `/overview` | everyone | Your workspace: what you can read, what is running, what is waiting on you |
| `/documents` | everyone | Every document, its classification, and who reads it |
| `/knowledge` | everyone | The knowledge layer branch by branch, with your reach marked on each one |
| `/access` | everyone | Requests you raised, and — for approvers — the queue waiting on you |
| `/security` | manager, admin | The access model as the running system is enforcing it |
| `/account` | everyone | Your clearance, and optional authenticator enrolment |

Navigation is filtered by role, but that is a convenience and not a control: the API refuses on
its own, so typing a URL you are not cleared for gets you an error, not data.

---

## The three things worth knowing before reading the code

**The tab is the session.** The token is mirrored to `sessionStorage` — never `localStorage`,
never a cookie — so a reload or a navigation picks the same signed-in session back up, and closing
the tab ends it. On start-up the stored token is presented to the API and dropped the moment the
server stops recognising it; it decides nothing on its own.

**Conversations live on the server.** Each exchange is written as it lands, with the full answer
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
  lib/
    api.ts          one request() with the bearer token, ApiError, MfaRequiredError, streamRun()
    types.ts        a mirror of the backend contract — blocks, envelopes, requests, grants
    format.ts       ms, countdown, relativeTime, plural, initials
    cn.ts           class merge
  store/auth.tsx    AuthProvider: sign-in, the two-step challenge, expiry clock, role helpers
  ui/index.tsx      the kit: Panel, Button, Input, OtpInput, Badge, Dialog, Tabs, toasts, Stat
  components/
    Markdown.tsx      the renderer described above
    AnswerBlocks.tsx  one component per block kind the backend can emit
    ThinkingTrace.tsx collapsed "Thinking · <phase>", expanding to the per-agent trace
    SecurityBanner.tsx classification strip, withheld notice, key-refused notice, clearance chip
  layouts/AppShell.tsx  sidebar, clearance chips, session expiry
  pages/            one file per route in the table above
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

"Attach PDF" in the composer parses the file with the knowledge layer's own pipeline and indexes
it **for that conversation only**. It is not added to the shared corpus, not classified, and
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
