# Access control, composed answers and conversation

Four changes to the workbench, all visible to whoever is sitting in front of it:

1. **Nothing is readable until someone signs in.** The CDU manual is classified, and a guest is
   cleared for nothing classified.
2. **The answer is written, not assembled.** The retrieved claims, edges, steps and passages are
   context for an answer; they are no longer the answer.
3. **A turn is read in the context of the ones before it.** "What if we use 11-E-01 instead?"
   knows what it is instead *of*.
4. **A tag that does not exist is matched to the ones that do.** "12-3-01" is answered as
   12-P-01 when that is clearly what was meant, and asked about when it is not.

---

## 1. Access control

Three roles (`user` < `manager` < `admin`), three document tags (`INTERNAL` < `CONFIDENTIAL` <
`SECRET`), and one default rule: a role reads a document when its level reaches the tag's. An
unauthenticated caller is `guest` and reads nothing. In this deployment `setup-security` pinned
the tags: the CDU operating manual is `SECRET`, the Crude desalter manual is `CONFIDENTIAL`, and
every other document present at setup (the standards, the vendor manuals and ESBWR) is
`INTERNAL` — setup gives `INTERNAL` to anything its two assignments do not match. A document
promoted from a conversation upload defaults to `CONFIDENTIAL`.

Two refinements to the ladder:

- **Compartments.** An administrator can pin an explicit reader allowlist on a document
  (`POST /security/documents/{id}/roles`, `workbench/security/classification.py`). The document
  is then read by exactly those roles, and seniority no longer implies access — an allowlist of
  `["manager"]` shuts out `admin`. The tag is unchanged and still drives banners and refusals.
- **Deny by default.** A document the registry classifies on its own — one added after setup,
  say — goes through the pattern rules, and if none matches it falls back to `SECRET` (admin
  only). A record whose document id the registry has never classified is treated as `SECRET`
  too.

Enforcement is at the knowledge service, not at the prompt: agents receive a
`GuardedKnowledgeService` and cannot reach a document their principal is not cleared for through
any route in the answer pipeline. (Outside it, the code sandbox is currently an exception: without
Docker, code run through `/sandbox/run` or `run_python` can read files on disk directly — see
`SECURITY.md`, Known limitations.) A question the caller's role cannot answer can be escalated — an approver reviews the
exact records and, if they agree, issues a signed one-time key that opens those records and
nothing else.

Signing in, in brief (`workbench/security/auth.py`, `totp.py`):

- **Accounts.** First run seeds `admin` / `Admin#2026`, `manager` / `Manager#2026` and `user` /
  `User#2026`, each flagged `must_change` unless its password came from `RWB_ADMIN_PASSWORD`,
  `RWB_MANAGER_PASSWORD` or `RWB_USER_PASSWORD`. Passwords are stored as PBKDF2-HMAC-SHA256
  (240 000 iterations, 16-byte salt).
- **Lockout.** Five wrong passwords inside fifteen minutes lock the account for fifteen
  minutes (`423` from the API). A correct password resets the counter. **Note: currently** wrong
  authenticator codes do not count toward the lockout, and an unknown username and a wrong
  password produce different messages.
- **Tokens.** 32 random bytes, stored only as a SHA-256 digest, valid for eight hours; revoked on
  logout, and invalidated when the account's role changes.
- **Second factor.** TOTP (6 digits, 30-second step), off by default; `RWB_MFA` names the roles
  that must present a code. Enrolment is two steps (enrol, then confirm with a code), and each
  accepted step is burned so a code cannot be replayed. When a required, enrolled role signs in
  without a code the API answers `428` and issues no token. A required role that has not
  enrolled is still let in and flagged `mfa_enrolment_pending`. The secret is kept in
  `users.json` in plain text.

**The whole of it is documented for a first-time reader in [`docs/SECURITY.md`](SECURITY.md)** —
the model, the escalation walk-through, how a key proves itself, the red-team check and the
answers to the questions people ask. What follows here is the *answer composition* work, which
is a separate concern.

## 2. The composed answer

### The problem

Before: a lookup returned a KPI block, an evidence list, a confidence block and an audit line;
"what does this pump do" returned five verbatim passages, twelve inferred instrument edges and
eighteen citations. All of it true, none of it an answer.

### The agent

`workbench/agents/composer.py` runs in Phase 5, between verification and governance. It:

1. flattens everything the run produced into a few shapes — documented values as sentences,
   relationships with both endpoints named, condensed procedure steps, trimmed passages,
   safety points, the limit verdict, what is missing;
2. builds a **brief**: the question, the conversation it belongs to, the equipment, and the
   material that bears on it, ordered by task type and trimmed to
   `cfg.llm.answer_brief_tokens` (1500 by default);
3. asks the model for `{answer, assumptions, not_documented}` — a *structured* call, because a
   grammar-constrained decode cannot open with "Hmm, the user wants me to...", which is exactly
   what a 4B model does when asked for free text;
