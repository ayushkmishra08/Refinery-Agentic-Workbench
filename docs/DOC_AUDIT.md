# Documentation audit — 2026-09-26

Every doc in the repo was read in full and each concrete claim was checked against the source
(commands, paths, counts, defaults, endpoints, schemas, algorithms). Nothing was edited except this
file. Line numbers refer to the doc being discussed; code references are `file:line`.

**Overall verdict:** the docs are *not* yet safe to write the project report from. The core ideas
are described correctly, but most docs were written on 2026-09-15/16 and were never refreshed for the
September subsystems, the Answer Composer, the six-document corpus, or the multi-model router. Several
numbers disagree between docs, a few security claims are stronger than the code, and some real code
bugs surfaced along the way (section 1).

---

## 0. Ground truth (use these numbers in the report)

| Fact | Actual value | Source |
|---|---|---|
| Tests | **665** collected (533 workbench + 132 knowledge_layer). test_security 97, test_mfa 28, test_sandbox 19, test_composer 25, test_followup 13, test_tag_matcher 17 | `.venv\Scripts\python.exe -m pytest --collect-only` |
| Benchmark | **70 prompts, 16 categories** (adds `inventory`, `out_of_scope`). Latest run 2026-09-16 20:20: all six metrics 1.0, mean confidence 0.637, mean 434 ms, 30.6 s total, 0 LLM calls | `workbench/benchmarks/prompts.yaml`, `data/workbench/reports/benchmark-20260916-202058.md` |
| Documents loaded | **6**: CDU operating manual, API610, API560, Bulletin-5EH, Crude desalter, ESBWR | `workbench/config.py:429-439`, `data/normalized` |
| CDU figures | 562 pages, 814 tables, 36 chapters, 1,119 sections (index; KL profile says 1,084), 182 procedures, 1,503 steps, 467 chunks, 172 glossary terms, 96 abbreviations, 19 standing instructions, 20 cross references | normalizer/chunker output, `document_profile.json` |
| Index graph | 1,056 entities, 276 relations, 1,531 claims | workbench cache build_stats |
| Task types | **15** (includes `INVENTORY`) | `workbench/core/request.py:28` |
| Agent keys | **17** (12 named + 4 retrieval specialists + `answer_composer`) | `workbench/agents/registry.py:6-30` |
| Block types | 16 (includes `text`, `image`) | `workbench/core/blocks.py` |
| Knowledge backend | default `auto` → `files` if artefacts exist, else `mock`. **Neo4j backend is a 20-line stub raising NotImplementedError** | `config.py:384,441-444`, `services/backends/neo4j_backend.py` |
| LLM extraction | **Never produced output.** Every KL report shows 0 LLM entities/relations/claims; the graph is deterministic-only (rule + table sources). API560 and Bulletin-5EH processed 0 chunks | `data/reports/*/extraction_by_source.json` |
| Models | Router on by default, picks per call from 7 models in `models/registry.yaml`: qwen3:4b, qwen3.5:2b, deepseek-r1:7b, qwen2.5-coder:7b, qwen3.5:9b, gpt-oss:20b, qwen3.6:27b. gpu_4gb profile needs qwen3:4b + qwen3.5:2b; deepseek-r1:7b is only the knowledge-layer default | `config.py:51,293`, `models/router.py` |
| LLM at default effort | **medium DOES call the LLM** (Answer Composer, unsure classification, follow-up rewrite). Only `low` is model-free. Reranker is **off** at medium (on at high/ultra) | `config.py:184-202` |
| LLM extraction flag | `apply_effort` overrides the hardware-profile flag, so diagnosis structuring is on only at `ultra` (any GPU) | `config.py:403` vs `:422` |
| Pipeline order (KL) | Parse + parse validation → table classification + reconstruction → normalize → profile → glossary → chunk → Neo4j schema/doc/terms → 7a document graph → 7b embeddings → 8 per-chunk extraction (LLM pass1/2 if available, then rule/spec/prose/table claims, validator, GraphInserter) → report | `knowledge_layer/pipeline.py:167-727` |
| Versions | pyproject 0.1.0, API 0.3.0, KL banner v0.3.0 | |
| serve port | defaults to **8000**; the frontend proxy expects **8077** (`serve --port 8077`) | `cli.py:691`, `WebPage/vite.config.ts:17` |
| Seed accounts | `admin/Admin#2026`, `manager/Manager#2026`, `user/User#2026` (no `lead` account) | `security/auth.py:48-52` |
| Upload paths | `data/workbench/uploads/<username>/<session_id>/`; sessions `sessions/<owner>__<session_id>.json` | `api.py:437`, `memory/session.py:83-85` |

