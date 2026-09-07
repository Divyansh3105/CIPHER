import type { ChatMessage } from "@/lib/api";
import RecalledMemoryChips from "@/components/RecalledMemoryChips";
import CitationChips from "@/components/CitationChips";
import { clockTime } from "@/lib/format";
import { type Persona, type PersonaInfo, personaLabel } from "@/lib/personas";

// Tailwind's scanner needs literal class strings, not a template literal --
// see the --color-persona-* tokens in globals.css.
const ACCENT_CLASS: Record<Persona, string> = {
  jarvis: "text-persona-jarvis",
  friday: "text-persona-friday",
  ultron: "text-persona-ultron",
};

const DOT_CLASS: Record<Persona, string> = {
  jarvis: "bg-persona-jarvis",
  friday: "bg-persona-friday",
  ultron: "bg-persona-ultron",
};

export default function ChatMessageBubble({
  message,
  personas,
}: {
  message: ChatMessage;
  personas: PersonaInfo[];
}) {
  // The user's own words get the only card in the thread. The assistant's
  // reply is set on the page ground behind a hairline rule instead, which is
  // what keeps a long answer readable -- a paragraph of body text inside a
  // filled bubble is the thing that makes a chat app feel like a toy.
  if (message.role === "user") {
    return (
      <article className="ml-auto flex max-w-3xl flex-col gap-1">
        <div className="flex items-center justify-end gap-2 font-mono text-[11px] text-zinc-500">
          <span>You</span>
          <span>·</span>
          <span className="tabular-nums">{clockTime(message.created_at)}</span>
        </div>
        <div className="whitespace-pre-wrap rounded-lg border border-zinc-800/90 bg-zinc-900 p-3.5 text-[14px] leading-relaxed text-zinc-200">
          {message.content}
        </div>
      </article>
    );
  }

  const accentClass = message.persona ? ACCENT_CLASS[message.persona] : "text-zinc-400";
  const dotClass = message.persona ? DOT_CLASS[message.persona] : "bg-zinc-600";

  return (
    <article className="mr-auto flex max-w-3xl flex-col gap-2">
      {/* Each message labels the persona that actually wrote it, not the
          currently-selected one -- that's what keeps a mixed-persona thread
          readable after a reload. */}
      <div className="flex items-center gap-2 font-mono text-[11px]">
        <span className={`flex items-center gap-1.5 font-medium tracking-wide ${accentClass}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
          {personaLabel(personas, message.persona)}
        </span>
        <span className="text-zinc-600">·</span>
        <span className="tabular-nums text-zinc-500">{clockTime(message.created_at)}</span>
      </div>

      <div className="whitespace-pre-wrap border-l border-zinc-800 pl-3 text-[14px] leading-relaxed text-zinc-200">
        {message.content}
      </div>

      <RecalledMemoryChips memories={message.recalled_memories} persona={message.persona} />
      <CitationChips citations={message.citations} />
    </article>
  );
}