4. checks what came back, and releases it or falls back.

### What the answer is checked against

| Check | What it catches |
|---|---|
| Figures | A number the brief does not contain |
| Tags | An equipment tag the brief does not contain |
| Names on tags | `12-F-01 (atmospheric furnace inlet)` — a right tag with a wrong name. Most of the gloss must appear in the documented name, type or aliases |
| Contradictory qualifiers | atmospheric/vacuum, suction/discharge, inlet/outlet, upstream/downstream, overhead/bottom, cold/hot — a furnace is a furnace, but the *vacuum* one is not the *atmospheric* one |
| Preamble | Opening sentences about the question rather than about the unit. Every leading one is cut, provided at least 25 words remain; otherwise nothing is cut and the answer is re-asked |
| Repetition | An answer that hands back the previous turn's answer (>75% similar) |
| Length | An answer of fewer than 12 words |

A failed check is re-asked once with the problem named (`answer_retries`). A second failure falls
back to the deterministic composer, and so does a model call that fails outright (Ollama down,
unparseable output) — that one falls back at once, without a re-ask. The deterministic composer
writes the same shape of answer from the same material without a model — plainer prose, never a
block dump. That path is what `effort=low`, `RWB_LLM_ANSWER=off`, a dead Ollama and the whole
test suite use. If the composer itself raises, the run continues without a composed answer and
the released markdown is the block rendering.

### Why prompt length, not context size, is the budget

On a GTX 1650 with qwen3:4b at `num_ctx` 4096:

| Prompt | Generation | Rate | Wall clock |
|---|---|---|---|
| ~330 tokens | 260 tokens | 17 tok/s | 25 s |
| ~5800 tokens | 250 tokens | 3.7 tok/s | 131 s |

The brief is capped for that reason. Sending everything retrieved would cost four times the
wall clock for a worse answer.

### What is released

`FinalResponse.answer_markdown` in `brief` style (the default) is:

```
<the composed prose>

<at most 2 supporting blocks, one per type — ordered steps, a limit gauge, a comparison, a table>

<the first safety block carrying a DANGER or WARNING flag, when there is one>

*Not covered by the documents: ...*            (at most 3 items)
*Assumed: ...*                                 (at most 2 of the model's assumptions)
*<access line>*                                (see below)
**Human review required.** <reason>            (when governance flags the answer)
*Source: CDU operating manual, p. 60, 66, 73, 96, 125, 192.*
```

Supporting blocks are trimmed: a table keeps 8 rows (the caption says how many were left out), a
comparison keeps its first 8 attributes and 3 differences, and a steps block keeps 24 steps
(3 x `max_supporting_rows`). Only the first safety block with a DANGER or WARNING flag is
printed; the rest stay in `blocks`. The deterministic composer produces no assumptions, so its
answers never carry the *Assumed* line.

The access line is either *Released under approved access grant G-…: N record(s) in ….
Nothing else in those documents was opened.* when an access key was honoured, or the policy's
message naming what was withheld from this role when anything was. After governance the
orchestrator appends the classification line (*Classification: … · built from … · released to
…*) and, when restricted material bears on the question, the escalation note with the
access-request id.

The source line names the pages that reached the brief, not every page retrieval touched: a
page the composer never saw is not a source for what it wrote.

`FinalResponse.blocks` still carries everything — KPI, evidence, confidence, verification,
plan, audit — so a frontend renders it in panels and `python -m workbench ask ... --detail`
prints all of it. `RWB_ANSWER_STYLE=full` restores the old rendering as the default.

Composition is skipped, and the wording released verbatim, when the run is asking a
clarification or refusing a restricted request. Those are already written for the situation, and
recomposing them would soften a refusal and bury a question. It is also skipped when no agent
produced any block or evidence — there is no material to compose from. A skipped composition is
announced as a `warning` progress event.

---

## 3. Conversation

`workbench/memory/followup.py` rewrites a dependent turn into a standalone question *before*
classification, because everything downstream reads one sentence and has no memory.

| Shape | Trigger | Rewrite |
|---|---|---|
| `substitution` | instead, rather than, in place of, "what if we used X" | a comparison against the previous subject; the task type becomes `comparison` and both sides are resolved |
| `elaboration` | opens with and/but/why/what about, and names no subject of its own | the previous subject with the new angle attached |
| `continuation` | a pronoun or a short question with no subject | the pronoun replaced by the subject it refers to |
| `new` | names its own subject | untouched |

```
> what does pump 11-P-01 do
  Pump 11-P-01 (Crude Charge Pump) discharges crude oil to the vacuum heater 12-F-01. It
  operates at 24 kg/cm² pressure and has a rated capacity of 482 m³/h ...

> what if we use 11-e-01 instead
  The proposed 11-E-01 (Crude/HN) exchanger does not function as a pump. It is a heat
  exchanger ... Unlike 11-P-01 (Crude Charge Pump), which moves crude oil at 482 m³/h rated
  capacity ... Using it instead would not meet the crude charge pump function.

> and at start-up?
  Pump 11-P-01 A/B is started during startup to charge crude oil into the unit after gravity
  displacement of air ...
```