---

## 1. Code issues found during the audit (fix or disclose before the report)

These are not doc problems — the code itself behaves in a way the docs promise it does not.

1. **Sandbox escape → full data bypass (critical).** Without Docker the subprocess backend is used,
   whose isolation is only Python-level patches. `open()` reads are unconfined, `io.FileIO` is not
   wrapped, `_socket` is not banned, and static-analysis failures are advisory (code still runs).
   Any signed-in account, including `user`, can call `POST /sandbox/run` (`api_ext.py:470-473`) and
   read `data/parsed`, `users.json`, `secret.key`, `kms/master.key`. Verified with a probe
   (`workbench/sandbox/runner.py`, `manifest.py:43-48`).
2. **Unauthenticated endpoints:** `/runs`, `/runs/{id}`, `/runs/{id}/events`, `/btw` (expose any run's
   text and answer), `GET /vault`, `GET /sovereignty`, `/sovereignty/connections`, `/sovereignty/egress`.
3. **`/audit/{session_id}` always returns `[]` for non-admins**: files are written under the raw
   session id (`orchestrator.py:953`) but the check requires `<username>__` prefix (`api.py:520`).
4. **SSE: first `final` event is not a FinalResponse** — the orchestrator's `{response_id, status}`
   `final` is forwarded before the real one (`api.py:342-349`). A client that closes on first `final`
   (as API.md documents) breaks.
5. **`--clean` / `--rebuild` wipe Neo4j before *every* PDF** (`pipeline.py:352-354`), so with 6 PDFs in
   `data/raw` only the last document survives.
6. **CLI sign-in crashes for MFA-required accounts** — `prompt_login` catches `AuthError` but
   `MfaRequired` is not one (`credentials.py:104-111`, `auth.py:63`).
7. **Username enumeration**: unknown user vs wrong password give different messages; locked → 423
   (`auth.py:309,312,327`, `api.py:210`).
8. **CLI commands with no role check:** `users --add` (anyone can add an admin), `security-log`,
   `security-check`; `revoke-key` only blocks `Role.USER` so a guest passes (`cli.py:388-545`).
9. **`approvals --all` / `GET /approvals?show_all=true`** return every request to any signed-in user
   (`escalation.py:331-334`). Preview gate uses `max(tags, key=t.value)` (string order), and ignores
   compartment allowlists (`orchestrator.py:778`).
10. **Grant key race:** no lock between `redeem` and `consume`; a key is only spent on
    answered/needs_review (`orchestrator.py:1332`). Latent: `grant.max_uses` is set after signing
    (`orchestrator.py:810`), so `grant_max_uses≠1` would break every key.
11. **TOTP brute-force:** wrong codes never count toward lockout and a correct password resets the
    counter (`auth.py:328,359-363`). TOTP secret is plaintext in `users.json`.
12. **Classification file parse error** re-derives default tags and drops pins/allowlists — can
    *widen* access (`classification.py:108-112`).
13. `workbench passwd` accepts 4-char passwords; `chmod(0o600)` is a no-op on Windows; CORS is
    `allow_origins=["*"]` (`api.py:77`).
