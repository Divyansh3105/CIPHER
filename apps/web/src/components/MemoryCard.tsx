"use client";

import { useState } from "react";
import type { Memory, MemoryType } from "@/lib/api";

// Tailwind's scanner needs literal class strings, not a template literal --
// same trap ChatMessageBubble.tsx's ACCENT_CLASS documents and avoids.
const MEMORY_TYPE_CLASS: Record<MemoryType, string> = {
  short_term: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  long_term: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  episodic: "bg-purple-50 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
  semantic: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
};

export default function MemoryCard({
  memory,
  onSave,
  onDelete,
  busy,
}: {
  memory: Memory;
  onSave: (id: string, content: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(memory.content);

  async function handleSave() {
    if (draft.trim().length === 0) return;
    await onSave(memory.id, draft.trim());
    setEditing(false);
  }

  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-2 flex items-center gap-2">
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${MEMORY_TYPE_CLASS[memory.memory_type]}`}
        >
          {memory.memory_type.replace("_", " ")}
        </span>
        {memory.embedding_pending && (
          <span className="text-[10px] font-medium text-amber-600 dark:text-amber-400">
            not searchable yet
          </span>
        )}
      </div>

      {editing ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          className="w-full resize-none rounded-lg border border-zinc-300 bg-transparent p-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:text-zinc-100"
          autoFocus
        />
      ) : (
        <p className="text-sm text-zinc-800 dark:text-zinc-200">{memory.content}</p>
      )}

      <div className="mt-2 flex gap-3 text-xs">
        {editing ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={handleSave}
              className="font-semibold text-zinc-900 hover:underline disabled:opacity-40 dark:text-zinc-100"
            >
              Save
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setDraft(memory.content);
                setEditing(false);
              }}
              className="text-zinc-500 hover:underline disabled:opacity-40 dark:text-zinc-400"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => setEditing(true)}
              className="text-zinc-500 hover:underline disabled:opacity-40 dark:text-zinc-400"
            >
              Edit
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onDelete(memory.id)}
              className="text-red-600 hover:underline disabled:opacity-40 dark:text-red-400"
            >
              Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}
