/**
 * The workbench answers in Markdown. This turns it into a document.
 *
 * The terminal prints `### Prerequisites` and `- check the strainer` literally; on screen those
 * have to become a heading and a bulleted list, or the answer reads like a config file. The
 * renderer is written by hand rather than pulled from a library for two reasons: the answer
 * markup the workbench emits is a small, known subset (headings, bullets, ordered steps, tables,
 * block quotes, bold/italic/code, citation refs), and an answer built from classified documents
 * should not be passed through a dependency that can execute or inject markup.
 *
 * **No HTML is ever interpreted.** Every leaf is rendered as a React text node, so a document
 * containing `<script>` or `<img onerror=…>` is shown as characters, never mounted. That matters
 * here more than usual: the text comes from PDFs the workbench did not write.
 */
import { Fragment, type ReactNode, createElement, useMemo } from "react";

import { cn } from "@/lib/cn";

// ---------------------------------------------------------------- inline

/**
 * `**bold**`, `*italic*`, `` `code` ``, `[1]` citation refs and bare URLs.
 * Returns React nodes; the input is never treated as HTML.
 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  // one pass, longest-delimiter-first so ** is not eaten by *
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\n]+\*|_[^_\n]+_|\[\d+\]|https?:\/\/\S+)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let i = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-i${i++}`;

    if (token.startsWith("**") || token.startsWith("__")) {
      out.push(<strong key={key} className="font-semibold text-foreground">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      out.push(
        <code key={key} className="rounded bg-slate-900/[0.06] px-1.5 py-0.5 font-mono text-[0.85em] text-slate-700">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (/^\[\d+\]$/.test(token)) {
      out.push(
        <sup key={key} className="ml-0.5 rounded bg-primary/10 px-1 py-px font-mono text-[0.68em] font-semibold text-primary">
          {token.slice(1, -1)}
        </sup>,
      );
    } else if (token.startsWith("http")) {
      out.push(
        <a key={key} href={token} target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-2">
          {token}
        </a>,
      );
    } else {
      out.push(<em key={key} className="italic">{token.slice(1, -1)}</em>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ---------------------------------------------------------------- blocks

type Node =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "bullets"; items: string[] }
  | { kind: "ordered"; items: { marker: string; text: string }[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "code"; lines: string[]; lang?: string }
  | { kind: "table"; header: string[]; rows: string[][] }
  | { kind: "rule" };

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|(.+)\|\s*$/;
const TABLE_SEP = /^\s*\|[\s:|-]+\|\s*$/;

function splitRow(line: string): string[] {
  return line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
}

/** Stands in for a markdown hard break between the parser and the renderer; never user-visible. */
const HARD_BREAK = "\u0000";

/** Split a parsed paragraph back into the rows its hard breaks asked for. */
function hardRows(text: string): string[] {
  return text.split(HARD_BREAK);
}

/** Markdown -> a flat list of block nodes. Unrecognised lines become paragraphs, never vanish. */
function parse(markdown: string): Node[] {
  const lines = (markdown || "").replace(/\r\n/g, "\n").split("\n");
  const nodes: Node[] = [];
  let paragraph: string[] = [];

  // Markdown's hard break — a line ending in two or more spaces, or a backslash — is how the
  // workbench writes a status line that has to stay on its own row. Trimming each line would lose
  // it, so the break is carried through the joined paragraph as a sentinel and turned into a <br/>
  // at render time. A single newline without that marker stays a soft break, joined with a space.
  const flush = () => {
    if (paragraph.length) {
      let text = "";
      for (const piece of paragraph) {
        if (!text) text = piece;
        else if (text.endsWith(HARD_BREAK)) text += piece;
        else text += " " + piece;
      }
      nodes.push({ kind: "paragraph", text: text.trim() });
      paragraph = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (!line.trim()) { flush(); continue; }

    if (line.trimStart().startsWith("```")) {
      flush();
      const lang = line.trim().slice(3).trim() || undefined;
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) body.push(lines[i++]);
      nodes.push({ kind: "code", lines: body, lang });
      continue;
    }

    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); nodes.push({ kind: "rule" }); continue; }

    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      nodes.push({ kind: "heading", level: heading[1].length, text: heading[2].trim() });
      continue;
    }

    if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      flush();
      const header = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) rows.push(splitRow(lines[i++]));
      i--;
      nodes.push({ kind: "table", header, rows });
      continue;
    }

    if (BULLET.test(line)) {
      flush();
      const items: string[] = [];
      while (i < lines.length && BULLET.test(lines[i])) {
        items.push(BULLET.exec(lines[i])![1]);
        // a wrapped continuation line is indented and is not itself a marker
        while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1]) && !BULLET.test(lines[i + 1]) && !ORDERED.test(lines[i + 1])) {
          items[items.length - 1] += ` ${lines[++i].trim()}`;
        }
        i++;
      }
      i--;
      nodes.push({ kind: "bullets", items });
      continue;
    }

    if (ORDERED.test(line)) {
      flush();
      const items: { marker: string; text: string }[] = [];
      while (i < lines.length && ORDERED.test(lines[i])) {
        const m = ORDERED.exec(lines[i])!;
        items.push({ marker: m[1], text: m[2] });
        while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1]) && !BULLET.test(lines[i + 1]) && !ORDERED.test(lines[i + 1])) {
          items[items.length - 1].text += ` ${lines[++i].trim()}`;
        }
        i++;
      }
      i--;
      nodes.push({ kind: "ordered", items });
      continue;
    }

    if (/^\s*>/.test(line)) {
      flush();
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      i--;
      nodes.push({ kind: "quote", lines: body });
      continue;
    }

    paragraph.push(line.trim() + (/(\s{2,}|\\)$/.test(line) ? HARD_BREAK : ""));
  }
  flush();
  return nodes;
}

