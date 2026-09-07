import Link from "next/link";
import Icon from "@/components/Icon";
import type { RecalledMemory } from "@/lib/api";
import type { Persona } from "@/lib/personas";

// No "use client" -- this is presentational only, matching
// ChatMessageBubble.tsx. Renders which stored memories were actually
// injected into this reply's prompt.

// The bolt takes the accent of the persona that recalled it, so a chip row
// stays attached to its message when several replies are on screen. The
// chip's own chrome stays zinc.
const BOLT_CLASS: Record<Persona, string> = {
  jarvis: "text-persona-jarvis",
  friday: "text-persona-friday",
  ultron: "text-persona-ultron",
};

export default function RecalledMemoryChips({
  memories,
  persona,
}: {
  memories: RecalledMemory[];
  persona?: Persona | null;
}) {
  if (memories.length === 0) return null;

  const boltClass = persona ? BOLT_CLASS[persona] : "text-zinc-400";

  return (
    <div className="flex flex-wrap items-center gap-1.5 pt-1">
      <span className="mr-1 font-mono text-[11px] uppercase tracking-tight text-zinc-500">
        Recalled:
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
          className="inline-flex max-w-[18rem] items-center gap-1 rounded border border-zinc-800 bg-zinc-900 px-2 py-0.5 font-mono text-[12px] text-zinc-300 transition-colors hover:bg-zinc-850 hover:text-zinc-100"
        >
          <Icon name="bolt" className={`h-3 w-3 shrink-0 ${boltClass}`} strokeWidth={2} />
          <span className="truncate">{memory.content}</span>
        </Link>
      ))}
    </div>
  );
}