14. `python -m knowledge_layer --help` is promised (`__main__.py:5`) but runs the full pipeline.
15. `npm run lint` is broken — eslint packages are not in devDependencies.
16. *(found while fixing docs)* `vault rotate` / `vault revoke` CLI have no role check (`cli_ext.py:164-180`); only `seal` requires admin.
17. `setup-security --reset-passwords` without `--random-passwords` resets to the seed defaults and clears must-change.
18. `security/setup.py` docstring says unmatched documents "stay SECRET"; the code pins them `INTERNAL` (`setup.py:39,54`). SECRET applies only to later, rule-unmatched documents and never-classified ids.
19. Audit traces are keyed by raw `session_id`, so users sharing an id (the `/ask` default is `"web"`) share one trace file.
20. `GET /sandbox/runs` returns every account's runs.
21. When an escalation callout is appended, the audit block is no longer last, so `chained_audit_hash` stays unset and the trace's `final` record has empty `models_used`.
22. Upload SSE stream ends "failed" even when indexing succeeded; `/stats` `runs_mine` is always 0; unknown request id on approve/deny returns 403 not 404.
24. Response store saves each response before `chained_audit_hash` is stamped (`orchestrator.py:1198` vs `1204`), so stored responses never carry the head hash.
25. `promote_upload` docstring says unclassified uploads get the most restricted tag; `UPLOAD_TAG` is `CONFIDENTIAL` (`classification.py:48`).
26. Follow-up LLM rewrite runs on every dependent turn at medium+, not only when rules fail (contrary to its docstring).
27. Knowledge layer: Phase 2 table classification has no checkpoint skip; pass 2 runs whenever pass 1 finds ≥2 entities (contrary to its config description); validator layer 5 never fires; reporter "Validation" section never renders; 7 of 9 prompt files unused.
28. `RetrievalSettings.bm25_k`, `vector_k`, `fused_k` are never read; context_builder docstring says preferences are "never hard filters" but `chunk_types` is hard; config docstring says "ultra adds the reranker" but it's on from `high`. LLM extraction is also enabled by `RWB_LLM_EXTRACTION=on` (applied last, `config.py:464`).
30. **Upload path traversal:** the `session_id` form field is joined into the upload path unsanitised (`api.py:437`, `api_ext.py:521`), so `../` can write outside `uploads/` (any signed-in user).
31. **Classification banner mislabels:** `env.classification = max(tags)` compares tags as strings (`orchestrator.py:1360`), so CONFIDENTIAL + INTERNAL material is stamped INTERNAL.
32. Loading a hash-chained file that contains any unchained line re-chains the whole file (`hashchain.py:166-201`), hiding earlier edits; `repair()` is not wired to any command.
33. `users --add` overwrites an existing account's password and role; `setup-security`, `packages`, `sandbox-run`, `audit-verify`, `vault status` have no role check; `/runs/{id}/btw`, `/models`, `/models/routing`, `/vault/tls`, `/health` are unauthenticated.
34. Model package `key_id` can point outside the trust store (`model_updates.py:177`) — read, not tested.
35. Pipeline's Neo4j warning points to `scripts/start_neo4j.ps1`; real path is `knowledge_layer/scripts/start_neo4j.ps1`.

---

## 2. Per-document findings

### README.md — mostly accurate, stale in places
- L373 "132 unit tests" → 665 total (132 is KL only).
- L292 "0.8 s mean" → latest 434 ms.
- L276 implies default effort makes no model call — medium does (composer, classifier, follow-ups).
- L152-172 `context_key()` table: `temporal_status` is **not** part of the key (`schemas/claims.py:295-306`).
- L369 "mock/neo4j backends" → files (default), composite, mock; neo4j is a stub.
- L220-222 `--clean`/`--rebuild`: must warn they clear Neo4j per PDF (see 1.5).
- L391-395 Neo4j figures (1,528 claims, 531 entities, 169 relations) come from an old run; not reproducible.
- L260 agent count omits `answer_composer`.
- **Project Structure (L355-371) is stale:** root `schemas/ src/ prompts/ scripts/` no longer exist
  (now `knowledge_layer/...`); prompt list shows 2 of 9 files; scripts list incomplete; `docs/` list
  missing most docs; `data/normalized/<doc>_normalized.json` not folders. Omits workbench packages
  models/ sandbox/ intake/ deliverables/ review/ security/ sovereignty/ benchmarks/, `scripts/setup_security.py`, WebPage/.
- Missing: commands logout, passwd, users, classify, security-log, request-access, requests,
  approvals, approve, deny, revoke-key, schema, agents; flags `ask --key/--session`,
  `export --format md/--title/--out`, `draft-signoff --reject`, `sovereignty --deep`, `route --budget`,
  `bench --category/--limit/--llm`; serve default port 8000; ~20 undocumented `RWB_*` env vars;
  air-gap guard on by default sets `HF_HUB_OFFLINE` so embedding/reranker models must be pre-cached;
  grant TTL 30 min / single use; unclassified docs default to SECRET.

