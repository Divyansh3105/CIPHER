"use client";

import { useState } from "react";
import Icon from "@/components/Icon";
import type { Memory, MemoryType } from "@/lib/api";
import { relativeTime } from "@/lib/format";

// Tailwind's scanner needs literal class strings, not a template literal --
// same trap ChatMessageBubble.tsx's ACCENT_CLASS documents and avoids.
//
// These four hues are shared with the galaxy's node colours and its legend
// (see MEMORY_TYPE_COLOUR in MemoryGalaxy). A memory has to be the same
// colour in the list as it is in the graph, or the legend is a lie.
export const MEMORY_TYPE_CLASS: Record<MemoryType, string> = {
  long_term: "bg-indigo-950/70 text-indigo-300 border-indigo-800/60",
  semantic: "bg-emerald-950/70 text-emerald-300 border-emerald-800/60",
  episodic: "bg-amber-950/70 text-amber-300 border-amber-800/60",
  short_term: "bg-sky-950/70 text-sky-300 border-sky-800/60",
};

export const MEMORY_TYPE_LABEL: Record<MemoryType, string> = {
  long_term: "Long term",
  semantic: "Semantic",
  episodic: "Episodic",
  short_term: "Short term",
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
    <article className="group flex items-start justify-between gap-4 rounded-lg border border-zinc-850 bg-zinc-900/70 p-3.5 transition duration-150 hover:border-zinc-750">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded border px-2 py-0.5 font-mono text-[10px] font-medium ${MEMORY_TYPE_CLASS[memory.memory_type]}`}
          >
            {MEMORY_TYPE_LABEL[memory.memory_type]}
          </span>
          <span className="font-mono text-[11px] text-zinc-500">
            MEM-{memory.id.slice(0, 8).toUpperCase()}
          </span>
          <span className="text-xs text-zinc-600">·</span>
          <span className="text-xs text-zinc-400">Captured {relativeTime(memory.created_at)}</span>
          <span className="text-xs text-zinc-600">·</span>
          <span className="font-mono text-[11px] text-zinc-500">source: {memory.source}</span>
          {memory.embedding_pending && (
            <span className="rounded border border-amber-800/60 bg-amber-950/60 px-1.5 py-0.5 font-mono text-[10px] text-amber-400">
              not searchable yet
            </span>
          )}
        </div>

        {editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            autoFocus
            className="w-full resize-none rounded border border-zinc-700 bg-zinc-950 p-2 text-xs leading-relaxed text-zinc-100 outline-none focus:border-zinc-600"
          />
        ) : (
          <p className="text-xs font-medium leading-relaxed text-zinc-100">{memory.content}</p>
        )}

        <div className="flex flex-wrap items-center gap-2 font-mono text-[11px] text-zinc-500">
          <span>
            Last recalled:{" "}
            <span className="text-zinc-400">
              {memory.last_recalled_at ? relativeTime(memory.last_recalled_at) : "never"}
            </span>
          </span>
          {memory.persona && (
            <>
              <span>·</span>
              <span>persona: {memory.persona}</span>
            </>
          )}
        </div>
      </div>

      {/* Edit and delete only appear on hover (or keyboard focus -- the
          focus-within keeps them reachable without a mouse, which
          opacity-on-hover alone would not). */}
      <div
        className={[
          "flex shrink-0 items-center gap-1 transition-opacity",
          editing ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
        ].join(" ")}
      >
        {editing ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={handleSave}
              className="rounded bg-zinc-200 px-2 py-1 text-[11px] font-medium text-zinc-900 transition hover:bg-white disabled:opacity-40"
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
              className="rounded border border-zinc-800 px-2 py-1 text-[11px] text-zinc-400 transition hover:text-zinc-200 disabled:opacity-40"
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
              title="Edit memory"
              aria-label="Edit memory"
              className="rounded p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40"
            >
              <Icon name="pencil" className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onDelete(memory.id)}
              title="Forget this memory"
              aria-label="Forget this memory"
              className="rounded p-1.5 text-zinc-400 transition hover:bg-red-950/40 hover:text-red-400 disabled:opacity-40"
            >
              <Icon name="trash" className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
    </article>
  );
}
