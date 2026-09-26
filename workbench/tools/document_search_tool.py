"""search_documents: the internal knowledge base, through the guarded service.

The tool receives the *guarded* KnowledgeService the orchestrator built for the session, so a
branch the caller may not read was pruned before this tool could ask — the tool cannot widen
access, whatever the model asks it for. Every hit is returned as evidence with document, page
and record id so a draft built from it links back to its source.
"""
from __future__ import annotations

from workbench.tools.base import Tool, ToolContext, ToolError, ToolResult


class SearchDocumentsTool(Tool):
    name = "search_documents"
    description = ("Search the organisation's own manuals, SOPs, standards and correspondence (the local knowledge "
                   "base). kind='chunks' returns passages, 'claims' documented values, 'procedures' step lists. "
                   "Only documents the caller is cleared for are searched.")
    parameters = {"type": "object",
                  "properties": {"query": {"type": "string"}, "k": {"type": "integer"},
                                 "kind": {"type": "string", "description": "chunks | claims | procedures"}},
                  "required": ["query"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        if ctx.knowledge is None:
            raise ToolError("no knowledge service is attached to this session; nothing can be searched")
        k = max(1, min(int(args.get("k") or 6), 50))
        kind = (args.get("kind") or "chunks").lower()
        query = args["query"].strip()
        if not query:
            raise ToolError("query is empty")
        evidence: list[dict] = []
        lines: list[str] = []
        if kind == "claims":
            for c in ctx.knowledge.search_claims(text=query, limit=k) or []:
                text = f"{c.subject}: {c.predicate} = {c.value}{(' ' + c.unit) if c.unit else ''}"
                ctxs = ", ".join(x for x in (c.parameter_role, c.location, c.scenario) if x)
                evidence.append({"document_id": c.document_id, "page": c.page, "claim_id": c.claim_id, "chunk_id": c.chunk_id,
                                 "text": text + (f" [{ctxs}]" if ctxs else ""), "kind": "claim", "source": c.source,
                                 "evidence": (c.evidence or "")[:300]})
                lines.append(f"- {text}{(' [' + ctxs + ']') if ctxs else ''} ({c.document_id} p.{c.page})")
        elif kind == "procedures":
            for p in ctx.knowledge.procedures(query=query, limit=k) or []:
                steps = [s.text for s in (p.steps or [])]
                text = f"{p.title} ({p.procedure_type}, {len(steps)} steps)"
                evidence.append({"document_id": p.document_id, "page": p.page_start, "procedure_id": p.procedure_id,
                                 "text": text, "kind": "procedure", "steps": steps[:30], "section_path": p.section_path})
                lines.append(f"- {text} ({p.document_id} p.{p.page_start})")
                for i, s in enumerate(steps[:8], start=1):
                    lines.append(f"    {i}. {s[:160]}")
        elif kind == "chunks":
            for ch in ctx.knowledge.search_chunks(query, k=k) or []:
                text = (ch.text or "").strip()
                evidence.append({"document_id": ch.document_id, "page": ch.page_start, "chunk_id": ch.chunk_id,
                                 "text": text[:1200], "kind": "chunk", "chunk_type": ch.chunk_type, "section_path": ch.section_path})
                lines.append(f"- [{ch.document_id} p.{ch.page_start}, {ch.chunk_type}] {text[:300]}")
        else:
            raise ToolError(f"kind must be chunks, claims or procedures, not {kind!r}")
        output = f"{len(evidence)} {kind} for {query!r}" + ("\n" + "\n".join(lines) if lines else "\n(nothing matched in the documents you may read)")
        return ToolResult(tool=self.name, ok=True, output=output, evidence=evidence,
                          data={"query": query, "kind": kind, "hits": len(evidence),
                                "documents": sorted({e["document_id"] for e in evidence})})
