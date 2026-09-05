import type { RecalledMemory } from "@/lib/api";

// No "use client" -- this is presentational only, matching
// ChatMessageBubble.tsx (the only other non-client component). Renders
// which stored memories were actually injected into this reply's prompt.
export default function RecalledMemoryChips({ memories }: { memories: RecalledMemory[] }) {
  if (memories.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
        Recalled
      </span>
      {memories.map((memory) => (
        <span
          key={memory.id}
          title={`${memory.content} (similarity ${memory.similarity.toFixed(2)})`}
          className="max-w-[14rem] truncate rounded-full border border-zinc-300 bg-zinc-50 px-2 py-0.5 text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400"
        >
          {memory.content}
        </span>
      ))}
    </div>
  );
}
