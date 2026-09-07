"use client";

import type { ConversationSummary } from "@/lib/api";
import Icon from "@/components/Icon";
import { relativeTime } from "@/lib/format";
import { type Persona, type PersonaInfo, personaLabel } from "@/lib/personas";

// One of the two places a persona accent is allowed to appear (the other is
// a message's persona label). The dot and the sublabel say which voice last
// answered this thread; nothing else in the sidebar is coloured.
const DOT_CLASS: Record<Persona, string> = {
  jarvis: "bg-persona-jarvis",
  friday: "bg-persona-friday",
  ultron: "bg-persona-ultron",
};

export default function ConversationSidebar({
  conversations,
  activeId,
  personas,
  busyId,
  onSelect,
  onNewChat,
  onDelete,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  personas: PersonaInfo[];
  /** The conversation currently being deleted, if any. */
  busyId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (conversation: ConversationSummary) => void;
}) {
  return (
    <aside className="flex h-full w-64 shrink-0 select-none flex-col border-r border-zinc-800 bg-zinc-925/90">
      <div className="border-b border-zinc-800/80 p-3">
        <button
          type="button"
          onClick={onNewChat}
          className="group flex w-full items-center justify-between rounded border border-zinc-800 bg-zinc-900 px-2.5 py-1.5 text-[13px] font-medium text-zinc-200 transition-all hover:border-zinc-700 hover:bg-zinc-850"
        >
          <span className="flex items-center gap-1.5">
            <Icon
              name="plus"
              className="h-3.5 w-3.5 text-zinc-400 group-hover:text-zinc-200"
              strokeWidth={2}
            />
            New conversation
          </span>
        </button>
      </div>

      <div className="flex items-center justify-between px-3 pt-2.5 pb-1 font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-500">
        <span>History</span>
        <span className="text-[10px] text-zinc-600">
          {conversations.length} {conversations.length === 1 ? "thread" : "threads"}
        </span>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-1.5 py-1">
        {conversations.length === 0 && (
          <p className="px-2.5 py-4 text-[12px] text-zinc-500">No conversations yet.</p>
        )}

        {conversations.map((conversation) => {
          const selected = conversation.id === activeId;
          const deleting = conversation.id === busyId;
          return (
            // A div, not a button, because the delete control lives inside
            // it and a button inside a button is invalid markup that
            // browsers resolve by dropping one of them.
            <div
              key={conversation.id}
              className={[
                "group relative rounded transition-colors",
                selected
                  ? "border border-zinc-800/90 bg-zinc-850/90 text-zinc-100"
                  : "text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100",
                deleting ? "opacity-40" : "",
              ].join(" ")}
            >
              <button
                type="button"
                onClick={() => onSelect(conversation.id)}
                disabled={deleting}
                className="flex w-full cursor-pointer flex-col gap-1 px-2.5 py-2 text-left"
              >
                <div className="flex items-start justify-between gap-1.5">
                  <span
                    className={[
                      "truncate text-[13px] leading-tight tracking-tight",
                      selected ? "font-medium" : "font-normal",
                    ].join(" ")}
                  >
                    {conversation.title || "Untitled conversation"}
                  </span>
                  <span
                    className={[
                      "mt-1 h-2 w-2 shrink-0 rounded-full",
                      DOT_CLASS[conversation.persona] ?? "bg-zinc-600",
                    ].join(" ")}
                    title={`Last answered by ${personaLabel(personas, conversation.persona)}`}
                  />
                </div>
                <div
                  className={[
                    "flex items-center justify-between font-mono text-[11px]",
                    selected ? "text-zinc-400" : "text-zinc-500 group-hover:text-zinc-400",
                  ].join(" ")}
                >
                  <span className="truncate">{personaLabel(personas, conversation.persona)}</span>
                  {/* Hidden while the delete control is showing, so the two
                      never sit on top of each other. The slot keeps its
                      width either way, so nothing shifts on hover. */}
                  <span className="tabular-nums transition-opacity group-hover:opacity-0 group-focus-within:opacity-0">
                    {relativeTime(conversation.updated_at)}
                  </span>
                </div>
              </button>

              <button
                type="button"
                disabled={deleting}
                onClick={() => onDelete(conversation)}
                title="Delete this conversation"
                aria-label={`Delete conversation: ${conversation.title || "Untitled conversation"}`}
                className="absolute bottom-1 right-1.5 rounded p-1 text-zinc-500 opacity-0 transition hover:bg-red-950/40 hover:text-red-400 focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-40"
              >
                <Icon name="trash" className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </nav>

      <div className="flex items-center justify-between border-t border-zinc-800 bg-zinc-950/40 p-2.5 font-mono text-[11px] text-zinc-500">
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-zinc-600" />
          local backend
        </span>
      </div>
    </aside>
  );
}
