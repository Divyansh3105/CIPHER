"use client";

import { useEffect, useRef } from "react";
import type { ChatMessage } from "@/lib/api";
import type { PersonaInfo } from "@/lib/personas";
import ChatMessageBubble from "@/components/ChatMessageBubble";

export default function MessageList({
  messages,
  pending,
  activePersonaLabel,
  personas,
}: {
  messages: ChatMessage[];
  pending: boolean;
  activePersonaLabel: string;
  personas: PersonaInfo[];
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, pending]);

  if (messages.length === 0 && !pending) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
        <p className="font-mono text-[13px] text-zinc-400">
          Say something to {activePersonaLabel} to get started.
        </p>
        <p className="max-w-md text-[12px] leading-relaxed text-zinc-600">
          Switch persona above, pin a model on the right, or turn the microphone on and talk.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-7 overflow-y-auto px-6 py-6">
      {messages.map((message) => (
        <ChatMessageBubble key={message.id} message={message} personas={personas} />
      ))}

      {pending && (
        <article className="mr-auto flex max-w-3xl flex-col gap-2">
          <div className="flex items-center gap-2 font-mono text-[11px] text-zinc-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500" />
            {activePersonaLabel} is thinking…
          </div>
        </article>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
