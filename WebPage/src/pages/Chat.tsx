/**
 * The chat workspace: ask a question, watch the agents work, read the answer.
 *
 * Everything the terminal offers is here as something you can click:
 *
 * * the phase-by-phase reasoning, folded into one "Thinking…" line that expands;
 * * `btw` — ask what the agents are doing *while* they are doing it, without interrupting them;
 * * effort, from index-only in milliseconds to model-refined plans;
 * * attaching a PDF, which is parsed and added to this conversation;
 * * the access route: when an answer is short because material sits above your clearance, the
 *   request is already raised and the key, once approved, is one click from re-asking.
 *
 * A note on state: conversations are held in memory for the life of the tab. That was a
 * requirement, and it suits a shared terminal — closing the tab ends the conversation.
 */
import {
  ArrowUp, FileUp, Gauge, MessageCircleQuestion, Paperclip, RefreshCw, Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { AnswerBlock, EvidenceList } from "@/components/AnswerBlocks";
import { Markdown } from "@/components/Markdown";
import { ClassificationStrip, KeyRefusedNotice, WithheldNotice } from "@/components/SecurityBanner";
import { ThinkingTrace } from "@/components/ThinkingTrace";
import { ApiError, api, streamRun } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ms } from "@/lib/format";
import { REASONING_BLOCKS, type Block, type Effort, type FinalResponse, type ProgressEvent, type StoredTurn } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { announceConversationsChanged, newConversationId, recallConversation, rememberConversation } from "@/store/conversations";
import { Badge, Button, EmptyState, Panel, ProgressBar, Spinner, Textarea, useToast } from "@/ui";

interface Turn {
  id: string;
  question: string;
  events: ProgressEvent[];
  response: FinalResponse | null;
  error: string | null;
  running: boolean;
  startedAt: number;
  elapsedMs: number;
  runId: string | null;
  usedKey: string | null;
  /** answers to "btw" asked while this turn was running */
  asides: { question: string; blocks: Block[] }[];
  /** Redrawn from the server's record of an earlier visit: no live trace, no key affordance. */
  restored?: boolean;
}

/**
 * The server keeps the released answer and its security envelope for every exchange. That is
 * enough to redraw the exchange as it was — the prose, the classification banner, what was
 * withheld — without the blocks, the trace or the evidence list, which are not stored. The result
 * is shaped like a FinalResponse so the same card draws both; the fields the card never reads for
 * a restored turn are left at their empty defaults.
 */
function restoredResponse(t: StoredTurn): FinalResponse {
  const security = {
    access_control: true, principal: "", role: "guest", authenticated: true, classification: null,
    source_documents: [], readable_documents: [], withheld_documents: [], withheld_summary: "",
    withheld_records: 0, escalation_target: null, access_request_id: null, grant_id: null,
    released_records: 0, release_blocked: false, attached_documents: [],
    ...(t.security ?? {}),
  };
  return {
    response_id: t.response_id ?? "", session_id: "", created_at: new Date((t.ts || 0) * 1000).toISOString(),
    task_type: t.task_type ?? "lookup", secondary_task_types: [], status: t.status ?? "answered",
    answer_markdown: t.answer_markdown || t.answer_preview || "", blocks: [], evidence: [],
    confidence: { score: 0, level: "low", basis: "", uncertainties: [] }, safety_flags: [],
    requires_human_review: false, review_reason: null, plan: null, warnings: [],
    audit_trail_id: "", timing_ms: 0, llm_calls: 0, backend: "", security,
  } as unknown as FinalResponse;
}

const EFFORTS: { id: Effort; label: string; hint: string }[] = [
  { id: "low", label: "Low", hint: "Indexes only, no model. Fastest." },
  { id: "medium", label: "Medium", hint: "Default: vector retrieval, model only when the rules are unsure." },
  { id: "high", label: "High", hint: "Wider retrieval, reranker, model-written prose." },
  { id: "ultra", label: "Ultra", hint: "Everything on. Minutes per question on a small GPU." },
];

/** The ingest phases the API reports, in words worth putting in front of someone waiting. */
const PHASE_WORDS: Record<string, string> = {
  "ingest": "reading the document",
  "ingest:parsing": "reading the pages — a document seen for the first time can take a few minutes",
  "ingest:normalizing": "recovering headings, tables and sections",
  "ingest:indexing": "indexing passages, equipment and values",
};

const SUGGESTIONS = [
  "What are all the equipments in the refinery?",
  "What is the normal flow rate of the crude charge pump?",
  "What is the recommended way to start the CDU?",
  "The crude charge pump discharge pressure is dropping. What should I check?",
];

