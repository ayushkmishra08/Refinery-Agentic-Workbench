/**
 * What an answer was built from, and what was kept back.
 *
 * Every released answer carries a classification the way a printed document carries one in its
 * footer, so an engineer can see at a glance whether what is on screen may be forwarded. When
 * material above the caller's clearance bears on the question, the same strip says so — how much
 * exists and which document it is in, never what it says — and offers the one route through:
 * an access request, routed to the lowest role that can release it.
 *
 * All of this is read from `response.security`, which the workbench fills in from the access
 * decision. Nothing here is inferred from the answer text.
 */
import { KeyRound, Lock, Paperclip, ShieldCheck, ShieldX } from "lucide-react";

import { cn } from "@/lib/cn";
import type { SecurityEnvelope } from "@/lib/types";
import { ROLE_TITLE, TAG_TONE } from "@/store/auth";
import { Badge, Button } from "@/ui";

/** The classification strip under an answer. */
export function ClassificationStrip({ security }: { security: SecurityEnvelope }) {
  if (!security.access_control) return null;

  if (security.release_blocked) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.07] px-3 py-2">
        <ShieldX className="size-4 shrink-0 text-[var(--danger)]" />
        <p className="text-xs text-slate-700">
          <span className="font-semibold text-[var(--danger)]">Answer withheld.</span>{" "}
          Part of it traced back to material you are not cleared to read, so the whole response was
          withheld rather than redacted. This was recorded as a security event.
        </p>
      </div>
    );
  }

  const attached = (security.attached_documents ?? []).filter((d) => security.source_documents.includes(d));
  if (!security.classification && !security.grant_id && !attached.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.7rem] text-muted-foreground">
      <ShieldCheck className="size-3.5 text-[var(--success)]" />
      {security.classification ? (
        <Badge tone={TAG_TONE[security.classification]} title="Highest classification among the documents this answer used">
          {security.classification}
        </Badge>
      ) : null}
      {attached.length ? (
        <Badge tone="neutral" title="Uploaded into this conversation. Not in the knowledge layer, and nobody else can read it here.">
          <Paperclip className="size-3" /> attachment
        </Badge>
      ) : null}
      {security.source_documents.length ? (
        <span className="truncate">built from {security.source_documents.join(", ")}</span>
      ) : null}
      <span>· released to {security.principal} ({ROLE_TITLE[security.role]})</span>
      {security.grant_id ? (
        <Badge tone="primary" title={`${security.released_records} record(s) opened by an approved key`}>
          <KeyRound className="size-3" /> grant {security.grant_id}
        </Badge>
      ) : null}
    </div>
  );
}

/**
 * Shown when restricted material bears on the question.
 *
 * It states the *size and location* of what was withheld and nothing else — "6 chunks, 7 claims
 * in CDU operating manual" tells the engineer the question is answerable without telling them the
 * answer. The request is usually already raised by the time this renders, because retyping a
 * question you have just been refused is the kind of friction that makes people stop asking.
 */
/**
 * A key was sent and refused. Without this the question just answers thinly and the person is left
 * to work out that their key never applied — a stale key and a genuinely undocumented value look
 * identical on screen.
 */
export function KeyRefusedNotice({ security, className }: { security: SecurityEnvelope; className?: string }) {
  if (!security.access_key_error) return null;
  return (
    <div className={cn("rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.06] px-3.5 py-2.5", className)} role="alert">
      <p className="text-sm font-semibold text-foreground">That access key was not used</p>
      <p className="mt-0.5 text-xs text-slate-600">
        {security.access_key_error} The question was answered at your own clearance instead.
      </p>
    </div>
  );
}

export function WithheldNotice({
  security, onTrack, className,
}: { security: SecurityEnvelope; onTrack?: (requestId: string) => void; className?: string }) {
  if (!security.access_control || !security.withheld_documents.length) return null;
  if (security.grant_id) return null;   // it was released; nothing is being withheld now

  return (
    <div className={cn("rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/[0.07] px-3.5 py-3", className)}>
      <div className="flex gap-2.5">
        <Lock className="mt-0.5 size-4 shrink-0 text-[var(--warning)]" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">Material you are not cleared for bears on this question</p>
          <p className="mt-1 text-xs text-slate-600">
            {security.withheld_summary || `Restricted material exists in ${security.withheld_documents.join(", ")}`}.
            {" "}Nothing from it is included above.
          </p>

          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {security.withheld_documents.map((doc) => (
              <Badge key={doc} tone="warning" title="You are not cleared for this document">{doc}</Badge>
            ))}
            {security.escalation_target ? (
              <span className="text-[0.7rem] text-muted-foreground">
                releasable by {ROLE_TITLE[security.escalation_target]}
              </span>
            ) : null}
          </div>

          {security.access_request_id ? (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-white/70 px-2 py-1 font-mono text-[0.7rem] font-semibold text-foreground">
                {security.access_request_id}
              </span>
              <span className="text-[0.7rem] text-muted-foreground">
                An access request has been raised for you.
              </span>
              {onTrack ? (
                <Button size="sm" variant="outline" onClick={() => onTrack(security.access_request_id!)}>
                  Track request
                </Button>
              ) : null}
            </div>
          ) : (
            <p className="mt-2 text-[0.7rem] text-muted-foreground">
              Ask {security.escalation_target ? ROLE_TITLE[security.escalation_target].toLowerCase() : "an approver"} to
              release it from the Access page.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** The clearance chip in the top bar: role, tiers, and how many documents that opens. */
export function ClearanceChip({
  role, tags, readable, withheld,
}: { role: keyof typeof ROLE_TITLE; tags: string[]; readable: number; withheld: number }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border/70 bg-white/60 px-2.5 py-1.5">
      <ShieldCheck className="size-3.5 text-primary" />
      <div className="leading-tight">
        <p className="text-[0.7rem] font-semibold text-foreground">{ROLE_TITLE[role]}</p>
        <p className="text-[0.62rem] text-muted-foreground">
          {tags.join(" · ") || "no clearance"} — {readable} readable{withheld ? `, ${withheld} withheld` : ""}
        </p>
      </div>
    </div>
  );
}
