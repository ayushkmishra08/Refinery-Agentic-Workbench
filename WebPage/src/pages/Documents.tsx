/**
 * What is loaded, how each document is classified, and which role opens it.
 *
 * Documents above the caller's clearance are listed but locked. Showing the *name* of a document
 * you cannot read is a deliberate choice: it is how an engineer knows the question is answerable
 * and what to request. Nothing about the contents — not a page, not a title beyond the one on the
 * cover — is available from this page for a locked document.
 *
 * The rule under the table is one comparison, and it is worth stating plainly, because "why can't
 * I see that" is the first question every new user asks.
 */
import { FileText, Lock, Unlock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SecurityOverview } from "@/lib/types";
import { ROLE_TITLE, TAG_TONE } from "@/store/auth";
import { Badge, ErrorState, Input, PageHeader, Panel, Skeleton } from "@/ui";

export default function Documents() {
  const [overview, setOverview] = useState<SecurityOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = async () => {
    setError(null);
    try {
      setOverview(await api.security());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  useEffect(() => { void load(); }, []);

  const rows = useMemo(() => {
    if (!overview) return [];
    const readable = new Set(overview.you.readable_documents);
    const needle = query.trim().toLowerCase();
    return overview.documents
      .map((d) => ({ ...d, readable: readable.has(d.document_id) }))
      .filter((d) => !needle || `${d.document_id} ${d.title} ${d.tag}`.toLowerCase().includes(needle))
      .sort((a, b) => Number(b.readable) - Number(a.readable) || a.document_id.localeCompare(b.document_id));
  }, [overview, query]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!overview) return <Skeleton className="h-64 w-full" />;

  const readableCount = rows.filter((r) => r.readable).length;

  return (
    <div>
      <PageHeader
        title="Documents"
        description={`${readableCount} of ${rows.length} readable by you. A document is read either by every role at or above its classification, or — where one is pinned — by exactly the roles named on it.`}
        actions={
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter…"
            className="h-8 w-48 text-xs"
            aria-label="Filter documents"
          />
        }
      />

      <div className="space-y-2">
        {rows.map((doc) => (
          <Panel
            key={doc.document_id}
            className={cn("px-4 py-3", !doc.readable && "border-dashed opacity-90")}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 gap-3">
                <span className={cn("mt-0.5 shrink-0", doc.readable ? "text-primary" : "text-muted-foreground")}>
                  {doc.readable ? <Unlock className="size-4" /> : <Lock className="size-4" />}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">{doc.title || doc.document_id}</p>
                  <p className="truncate font-mono text-[0.68rem] text-muted-foreground">{doc.document_id}</p>
                  {doc.reason ? <p className="mt-1 text-xs text-muted-foreground">{doc.reason}</p> : null}
                </div>
              </div>

              <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                {doc.readable && doc.pages ? (
                  <span className="text-[0.68rem] text-muted-foreground">{doc.pages} pages</span>
                ) : null}
                <Badge tone={TAG_TONE[doc.tag]}>{doc.tag}</Badge>
                <Badge tone="neutral" title="Every role that may read this document">
                  {doc.compartmented
                    ? (doc.roles ?? []).map((r) => ROLE_TITLE[r]).join(", ") || "nobody"
                    : `${ROLE_TITLE[doc.min_role]} and above`}
                </Badge>
                {doc.compartmented ? (
                  <Badge tone="neutral" title="This document names its readers outright rather than following the tag ladder.">
                    compartment
                  </Badge>
                ) : null}
                {doc.readable
                  ? <Badge tone="success">readable</Badge>
                  : <Badge tone="warning">not yours to read</Badge>}
              </div>
            </div>

            {!doc.readable ? (
              <p className="mt-2 rounded-md bg-[var(--warning)]/[0.06] px-2.5 py-1.5 text-[0.7rem] text-slate-600">
                Nothing in this document is searched or shown for your role, and its size is not
                reported. Ask your question anyway — the workstation will tell you how much of it
                bears on the question, and raise a request with{" "}
                {(doc.roles ?? [doc.min_role]).map((r) => ROLE_TITLE[r]).join(" or ")}.
              </p>
            ) : null}
          </Panel>
        ))}

        {!rows.length ? (
          <Panel className="px-4 py-8 text-center">
            <FileText className="mx-auto mb-2 size-6 text-muted-foreground/60" />
            <p className="text-sm text-muted-foreground">No document matches that filter.</p>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}