### SETUP.md — needs a rewrite of prerequisites and dependencies
- L15 `cd Refinery_KL_Extraction` → `Refinery`.
- L8 Neo4j listed as required — optional for both pipeline and workbench; **Ollama and Node/npm missing**.
- L39-45 dependency list omits fastapi, uvicorn, python-multipart, rank-bm25, pyyaml, numpy, httpx,
  rich, xxhash, cryptography, pyflakes, psutil, rapidocr, pypdfium2, pillow, python-docx,
  python-pptx, openpyxl, pywin32, pytest-asyncio.
- L81-84 offline tip (switch to all-MiniLM) breaks workbench retrieval, which hard-codes
  bge-small-en-v1.5 (`workbench/config.py:219`).
- L95-96 only seeded passwords are must-change; env-supplied ones are not.
- L106-107 sample question hits the SECRET CDU manual — only admin gets an answer.
- Missing: `ollama pull qwen3:4b` / `qwen3.5:2b`, hardware profile / RWB_PROFILE, backend auto-select,
  frontend setup, `serve --port 8077`, pre-caching models before air-gap, how to run tests, MFA env
  vars, `workbench setup-security` ≡ `scripts/setup_security.py`.

### walkthrough.md — obsolete; rewrite or delete
Pre-restructure snapshot. 49 tests (now 665); links point to another machine
(`c:/Users/pandi/.../Refinery_KL_Extraction/src/...`); 18 source modules (≈30); wrong phase order
(normalizer before table classifier); "130+ entity / 40+ relationship types" (101 / 38); 4096 ctx
(KL is 8192); omits phases 7a/7b and deterministic claim passes; v0.1.0 (v0.3.0); "skips Phase 8"
without Ollama (it runs deterministic passes); validation is per item, not all-or-nothing; Neo4j
install steps contradict SETUP.md.

### docs/PLAN.md — historical; status table stale
- L17 "mock/neo4j backends"; L20 "12 agents one file each"; L22 `pipeline.run()` (is `Orchestrator.ask()`);
  L25 FastAPI "later" (exists); L29 docs list; L40 vs L63 self-contradiction on the classifier;
  L66 retrieval agents listed as Phase 4 (registry says Phase 2); L63 62-prompt benchmark; L69
  frontend "pending" (exists); L77 omits cpu profile, multi-model routing, RWB_VISION_MODEL.
- The module map it relies on (`workbench/__init__.py` docstring) is itself stale.
- None of the September subsystems appear.

### docs/DEMO.md — runnable only after fixes
- L358 "62 prompts ~2.5 min" → 70 prompts ~30-37 s.
- L184-186, L236-237 "no model at default effort" → wrong; ~30 ms timings only with LLM off / `--effort low`.
- L186 model name may differ with routing.
- L292 "516 tagged items" → 520 with 6 docs, varies by role.
- L373-374 `--args '{"expression": ...}'` fails in PowerShell 5.1 (quotes stripped) — escape them.
- **Missing sign-in step**: auth is on by default and the CDU manual is SECRET → run
  `setup-security` + `login --user admin` (or `RWB_AUTH=off`). `vault seal` needs admin; planning/why
  demos need the Qwen models pulled.

### docs/webpagescript.md — deleted in working tree, not committed
746-line presenter script for the web UI, added in commit 92b5e59. Recover with
`git restore docs/webpagescript.md` if the deletion was unintended.

### docs/HOW_IT_WORKS.md — core explanation, significantly out of date
- L10/L566/L588 one document → six loaded.
- L16/537/560 "fourteen kinds of request" → 15; L94 "sixteen agent keys" → 17.
- L56-68 pipeline order wrong (see ground truth); L67-68 "LLM passes still running" → never ran.
- L80 1,052/~270 → 1,056/276.
- L86-88 uploads are **per session** (`Orchestrator.session_uploads`, `_knowledge_for`), not added to
  the shared service; manager-only `promote_upload` moves them in.
- §3.1 verification runs in Phase 5, not as a plan step; only `overall_score` feeds confidence.
- **§4.1/§4.2 flow is incomplete:** missing Phase 0 Access, follow-up rewrite, survey-instead-of-clarify,
  model routing, Answer Composer, security envelope, escalation offer, classification stamp, release gate.
  "Safety in phases 0,3,4,5" is wrong (resolver gate + Phase 4 steps only).
