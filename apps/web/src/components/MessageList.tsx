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
      <div className="flex flex-1 items-center justify-center text-sm text-zinc-500 dark:text-zinc-400">
        Say something to {activePersonaLabel} to get started.
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-6">
      {messages.map((message) => (
        <ChatMessageBubble key={message.id} message={message} personas={personas} />
      ))}
      {pending && (
        <div className="flex justify-start">
          <div className="rounded-2xl bg-zinc-100 px-4 py-2 text-sm text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            {activePersonaLabel} is thinking…
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
