/**
 * The sidebar's list of a person's conversations, and the way to start another.
 *
 * Each row is a server-side session: title from the first question, when it was last touched, how
 * many exchanges, and a paperclip when a document is attached to it. Clicking one opens it in the
 * chat and the turns are redrawn from what the server kept. The list refreshes itself whenever the
 * chat announces a change, so a new conversation appears here the moment its first answer lands.
 */
import { MessageSquarePlus, Paperclip, Trash2 } from "lucide-react";
import { useLocation, useNavigate } from "react-router";

import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import { newConversationId, rememberConversation, useConversations } from "@/store/conversations";
import { Spinner, useToast } from "@/ui";

export function RecentConversations({ username }: { username: string }) {
  const { items, loading, error, remove } = useConversations(!!username);
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();

  const openId = new URLSearchParams(location.search).get("c");

  const open = (id: string) => {
    rememberConversation(username, id);
    navigate(`/chat?c=${encodeURIComponent(id)}`);
  };

  const startNew = () => open(newConversationId());

  const del = async (id: string, title: string) => {
    try {
      await remove(id);
      if (openId === id) startNew();
      toast.push({ tone: "info", message: "Conversation removed", detail: title });
    } catch (err) {
      toast.push({ tone: "danger", message: "Could not remove it", detail: err instanceof Error ? err.message : String(err) });
    }
  };

  return (
    <section className="mt-4 flex min-h-0 flex-1 flex-col" aria-label="Recent conversations">
      <div className="mb-1.5 flex items-center justify-between px-1">
        <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">Recent</p>
        <button
          type="button"
          onClick={startNew}
          title="New conversation"
          className="flex size-6 items-center justify-center rounded-md text-primary hover:bg-primary/10"
        >
          <MessageSquarePlus className="size-3.5" />
        </button>
      </div>

      {error ? (
        <p className="px-1 text-[0.68rem] text-[var(--danger)]">{error}</p>
      ) : items.length === 0 ? (
        <p className="px-1 text-[0.68rem] text-muted-foreground">
          {loading ? <Spinner className="size-3" /> : "Nothing yet. Ask something and it will be kept here."}
        </p>
      ) : (
        <ul className="thin-scroll -mx-1 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
          {items.map((c) => {
            const active = c.session_id === openId;
            return (
              <li key={c.session_id} className="group relative">
                <button
                  type="button"
                  onClick={() => open(c.session_id)}
                  className={cn(
                    "w-full rounded-md px-2 py-1.5 text-left transition-colors",
                    active ? "bg-primary/10 text-foreground" : "text-slate-700 hover:bg-slate-900/[0.05]",
                  )}
                  aria-current={active ? "page" : undefined}
                >
                  <p className="truncate pr-5 text-xs font-medium">{c.title}</p>
                  <p className="mt-0.5 flex items-center gap-1.5 text-[0.65rem] text-muted-foreground">
                    <span>{c.updated ? relativeTime(c.updated) : ""}</span>
                    <span>· {c.turns} {c.turns === 1 ? "turn" : "turns"}</span>
                    {c.attachments.length ? <Paperclip className="size-2.5" aria-label="has an attachment" /> : null}
                  </p>
                </button>
                <button
                  type="button"
                  onClick={() => void del(c.session_id, c.title)}
                  title="Remove this conversation"
                  className="absolute right-1 top-1.5 hidden size-5 items-center justify-center rounded text-muted-foreground hover:bg-[var(--danger)]/10 hover:text-[var(--danger)] group-hover:flex"
                >
                  <Trash2 className="size-3" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
