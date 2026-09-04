"use client";

import { useCallback, useEffect, useState } from "react";
import ConversationSidebar from "@/components/ConversationSidebar";
import MessageList from "@/components/MessageList";
import ChatInput from "@/components/ChatInput";
import PersonaSwitcher from "@/components/PersonaSwitcher";
import {
  ApiError,
  type ChatMessage,
  type ConversationSummary,
  getConversation,
  listConversations,
  listPersonas,
  sendMessage,
} from "@/lib/api";
import { DEFAULT_PERSONA, FALLBACK_PERSONAS, type Persona, personaLabel } from "@/lib/personas";

export default function Home() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [persona, setPersona] = useState<Persona>(DEFAULT_PERSONA);
  const [personas, setPersonas] = useState(FALLBACK_PERSONAS);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch {
      // Non-fatal: the sidebar just stays stale/empty if this fails.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    listConversations()
      .then((result) => {
        if (!cancelled) setConversations(result);
      })
      .catch(() => {
        // Non-fatal: the sidebar just stays stale/empty if this fails.
      });
    listPersonas()
      .then((result) => {
        if (!cancelled && result.length > 0) setPersonas(result);
      })
      .catch(() => {
        // Non-fatal: FALLBACK_PERSONAS keeps the switcher usable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSelectConversation(id: string) {
    setError(null);
    setNotice(null);
    try {
      const detail = await getConversation(id);
      setActiveId(detail.id);
      setMessages(detail.messages);
      setPersona(detail.persona);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load conversation.");
    }
  }

  function handleNewChat() {
    setActiveId(null);
    setMessages([]);
    setError(null);
    setNotice(null);
    // Deliberately keep the current persona selection -- switching persona
    // shouldn't reset it, and it's the natural persona to start a new chat with.
  }

  async function handleSend(content: string) {
    setError(null);
    setNotice(null);

    const optimisticUserMessage: ChatMessage = {
      id: `pending-${crypto.randomUUID()}`,
      role: "user",
      content,
      persona,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUserMessage]);
    setPending(true);

    try {
      const result = await sendMessage(content, activeId ?? undefined, persona);
      setActiveId(result.conversation_id);
      setMessages((prev) => [...prev, result.message]);
      if (result.filtered) {
        setNotice(
          `${personaLabel(personas, result.message.persona)}'s safety filter replaced that reply -- it crossed a line the persona enforces.`
        );
      } else if (result.fell_back) {
        setNotice(`Primary model was unavailable — replied using the fallback model (${result.model_used}).`);
      }
      refreshConversations();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong sending that message.");
    } finally {
      setPending(false);
    }
  }

  const activeLabel = personaLabel(personas, persona);

  return (
    <div className="flex flex-1 bg-white dark:bg-black">
      <ConversationSidebar
        conversations={conversations}
        activeId={activeId}
        personas={personas}
        onSelect={handleSelectConversation}
        onNewChat={handleNewChat}
      />
      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h1 className="text-sm font-semibold tracking-wide text-zinc-900 dark:text-zinc-100">
            CIPHER — {activeLabel}
          </h1>
          <PersonaSwitcher personas={personas} value={persona} onChange={setPersona} disabled={pending} />
        </header>

        {notice && (
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
            {notice}
          </div>
        )}
        {error && (
          <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {error}
          </div>
        )}

        <MessageList
          messages={messages}
          pending={pending}
          activePersonaLabel={activeLabel}
          personas={personas}
        />
        <ChatInput disabled={pending} personaLabel={activeLabel} onSend={handleSend} />
      </div>
    </div>
  );
}