- §4.2 block order describes the pre-composer path; with `presentation.style="brief"` (default)
  `answer_markdown` is composed prose + ≤2 supporting blocks + sources line. Appendix A outputs predate this.
- §6 L278 chunk types / chapters list wrong (`context_builder.py:26-45`).
- §8 L323 restricted patterns also include `jumper`, `force … interlock/trip`.
- §10: reranker off at medium; chunk_types is a **hard** filter (only chapters soft); LLM-extraction
  flag overridden by effort; "one model resident" wrong with routing on; LLM-call list misses composer,
  follow-up rewrite, entity guess; report summary is **not** schema-constrained (`report.py:96`).
- §12 table missing `llm_answer`, `llm_followup`, `answer_words`.
- §14 62 prompts → 70; `bench --verbose` doesn't exist (`-v` is global, before subcommand).
- §15 "web front end pending" → exists.
- Audit trail is now hash-chained (`services/audit_store.py`), head hash stamped on the audit block.
- **Not explained anywhere in this doc:** parse_validation, table_classifier/reconstructor,
  table_context, structure, document_graph, OllamaExtractor, ExtractionValidator, entity_resolver,
  GraphInserter CONFLICTS_WITH/CORROBORATES, MemoryRetriever, ontology_manager, checkpoint/resume,
  reporter; workbench follow-up resolution, tag_matcher, model router, ContextBuilder inventory route,
  retrieval maths (RRF k=60, pool=max(4k,24), entity bonus), revision_resolver authority tuple,
  security path, response_store, deliverables, review drafts, intake, tools/sandbox, sovereignty.

### docs/architecture/agent_workflow.png — does not match the code
It is the design-brief diagram. Actual execution: Phase 0 Access (not drawn) → one Understanding
phase with just TaskClassifier + ContextResolver (which does entities/scenario/mode/ambiguity/evidence/
safety gate) → Phase 2 is one `ContextBuilder.build` service (the retrieval agents run later as Phase 4
plan steps; no Instrumentation agent) → planner does template/extend/prune/validate/12-step cap in one
step → sequential execution, replan only on required-step `needs_replan` → Phase 5 order is
Verification → Answer Composer → Governance → envelope/escalation/classification → release gate.
No safety pass in Phase 5; Report is a plan step; Neo4j box is a stub; routing strip lacks `inventory`.
**Redraw from `orchestrator.py _run` for the report.** (A byte-identical copy sits at
`workbench/a5983fcc-….png`.)

### docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md — plan still valid, status section stale
Integration is **still pending** (Neo4j backend stub; proposed full-text indexes `chunk_text`,
`procedure_title` not created; no LLM relations). Stale: 166 tests, 62 prompts, "frontend not
exercised", "LLM extraction running", "one document", `-v` placement, "all LLM output schema-constrained".
Corrections: relationships now carry explicit `r.source` / `r.grounding` (`memory.py:633-647`);
chunk_types is a hard filter; graph model omits HAS_ENTITY, HAS_CHUNK, IN_SECTION, SUPPORTS, MENTIONS,
HAS_CHAPTER/SECTION/SUBSECTION, NEXT, HAS_STANDING_INSTRUCTION and Claim fields grounding, unit,
unit_raw, claim_category, resolution_status, table_id, sentence_index; images now go via intake
(OCR→vision→review draft); uploads per session + `promote_upload`; file map omits composer, narration,
tag_matcher, kl_io, response_store, thinking_store, api_ext, cli_ext, credentials, deps,
thinking_display and all September packages.

### docs/API.md — routes complete, response details wrong
All routes documented ↔ implemented (no gaps).
- L41 MFA enrol key is `uri`, not `otpauth_uri`.
- L82-87 login: body also takes `code`; response adds `level, mfa_enrolled, mfa_satisfied,
  mfa_enrolment_pending`; **428** MFA challenge undocumented; `LoginBody.username` defaults to `"lead"`.
