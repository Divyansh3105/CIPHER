import Link from "next/link";
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
        // Links into the galaxy and flies the camera to this exact node, so
        // "where did that come from" is one click rather than a search. The
        // id is the snapshot's id, which may point at a memory since edited
        // or deleted -- the galaxy simply does not move in that case, which
        // is the honest outcome: the chip records what the model saw then.
        <Link
          key={memory.id}
          href={`/memory?view=galaxy&focus=${encodeURIComponent(memory.id)}`}
          title={`${memory.content} (similarity ${memory.similarity.toFixed(2)}) — show in the galaxy`}
          className="max-w-[14rem] truncate rounded-full border border-zinc-300 bg-zinc-50 px-2 py-0.5 text-[11px] text-zinc-600 hover:border-sky-500 hover:text-sky-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-sky-500 dark:hover:text-sky-400"
        >
          {memory.content}
        </Link>
      ))}
    </div>
  );
}
