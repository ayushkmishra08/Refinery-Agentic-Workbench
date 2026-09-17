/**
 * The list of a person's conversations, kept fresh across the app.
 *
 * The server owns the history: every turn is written to `data/workbench/sessions/<user>__<id>.json`
 * as it completes, so a conversation survives a reload, a sign-out, and a restart of the API. What
 * the browser keeps is only *which* conversation this tab is looking at (`?c=` in the URL, mirrored
 * to sessionStorage so a reload lands back on it), never the turns themselves.
 *
 * Pages that change the list — the chat, after an answer lands or a document is attached — fire
 * `CONVERSATIONS_CHANGED` on `window`; the sidebar listens and refetches. A window event rather
 * than shared state because the two live in different route subtrees and this is the whole of
 * their coupling.
 */
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";

export const CONVERSATIONS_CHANGED = "rwb:conversations-changed";

/** Tell every listener the list is stale. Cheap, and safe to call more often than needed. */
export function announceConversationsChanged(): void {
  window.dispatchEvent(new Event(CONVERSATIONS_CHANGED));
}

export function useConversations(enabled: boolean) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      setItems(await api.conversations());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void refresh();
    window.addEventListener(CONVERSATIONS_CHANGED, refresh);
    return () => window.removeEventListener(CONVERSATIONS_CHANGED, refresh);
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await api.deleteConversation(id);
    setItems((prev) => prev.filter((c) => c.session_id !== id));
  }, []);

  return { items, loading, error, refresh, remove };
}

// ---------------------------------------------------------------- which conversation is open

const KEY_PREFIX = "rwb:conversation:";

/** A fresh conversation id. Short, URL-safe, and unguessable enough for a per-user namespace. */
export function newConversationId(): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return "c-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function rememberConversation(username: string, id: string): void {
  try { sessionStorage.setItem(KEY_PREFIX + username, id); } catch { /* private mode, or storage full */ }
}

export function recallConversation(username: string): string | null {
  try { return sessionStorage.getItem(KEY_PREFIX + username); } catch { return null; }
}