Rules do the work; the model is asked to rewrite only when the sentence is clearly dependent and
the rules could not supply a subject, and its version is kept only if it actually names the
carried subject. `Turn` records what was typed, what it was read as, and the composed answer, so
the next turn can refer back.

---

## 4. Tags that do not exist

`workbench/services/tag_matcher.py`. A tag is decomposed into unit / class / number / train and
each part scored separately, because the parts fail differently. The unit prefix is almost never
wrong — it is the section of plant the engineer is standing in. The number is usually right. The
class letter is what gets dropped, OCR'd into a digit, or guessed.

The equipment word in the sentence is a signal: `pump 12-3-01` prefers a pump, `exchanger
12-3-01` prefers an exchanger. The A/B trains of one item are collapsed, so three spellings of
one pump do not read as three candidates.

```
what does the exchanger 12-3-01 do
  -> There is no 12-3-01 in the documents; the closest documented item is 12-E-01
     (Crude/ Kero Cr), and this answer is about that. If you meant 13-E-01 instead, say so.
     The exchanger 12-E-01 preheats crude oil in the atmospheric section ...

what does pump 12-3-01 do
  -> There is no 12-3-01 in the loaded documents. Several documented items are an equally
     close match — which one did you mean?
       12-P-01 (Quench Pumps) — a pump, same unit prefix 12
       12-PM-01 (SR Pumps) — a pump, same unit prefix 12
```

The difference is `adopt()`: a candidate is taken without asking only when it scores ≥ 0.72
*and* the runner-up is ≥ 0.08 behind, or when every field of the tag matches exactly, or when
two near-perfect matches differ clearly in how often the manual talks about them. Two unit-12
pumps numbered 01 are a genuine tie, and one extra question is cheaper than an answer about the
wrong pump.

The correction sentence is written deterministically and placed first, whatever the model wrote,
so which equipment the answer is about is never buried.

A related fix: the backend's own fuzzy resolution used to accept `11-PP-01` as `11-F-01` — a
pump becoming a heater, silently. Tag resolution is now exact-only; anything else goes to the
matcher, which can weigh the unit, the number and the class the question named.

---

## 5. Configuration

| Setting | Default | Effect |
|---|---|---|
| `RWB_AUTH` | on | `off` disables the access gate entirely (every caller becomes an administrator) |
| `RWB_ADMIN_PASSWORD` | `Admin#2026` | Password for the seeded `admin` account (read at first run only) |
| `RWB_MANAGER_PASSWORD` | `Manager#2026` | Password for the seeded `manager` account (read at first run only) |
| `RWB_USER_PASSWORD` | `User#2026` | Password for the seeded `user` account (read at first run only) |
| `RWB_MFA` | off | `on` requires an authenticator code from `manager` and `admin`; or a comma list of roles |
| `RWB_OTP_DEMO` | off | `1` returns the expected code in the sign-in challenge — demonstrations only |
| `RWB_ANSWER_STYLE` | `brief` | `full` puts the whole block rendering in `answer_markdown` |
| `RWB_LLM_ANSWER` | on | `off` always uses the deterministic composer |
| `cfg.llm.answer_brief_tokens` | 1500 | Ceiling on the brief |
| `cfg.llm.num_predict_answer` | from effort | Token budget for the answer |
| `cfg.llm.answer_retries` | 1 | Re-asks allowed when a check fails |
| `cfg.presentation.max_supporting_rows` | 8 | Rows per supporting table and attributes per comparison under the prose (steps: 3x) |
| `cfg.presentation.max_supporting_blocks` | 2 | Supporting blocks appended at all |

Effort levels set `llm_answer`, `llm_followup` and `answer_words` (120 / 170 / 220 / 300 for
low / medium / high / ultra). `low` never calls a model and uses the deterministic composer.

## 6. Tests

```
tests/workbench/test_security.py     97  credentials, lockout, tokens, classification, policy,
                                         the guard, the gate end to end, the HTTP surface
tests/workbench/test_mfa.py          28  authenticator codes, enrolment, the sign-in challenge
tests/workbench/test_composer.py     25  prose not blocks, grounding, misnamed tags, preamble,
                                         the released markdown, brief/full styles
tests/workbench/test_followup.py     13  the four shapes, rewriting, through the pipeline
tests/workbench/test_tag_matcher.py  17  parsing, scoring, suggesting, adopt-vs-ask
```

`python -m pytest tests -q` — 665 tests collected: 533 workbench + 132 knowledge-layer.
`python -m workbench bench` — 70 prompts, LLM-free, currently 100% on every scored dimension.