const HEADING_CLASS: Record<number, string> = {
  1: "mt-6 mb-3 text-xl font-semibold tracking-tight text-foreground first:mt-0",
  2: "mt-6 mb-2.5 text-lg font-semibold tracking-tight text-foreground first:mt-0",
  3: "mt-5 mb-2 text-[0.95rem] font-semibold uppercase tracking-wide text-slate-500 first:mt-0",
  4: "mt-4 mb-1.5 text-sm font-semibold text-foreground first:mt-0",
  5: "mt-3 mb-1 text-sm font-semibold text-slate-600 first:mt-0",
  6: "mt-3 mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500 first:mt-0",
};

export function Markdown({ children, className }: { children: string; className?: string }) {
  const nodes = useMemo(() => parse(children), [children]);

  return (
    <div className={cn("text-[0.9375rem] leading-relaxed text-slate-700", className)}>
      {nodes.map((node, idx) => {
        const key = `n${idx}`;
        switch (node.kind) {
          case "heading":
            return createElement(
              `h${Math.min(node.level + 1, 6)}`,
              { key, className: HEADING_CLASS[node.level] ?? HEADING_CLASS[4] },
              renderInline(node.text, key),
            );

          case "paragraph":
            return (
              <p key={key} className="mb-3 last:mb-0">
                {hardRows(node.text).map((row, r) => (
                  <Fragment key={`${key}-r${r}`}>
                    {r ? <br /> : null}
                    {renderInline(row, `${key}-r${r}`)}
                  </Fragment>
                ))}
              </p>
            );

          case "bullets":
            return (
              <ul key={key} className="mb-3 space-y-1.5 last:mb-0">
                {node.items.map((item, j) => (
                  <li key={`${key}-${j}`} className="flex gap-2.5">
                    <span aria-hidden className="mt-[0.55em] size-1.5 shrink-0 rounded-full bg-primary/50" />
                    <span className="min-w-0 flex-1">{renderInline(item, `${key}-${j}`)}</span>
                  </li>
                ))}
              </ul>
            );

          case "ordered":
            return (
              <ol key={key} className="mb-3 space-y-2 last:mb-0">
                {node.items.map((item, j) => (
                  <li key={`${key}-${j}`} className="flex gap-3">
                    <span className="mt-px flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 font-mono text-[0.7rem] font-semibold text-primary">
                      {item.marker}
                    </span>
                    <span className="min-w-0 flex-1">{renderInline(item.text, `${key}-${j}`)}</span>
                  </li>
                ))}
              </ol>
            );

          case "quote":
            return (
              <blockquote key={key} className="mb-3 border-l-2 border-primary/30 bg-primary/[0.04] py-2 pl-3 pr-2 text-slate-600 last:mb-0">
                {node.lines.filter((l) => l.trim()).map((l, j) => (
                  <p key={`${key}-${j}`} className="mb-1 last:mb-0">{renderInline(l, `${key}-${j}`)}</p>
                ))}
              </blockquote>
            );

          case "code":
            return (
              <pre key={key} className="thin-scroll mb-3 overflow-x-auto rounded-lg border border-border/70 bg-slate-900/[0.04] p-3 last:mb-0">
                <code className="font-mono text-[0.8rem] leading-relaxed text-slate-700">{node.lines.join("\n")}</code>
              </pre>
            );

          case "table":
            return (
              <div key={key} className="thin-scroll mb-3 overflow-x-auto rounded-lg border border-border/70 last:mb-0">
                <table className="w-full border-collapse text-[0.85rem]">
                  <thead>
                    <tr className="bg-slate-900/[0.04]">
                      {node.header.map((h, j) => (
                        <th key={`${key}-h${j}`} className="whitespace-nowrap px-3 py-2 text-left font-semibold text-slate-600">
                          {renderInline(h, `${key}-h${j}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {node.rows.map((row, j) => (
                      <tr key={`${key}-r${j}`} className="border-t border-border/60">
                        {row.map((cell, k) => (
                          <td key={`${key}-r${j}c${k}`} className="px-3 py-2 align-top text-slate-700">
                            {renderInline(cell, `${key}-r${j}c${k}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );

          case "rule":
            return <hr key={key} className="my-4 border-border/70" />;
        }
      })}
    </div>
  );
}