- L102 whoami shape differs from login.
- L108 audit access record — see bug 1.3.
- L113-118 AskBody also `access_key`; session_id default `"web"`.
- L125 401 body is `{"detail": …}`; `/runs` never 401s (final_status `unauthorized`).
- L135-157 SSE: first `final` issue (1.4); replay items carry only `{event,agent,message,replay,ts}`;
  `phase_started` also for `0 Access`; `phase_finished` for 3/4/5 too; agent events fire for non-plan
  agents; `plan_created` steps carry `mode, optional, safety_sensitive`; `llm_call` data `{prompt,max_tokens,model}`;
  `warning` IS emitted; undocumented `access_denied`; `error` from release gate → `blocked`;
  ProgressEvent has `thinking, model, decision`.
- L178-181 RunState missing `principal, followup, progress`.
- L205-216 uploads path; per-session not shared (contradicts §2.5b); images → intake + `draft`.
- L224 review items carry `ts, status`; L319 session filename; L356 status list missing `blocked`;
  L357 task_type missing `inventory`; L392-394 ordering rule only for non-composed path.
- L441 `GET /vault` listed as signed-in but has no auth check; L414 not every write hits the security audit.
- **Missing:** an auth/role column (reviews = manager, security-log manager+ scoped to own trail,
  etc.); unauthenticated `/runs*`/`/btw`; parameters/bodies for MFA delete, approvals, access-requests,
  approve/deny, promote; `/knowledge/tree` extra fields; the whole `security` (SecurityEnvelope)
  response field; `/health` extra fields; evidence `grounding`; plan fields; audit `models_used`,
  `routing[]`, `chained_audit_hash`. Section numbering skips §8.

### docs/schema/*.json — accurate
Freshly exported schemas are **byte-identical** to the committed files; all 16 block types present.
Caveats: `status`, `severity`, `event` are free strings (no enum); `user_request` ≠ `/ask` body
(AskBody not exported); no `replay` field in progress_event; no `$schema` key.

### docs/ACCESS_AND_ANSWERS.md — mostly verified
Correct: roles/tags ladder, tag assignments, signed one-time key format, composer placement,
1500-token brief, checks, retries, fallback, effort word targets, 70 prompts.
- L191 `RWB_LEAD_PASSWORD`/`1234`/`lead` does not exist → seed accounts in ground truth.
- L18-20 "one rule" omits compartment allowlists (`classification.py:60-83`) and the SECRET fallback.
- L65 every leading meta sentence is cut (if ≥25 words remain), not one.
- L92 supporting block types include `table`; steps capped 24, comparison 8 attributes.
- L206-214 test counts stale.
- Missing: <12-word rejection, `*Assumed:*` line, access line, human-review line, first
  DANGER/WARNING only, skip when no material, immediate fallback on model failure, MFA/lockout/token facts.

### docs/SECURITY.md — several claims overstated (important for a security report)
- L27-29/50/55-56 ladder isn't the only rule; clearing an allowlist *can* widen access; `set_roles` accepts any list.
- L89-92, L206-208 sample outputs stale (passages/documented values wording).
- L282 signature is 32 hex chars, not 24; L302-305 HMAC covers **seven** fields; question is a
  normalised SHA-256; L290-300 check order differs.
- L330-331 wrong-password vs unknown-user are **not** identical (1.7).
- L334 CLI stores the raw bearer token in `cli_session.json` → replayable 8 h.
- L350 uploads survive restart (`_ensure_uploads`).
- L376 CLI can't do MFA (1.6); L378 a token is minted then revoked at 428.
- L389-394 release gate: only provenance blocks; tag/figure checks are `note` severity.
- L413-415 red-team "shape" only checks status; probes hard-coded to this corpus.
- L476-479 parse error doesn't make everything SECRET (1.12); hand-editing plain JSON/key files can
  loosen access or mint grants.
- L472-473 no setting exists to hide document names.
- L462-467, L485-487 managers/admins **can** read lower-rank conversations (`supervised_session`,
  logged `conversation_viewed`) — and this breaks under compartments.
- L542 94 tests → 97.
- §12 permission column wrong for users --add, security-log, security-check, revoke-key, setup script.
- **Overstated:** "structural / cannot reveal what it doesn't have" (sandbox bypass; guard `__getattr__`
  passes unknown methods; `.primary/.inner` expose raw backend); "when in doubt, refuse"; must-change
  never enforced; any decider can approve; key single-use has a race; approvers see all requests;
  hash chain is unkeyed SHA-256 and truncation is undetectable; TOTP brute-forceable.
