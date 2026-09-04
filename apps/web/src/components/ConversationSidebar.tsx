"use client";

import type { ConversationSummary } from "@/lib/api";
import { type PersonaInfo, personaLabel } from "@/lib/personas";

export default function ConversationSidebar({
  conversations,
  activeId,
  personas,
  onSelect,
  onNewChat,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  personas: PersonaInfo[];
  onSelect: (id: string) => void;
  onNewChat: () => void;
}) {
  return (
    <aside className="flex w-64 flex-col border-r border-zinc-200 dark:border-zinc-800">
      <div className="p-3">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full rounded-xl border border-zinc-300 px-3 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          + New chat
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        {conversations.length === 0 && (
          <p className="px-2 py-4 text-xs text-zinc-500 dark:text-zinc-400">No conversations yet.</p>
        )}
        <ul className="flex flex-col gap-1">
          {conversations.map((conversation) => {
            const selected = conversation.id === activeId;
            return (
              <li key={conversation.id}>
                <button
                  type="button"
                  onClick={() => onSelect(conversation.id)}
                  className={`w-full rounded-lg px-3 py-2 text-left ${
                    selected
                      ? "bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900"
                      : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
                  }`}
                >
                  <span className="block truncate text-sm">
                    {conversation.title || "Untitled conversation"}
                  </span>
                  {/* A selected item's background is zinc-900, so the normal
                      muted text color is nearly invisible on it -- this has
                      to be conditional, not cosmetic. */}
                  <span
                    className={`block text-xs font-semibold uppercase tracking-wide ${
                      selected ? "text-zinc-400 dark:text-zinc-500" : "text-zinc-500 dark:text-zinc-400"
                    }`}
                  >
                    {personaLabel(personas, conversation.persona)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
