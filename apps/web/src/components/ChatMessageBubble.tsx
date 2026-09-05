import type { ChatMessage } from "@/lib/api";
import { type Persona, type PersonaInfo, personaLabel } from "@/lib/personas";
import RecalledMemoryChips from "@/components/RecalledMemoryChips";

// Tailwind's scanner needs literal class strings, not a template literal --
// see the --color-persona-* tokens in globals.css.
const ACCENT_CLASS: Record<Persona, string> = {
  jarvis: "text-persona-jarvis",
  friday: "text-persona-friday",
  ultron: "text-persona-ultron",
};

export default function ChatMessageBubble({
  message,
  personas,
}: {
  message: ChatMessage;
  personas: PersonaInfo[];
}) {
  const isUser = message.role === "user";
  const accentClass = message.persona ? ACCENT_CLASS[message.persona] : "text-zinc-500 dark:text-zinc-400";

  return (
    <div className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
          isUser
            ? "bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900"
            : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
        }`}
      >
        {/* Each bubble labels the persona that actually wrote it, not the
            currently-selected one -- that's what keeps a mixed-persona
            thread readable after a reload. */}
        {!isUser && (
          <div className={`mb-1 text-xs font-semibold uppercase tracking-wide ${accentClass}`}>
            {personaLabel(personas, message.persona)}
          </div>
        )}
        {message.content}
        {!isUser && <RecalledMemoryChips memories={message.recalled_memories} />}
      </div>
    </div>
  );
}
