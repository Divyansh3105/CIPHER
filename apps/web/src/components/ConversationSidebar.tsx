"use client";

import type { ConversationSummary } from "@/lib/api";

export default function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNewChat,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
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
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                onClick={() => onSelect(conversation.id)}
                className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm ${
                  conversation.id === activeId
                    ? "bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900"
                    : "hover:bg-zinc-100 dark:hover:bg-zinc-800"
                }`}
              >
                {conversation.title || "Untitled conversation"}
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