- §10 event list missing ~21 events (mfa_*, conversation_viewed, document_roles_set, upload_promoted,
  branch_*, key_*, sandbox_run, intake, deliverable_exported, draft_*, signoff_blocked,
  model_package_*); `egress_blocked` actually goes to `sovereignty/egress.jsonl`.
- §13 code map omits totp, tls, vault, vault_backend, sovereignty/*, sandbox/*, credentials.
- **Undocumented mechanisms:** sandbox controls (job object 512 MB/20 s, Docker flags, "verified"
  definition); air-gap egress guard (what it covers and doesn't: UDP, DNS, native code, child
  processes; netmonitor 2 s polling); supervision; hash-chain format; vault (AES-256-GCM envelope,
  off by default, master key on same disk, only index cache sealed); TLS/mTLS opt-in, local CA,
  unencrypted keys, no cert→principal mapping; Ed25519-signed model packages (verify manager, import
  admin, small TOCTOU); upload default CONFIDENTIAL; escalation config knobs; `secret.key` location.

### docs/SOLUTION_CONTEXT.md — broad and mostly right, but overstates LLM/Neo4j
Verified: pipeline, Neo4j-stub status, PBKDF2 240k, lockout, token digests/TTL, HMAC grants,
release gate, block/event/task types, replan budgets, unload times, hardware table, §21 subsystems.
- §9 L455, §21 L1082 16 agents → 17. §19 L1017 `bench --verbose` fails. §20 L1065 default is `auto`.
- §14.1 L741 session path; §17 L968 upload path; §17 L973 uploads *are* tagged CONFIDENTIAL.
- §6.1 L331 1,119 vs KL 1,084 sections; L340 38 → 20 cross refs; L324 parse has a WARN (19
  reading-order inversions); §15 L795 90-120 s → config says ~50-100 s.
- **Overstated:** LLM extraction shown as part of the operating pipeline (0 output ever); §6.1 graph
  figures from an old snapshot; §15 LLM-extraction and reranker on gpu_4gb; §13.8 low confidence sets
  `needs_review` not `requires_human_review`; §16.1 only pulls deepseek-r1:7b.
- Stale: §19 benchmark/test figures; header date; §8 counts; §17 image handling; §4 repo map.
- Missing: September dirs in repo map, ~27 CLI commands, many API routes, 5 frontend routes
  (/models /sovereignty /tools /review /vault), 10 env vars and security-relevant defaults (air-gap
  and netmon ON, vault OFF, routing ON), model registry list, `access_denied` event, hash-chained
  logs + `audit-verify`, draft sign-off needs manager.
- Port 8000 in L870/882/885 vs frontend 8077.

### WebPage/README.md + .env.example — minor fixes
- L4 talks to api.py "and nothing else" → half the surface is api_ext.py.
- `VITE_WORKBENCH_URL` is **not** used by the proxy — it makes the browser call that URL directly
  (`api.ts:27`); `vite.config.ts` reads `process.env` without `loadEnv`. Production needs a reverse
  proxy stripping `/api` (undocumented).
- L20-25 omits `npm run lint` (broken). L33 `/` redirects to `/chat`; SignIn isn't a route.
- L34/127-129 upload accepts images too → OCR/vision intake + review draft.
- L40 export also available per answer in Chat. L41 package verify is manager+, import admin.
- Layout omits types_ext.ts, store/conversations.tsx, Bits, ErrorBoundary, LinkButton, RecentConversations.
- Undocumented UI: MFA step at sign-in, routed-models line, review badge, citations toggle, Tools
  attack presets and run log, Sovereignty auto-refresh, Knowledge branch editor.
- API wrappers never used by any page: reviews/decideReview (**no HITL review-queue screen**),
  requestAccess, intake, audit, agents. Backend routes with no UI: /vault/tls, /sovereignty/egress,
  /security-log, /schema, GET /runs, promote.
- Naming: "MRPL AI Workstation" vs "Refinery Engineering AI Workbench" elsewhere.
- `Chat.tsx:14-15` comment says conversations are in-memory; they're server-side.

### docs/manual/index.html — the most accurate doc
All cited tests, commands, endpoints, classes and sandbox defaults exist. Minor: env table omits
`RWB_KEEP_WARM`, `RWB_WARM_START`, `RKL_*`; `models` sample shows 4 of 7 models; egress allow-list
is loopback + private ranges (not "Ollama and Neo4j hosts"); pages don't call /vault/tls,
/sovereignty/egress, /intake; "raise sandbox.memory_mb in config" means editing `config.py`.

### workbench/fixtures/README.md — fine.
### `workbench/__init__.py` docstring — stale (`src`/`schemas`, mock backend).

---

## 3. Coverage gaps (code with no or near-no documentation)

- **knowledge_layer:** parse_validation (0 hits), table_context, document_graph, ontology_manager,
  reference_tracker, retriever, spec_claims, table_claims, rule_relations, entity_resolver,
  glossary_builder, table_reconstructor (one line each at most); all 9 `prompts/*.txt` (only 2 are
  used: `extractor.py:363,487`); 12 scripts (check_tables, inspect_*, probe_*, reset_phases,
  setup_neo4j, smoke_test, test_fixes, test_parse_window, test_parser_fix); config sections
  Normalizer/Chunker/Validation and most Parser/Identity/Ollama fields.
- **workbench:** security/totp, leakcheck, redteam internals; services kl_io, response_store,
  revision_resolver, tag_matcher, thinking_store, context_builder, evidence_store, audit_store;
  memory/followup; llm/fake; deliverables/*, intake/ocr, sovereignty/*, sandbox/runlog,
  tools/agent_loop and tool files (manual only); most Settings fields (Security 12/13,
  Sovereignty, ModelRouting, Sandbox, Governance, Retrieval k-values, Effort knobs, Paths).
- **Env var** `RKL_OLLAMA_URL` undocumented everywhere.
- **CLI:** deny, passwd, users, classify, request-access, requests, revoke-key, agents barely
  mentioned; flags `--reset-passwords`, `--no-injections`, `--verbose-probes`, `--reject`,
  `users --add/--role/--name`, `security-log --event/--principal`, `trace --show`,
  `bench --category/--limit/--llm`, `agent --max-iterations`, `export --out/--title`.

---

## 4. Inter-doc contradictions to resolve

| Topic | Values found | Truth |
|---|---|---|
| Tests | 49 / 94 / 132 / 166 / 289 | 665 |
| Benchmark | 62 (HOW_IT_WORKS, PLAN, SOLUTION_CONTEXT, HANDOFF, DEMO) / 70 (README, ACCESS) | 70 |
| Pipeline order | walkthrough ≠ README | README (table classification before normalize) |
| LLM at default effort | README/DEMO/HOW_IT_WORKS: none | medium calls composer etc. |
| Backends | "mock/neo4j" (README tree, PLAN) vs "files" | auto → files/mock; neo4j stub |
| Repo layout | `src/`, `schemas/`, `Refinery_KL_Extraction` | `knowledge_layer/`, `Refinery` |
| Context window | 4096 vs 8192 | KL 8192; workbench gpu_4gb 4096 |
| Models to pull | deepseek-r1:7b only | + qwen3:4b, qwen3.5:2b |
| Neo4j setup | walkthrough vs SETUP | SETUP (standalone neo4j-data/) |
| serve port | 8000 (HANDOFF, SOLUTION_CONTEXT) vs 8077 | default 8000, frontend needs 8077 |
| Product name | MRPL AI Workstation vs Refinery Engineering AI Workbench | pick one |

---

## 5. Repo hygiene

- `response.json` (root): not JSON — a UTF-16 console capture; unreferenced. Delete.
- `workbench/a5983fcc-3857-4b66-85e4-6e6dc5890426.png`: duplicate of `docs/architecture/agent_workflow.png`. Delete.
- `WebPage/node_modules` (2,898 files, 132 MB) committed; no `node_modules/` in `.gitignore`. Remove from git and ignore.
- `WebPage/*.tsbuildinfo` committed build caches. Ignore.
- `data/reports/pipeline_run*_stdout/stderr.log` tracked despite `.gitignore:45`.
- `data/failures/*.json`, `data/knowledge/smoke_test/`, `data/knowledge/test/` committed, undocumented.
- `docs/webpagescript.md` deletion uncommitted.