let turnCounter = 0;

export default function Chat() {
  const { session } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [effort, setEffort] = useState<Effort>("medium");
  const [aside, setAside] = useState("");
  const [askingAside, setAskingAside] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [ingest, setIngest] = useState<{ name: string; phase: string; percent: number | null; startedAt: number } | null>(null);
  const [ingestElapsed, setIngestElapsed] = useState(0);
  const [attachments, setAttachments] = useState<string[]>([]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // One conversation per id, the id in the URL. A new tab or a click in the sidebar changes it;
  // the server namespaces it under the signed-in user, so two people never share one by accident.
  // With no id in the URL, go back to the one this tab had open, or start a fresh one.
  const [searchParams, setSearchParams] = useSearchParams();
  const username = session?.username ?? "guest";
  const sessionId = searchParams.get("c") ?? "";
  useEffect(() => {
    if (sessionId) { rememberConversation(username, sessionId); return; }
    const next = recallConversation(username) ?? newConversationId();
    setSearchParams({ c: next }, { replace: true });
  }, [sessionId, username, setSearchParams]);

  // Redraw the conversation from what the server kept, and pick up its attachments. Runs when the
  // id changes, and after a reload — the turns are the server's, not this tab's.
  const [restoring, setRestoring] = useState(false);
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    stopRef.current?.();
    setTurns([]);
    setAttachments([]);
    setRestoring(true);
    api.conversation(sessionId)
      .then((snap) => {
        if (cancelled) return;
        setTurns(snap.turns.map((t, i) => ({
          id: `restored-${i}-${t.ts}`, question: t.request, events: [], response: restoredResponse(t),
          error: null, running: false, startedAt: (t.ts || 0) * 1000, elapsedMs: 0, runId: null,
          usedKey: null, asides: [], restored: true,
        })));
        const attached = snap.attached_documents?.length
          ? snap.attached_documents
          : snap.uploaded_documents.filter((d) => d.kind === "pdf").map((d) => d.document_id);
        setAttachments(attached);
      })
      .catch(() => { /* a brand-new id has nothing yet; that is not an error */ })
      .finally(() => { if (!cancelled) setRestoring(false); });
    return () => { cancelled = true; };
  }, [sessionId]);
  const running = turns.some((t) => t.running);
  /** A document is being read; questions have to wait for it or they will not see it. */
  const ingesting = uploading || ingest !== null;
  const activeTurn = turns.find((t) => t.running) ?? null;

  useEffect(() => () => stopRef.current?.(), []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, activeTurn?.events.length]);

  // Pages arrive a window at a time, so the bar can hold one value for a minute or more. Without
  // a clock moving beside it a correct, patient parse is indistinguishable from a hung one.
  useEffect(() => {
    if (!ingest) { setIngestElapsed(0); return; }
    const id = window.setInterval(() => setIngestElapsed(Date.now() - ingest.startedAt), 500);
    return () => window.clearInterval(id);
  }, [ingest]);

  // a ticking elapsed counter for the running turn only
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      setTurns((prev) => prev.map((t) => (t.running ? { ...t, elapsedMs: Date.now() - t.startedAt } : t)));
    }, 200);
    return () => window.clearInterval(id);
  }, [running]);

  const patch = useCallback((id: string, update: Partial<Turn> | ((t: Turn) => Partial<Turn>)) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...(typeof update === "function" ? update(t) : update) } : t)));
  }, []);

  const runQuestion = useCallback(
    async (question: string, accessKey?: string) => {
      const text = question.trim();
      // Asking before the document is in gets an answer built from everything *except* it, which
      // reads as a confident non sequitur — the "random output" this used to produce. The composer
      // is disabled while a parse runs, and this is the matching guard for Enter and for any other
      // route into here.
      if (!text || running || ingesting) return;

      const id = `turn-${++turnCounter}`;
      setTurns((prev) => [...prev, {
        id, question: text, events: [], response: null, error: null, running: true,
        startedAt: Date.now(), elapsedMs: 0, runId: null, usedKey: accessKey ?? null, asides: [],
      }]);
      setDraft("");

      try {
        const { run_id } = await api.startRun({
          text, session_id: sessionId,
          access_key: accessKey ?? null,
          options: { effort },
        });
        patch(id, { runId: run_id });

        stopRef.current = streamRun(run_id, {
          onEvent: (ev) => patch(id, (t) => ({ events: [...t.events, ev] })),
          onFinal: async () => {
            announceConversationsChanged();
            try {
              const state = await api.getRun(run_id);
              patch(id, {
                response: state.final ?? null,
                error: state.final ? null : state.error ?? "The run finished without an answer.",
                running: false,
                elapsedMs: state.final?.timing_ms ?? Date.now(),
              });
            } catch (err) {
              patch(id, { running: false, error: err instanceof ApiError ? err.message : String(err) });
            }
            stopRef.current?.();
            stopRef.current = null;
          },
          onError: (err) => {
            patch(id, { running: false, error: err.message });
            stopRef.current = null;
          },
        });
      } catch (err) {
        patch(id, { running: false, error: err instanceof ApiError ? err.message : String(err) });
      }
    },
    [effort, ingesting, patch, running, sessionId],
  );

  const askAside = useCallback(async () => {
    const text = aside.trim();
    if (!text) return;
    setAskingAside(true);
    try {
      const target = turns.find((t) => t.running) ?? turns[turns.length - 1];
      const out = await api.btw(text, target?.runId ? { runId: target.runId } : { sessionId });
      // the label already reads "btw", so a question typed as "btw what are you doing" should not
      // come back as "btw — btw what are you doing"
      const asked = text.replace(/^btw\b[\s,:;-]*/i, "").trim() || text;
      if (target) patch(target.id, (t) => ({ asides: [...t.asides, { question: asked, blocks: out.blocks }] }));
      setAside("");
    } catch (err) {
      toast.push({ tone: "danger", message: "Could not ask that", detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setAskingAside(false);
    }
  }, [aside, patch, sessionId, toast, turns]);

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      setIngest(null);
      try {
        const out = await api.upload(file, sessionId);
        const name = out.document_id || out.filename || file.name;

        // A PDF is parsed in a background thread and that takes anywhere from a couple of seconds
        // to several minutes on a first sight of the file. Saying "added" the moment the bytes
        // land — which is what this used to do — sends people off to ask a question about a
        // document the workstation has not read yet, and the answer comes back empty. So the run
        // is followed to its end and the composer stays disabled until the document is really in.
        if (out.status === "indexing" && out.run_id) {
          const startedAt = Date.now();
          setIngest({ name, phase: "opening the document", percent: null, startedAt });
          for (;;) {
            await new Promise((r) => setTimeout(r, 1200));
            const run = await api.ingestStatus(out.run_id);
            const p = run.progress;
            const counted = p && p.total ? `${p.done} of ${p.total} ${p.unit || "pages"}` : "";
            setIngest({
              name,
              phase: counted || PHASE_WORDS[run.phase ?? ""] || "opening the document",
              percent: typeof p?.percent === "number" ? p.percent : null,
              startedAt,
            });
            if (run.finished) {
              if (run.final_status === "indexed") break;
              throw new ApiError(0, run.error || "The document could not be indexed.");
            }
          }
        }

        setAttachments((prev) => (prev.includes(name) ? prev : [...prev, name]));
        announceConversationsChanged();
        toast.push({
          tone: "success",
          message: `${name} is ready`,
          detail: out.kind === "image"
            ? "The image was described and added to this conversation."
            : "It is readable in this conversation only — ask about it now. It is not added to the knowledge layer.",
        });
      } catch (err) {
        toast.push({ tone: "danger", message: "Upload failed", detail: err instanceof ApiError ? err.message : String(err) });
      } finally {
        setIngest(null);
        setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    },
    [sessionId, toast],
  );

  /** Drop this conversation's uploads. They were never in the knowledge layer, so nothing else changes. */
  const dropUploads = useCallback(async () => {
    try {
      await api.dropUploads(sessionId);
      setAttachments([]);
      announceConversationsChanged();
      toast.push({ tone: "info", message: "Removed", detail: "Those documents are no longer part of this conversation." });
    } catch (err) {
      toast.push({ tone: "danger", message: "Could not remove them", detail: err instanceof ApiError ? err.message : String(err) });
    }
  }, [sessionId, toast]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void runQuestion(draft);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* ---------------------------------------------------------------- transcript */}
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto px-4 py-5 lg:px-8">
        <div className="mx-auto w-full max-w-3xl space-y-6">
          {!turns.length && !restoring ? (
            <div className="pt-6">
              <EmptyState
                icon={<MessageCircleQuestion className="size-8" />}
                title="Ask the plant documentation a question"
                description="Answers are written from the documents your role may read, and every value carries the page it came from. If something sits above your clearance, you will be told it exists — never what it says — and offered the route to request it."
              />
              <div className="mx-auto mt-2 grid max-w-xl gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => void runQuestion(s)}
                    disabled={running || ingesting}
                    className="rounded-lg border border-border/70 bg-white/55 px-3.5 py-2.5 text-left text-sm text-slate-700 transition-colors hover:border-primary/40 hover:bg-white disabled:pointer-events-none disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {turns.map((turn) => (
            <TurnView
              key={turn.id}
              turn={turn}
              onRetryWithKey={(key) => void runQuestion(turn.question, key)}
              onTrackRequest={() => navigate("/access")}
              onPickOption={(option) => void runQuestion(option)}
            />
          ))}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* ---------------------------------------------------------------- composer */}
      <div className="border-t border-border/70 bg-white/55 px-4 py-3 backdrop-blur lg:px-8">
        <div className="mx-auto w-full max-w-3xl space-y-2">
          {ingest ? (
            <div className="space-y-1.5 rounded-lg border border-primary/25 bg-primary/[0.06] px-2.5 py-2" aria-live="polite">
              <div className="flex items-center gap-2">
                <FileUp className="size-3.5 shrink-0 text-primary" />
                <p className="min-w-0 flex-1 truncate text-xs text-slate-700">
                  <span className="font-medium text-foreground">Parsing {ingest.name}</span> — {ingest.phase}
                </p>
                <span className="shrink-0 font-mono text-[0.7rem] text-muted-foreground">{ms(ingestElapsed)}</span>
                {ingest.percent !== null ? (
                  <span className="shrink-0 font-mono text-[0.7rem] text-primary">{ingest.percent}%</span>
                ) : null}
              </div>
              <ProgressBar percent={ingest.percent} label={`Parsing ${ingest.name}`} />
              <p className="text-[0.68rem] text-muted-foreground">
                Questions wait until it is read — an answer given now would not include this document.
              </p>
            </div>
          ) : null}

          {attachments.length ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[0.7rem] text-muted-foreground">In this conversation only:</span>
              {attachments.map((a) => (
                <Badge key={a} tone="primary"><Paperclip className="size-2.5" /> {a}</Badge>
              ))}
              <button
                type="button"
                onClick={() => void dropUploads()}
                className="text-[0.7rem] text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                remove
              </button>
            </div>
          ) : null}

          {running ? (
            <div className="flex items-center gap-2 rounded-lg border border-border/70 bg-white/70 px-2.5 py-1.5">
              <MessageCircleQuestion className="size-3.5 shrink-0 text-primary" />
              <input
                value={aside}
                onChange={(e) => setAside(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void askAside(); } }}
                placeholder="btw — ask what it is doing right now, without interrupting it"
                className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground/80"
              />
              <Button size="sm" variant="ghost" loading={askingAside} disabled={!aside.trim()} onClick={() => void askAside()}>
                Ask
              </Button>
            </div>
          ) : null}

          <div className="flex items-end gap-2">
            <div className="relative min-w-0 flex-1">
              <Textarea
                ref={textareaRef}
                rows={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder={
                  ingesting ? `Reading ${ingest?.name ?? "the document"}…`
                    : running ? "Working on the current question…"
                    : "Ask about equipment, procedures, limits, safety…"
                }
                disabled={running || ingesting}
                className="max-h-40 min-h-[2.75rem] py-3 pr-11"
              />
              <Button
                size="icon"
                variant="primary"
                className="absolute bottom-1.5 right-1.5 size-8"
                disabled={!draft.trim() || running || ingesting}
                onClick={() => void runQuestion(draft)}
                aria-label="Send question"
              >
                {running ? <Spinner className="size-3.5" /> : <ArrowUp className="size-4" />}
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <Gauge className="size-3.5 text-muted-foreground" />
              <div className="flex gap-0.5 rounded-lg bg-slate-900/[0.05] p-0.5">
                {EFFORTS.map((e) => (
                  <button
                    key={e.id}
                    title={e.hint}
                    onClick={() => setEffort(e.id)}
                    disabled={running}
                    className={cn(
                      "rounded-md px-2 py-1 text-[0.68rem] font-medium transition-colors disabled:opacity-50",
                      effort === e.id ? "bg-white text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {e.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.png,.jpg,.jpeg"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }}
              />
              <Button size="sm" variant="ghost" loading={uploading} disabled={running || ingesting}
                      onClick={() => fileRef.current?.click()}>
                <FileUp className="size-3.5" /> Attach PDF
              </Button>
              {turns.length ? (
                <Button size="sm" variant="ghost" disabled={running || ingesting} onClick={() => setTurns([])}>
                  <Trash2 className="size-3.5" /> Clear
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- one exchange

function TurnView({
  turn, onRetryWithKey, onTrackRequest, onPickOption,
}: {
  turn: Turn; onRetryWithKey: (key: string) => void;
  onTrackRequest: () => void; onPickOption: (option: string) => void;
}) {
  const [keyDraft, setKeyDraft] = useState("");
  const [showEvidence, setShowEvidence] = useState(false);
  const response = turn.response;

  // The backend also states the escalation in prose, worded for a terminal ("release it with
  // `workbench approve AR-…`"). WithheldNotice says the same thing with buttons, so the callout
  // is dropped here rather than shown twice in two registers.
  const visibleBlocks = useMemo(
    () => (response?.blocks ?? []).filter(
      (b) => !REASONING_BLOCKS.has(b.type)
        && b.type !== "evidence"
        && b.id !== "verification"
        && b.id !== "escalation",
    ),
    [response],
  );

  return (
    <article className="space-y-3">
      {/* the question */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-primary-foreground shadow-sm">
          {turn.question}
          {turn.usedKey ? (
            <span className="mt-1 block text-[0.65rem] opacity-80">re-asked with an approved access key</span>
          ) : null}
        </div>
      </div>

      {/* the reasoning — a restored exchange has none to show; the trace was not kept */}
      {turn.restored ? null : (
        <ThinkingTrace events={turn.events} running={turn.running} elapsedMs={turn.elapsedMs} />
      )}

      {/* anything asked while it ran */}
      {turn.asides.map((a, i) => (
        <div key={i} className="rounded-lg border border-border/70 bg-[var(--info)]/[0.05] px-3.5 py-2.5">
          <p className="mb-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-[var(--info)]">
            btw — {a.question}
          </p>
          <div className="space-y-2">
            {a.blocks.map((b, j) => <AnswerBlock key={j} block={b} />)}
          </div>
        </div>
      ))}

      {turn.error ? (
        <div role="alert" className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.06] px-3.5 py-3">
          <p className="text-sm font-medium text-[var(--danger)]">The question could not be answered</p>
          <p className="mt-1 text-xs text-slate-600">{turn.error}</p>
        </div>
      ) : null}

      {/* the answer */}
      {response ? (
        <Panel className="px-4 py-3.5">
          <div className="space-y-4">
            {visibleBlocks.length
              ? visibleBlocks.map((b, i) => (
                  <AnswerBlock key={`${b.id}-${i}`} block={b} onPickOption={onPickOption} />
                ))
              : <Markdown>{response.answer_markdown}</Markdown>}
          </div>

          <KeyRefusedNotice security={response.security} className="mt-4" />
          <WithheldNotice security={response.security} className="mt-4" onTrack={onTrackRequest} />

          {/* re-ask with an approved key */}
          {!turn.restored && response.security.withheld_documents.length && !response.security.grant_id ? (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-border/70 bg-white/55 px-3 py-2">
              <span className="text-[0.7rem] text-muted-foreground">Have an approved key?</span>
              <input
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                placeholder="RGK.G-…"
                className="min-w-0 flex-1 rounded-md border border-border bg-white/80 px-2 py-1 font-mono text-[0.7rem] outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <Button size="sm" variant="primary" disabled={!keyDraft.trim().startsWith("RGK.")}
                      onClick={() => onRetryWithKey(keyDraft.trim())}>
                <RefreshCw className="size-3" /> Re-ask with key
              </Button>
            </div>
          ) : null}

          {/* footer: citations, review flag, classification */}
          <div className="mt-4 space-y-2 border-t border-border/70 pt-3">
            <div className="flex flex-wrap items-center gap-2">
              {response.requires_human_review ? (
                <Badge tone="warning" title={response.review_reason ?? undefined}>human review required</Badge>
              ) : null}
              {response.status !== "answered" ? <Badge tone="neutral">{response.status}</Badge> : null}
              {response.evidence.length ? (
                <button
                  onClick={() => setShowEvidence((v) => !v)}
                  className="text-[0.7rem] font-medium text-primary underline-offset-2 hover:underline"
                >
                  {showEvidence ? "Hide" : "Show"} {response.evidence.length} citation{response.evidence.length === 1 ? "" : "s"}
                </button>
              ) : null}
              <span className="ml-auto text-[0.68rem] text-muted-foreground">
                {ms(response.timing_ms)}{response.llm_calls ? ` · ${response.llm_calls} model call${response.llm_calls === 1 ? "" : "s"}` : ""}
              </span>
            </div>

            {response.review_reason ? (
              <p className="text-[0.7rem] text-muted-foreground">{response.review_reason}</p>
            ) : null}

            {showEvidence ? <EvidenceList items={response.evidence} /> : null}

            <ClassificationStrip security={response.security} />
          </div>
        </Panel>
      ) : null}
    </article>
  );
}
