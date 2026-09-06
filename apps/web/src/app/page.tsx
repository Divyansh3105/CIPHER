"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import ConversationSidebar from "@/components/ConversationSidebar";
import MessageList from "@/components/MessageList";
import ChatInput from "@/components/ChatInput";
import PersonaSwitcher from "@/components/PersonaSwitcher";
import VoiceControls, { type VoiceState } from "@/components/VoiceControls";
import ModelChip from "@/components/ModelChip";
import { useSpeechInput, useSpeechOutput } from "@/hooks/useVoice";
import { matchWakePhrase, WAKE_FOLLOW_UP_MS } from "@/lib/speech";
import {
  type ActiveModel,
  ApiError,
  type ChatMessage,
  type ConversationSummary,
  clearActiveModel,
  getConversation,
  listConversations,
  listModels,
  listPersonas,
  type ModelInfo,
  sendMessage,
  setActiveModel,
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

  // --- Phase 4: runtime model swap ------------------------------------
  const [activeModel, setActiveModelState] = useState<ActiveModel | null>(null);
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([]);
  const [swapping, setSwapping] = useState(false);

  // --- Phase 4: voice -------------------------------------------------
  //
  // Off until the user turns the mic on. Browsers block speech synthesis
  // until a user gesture anyway, and a page that starts talking at you
  // unprompted is worse than one that stays quiet.
  const [voiceReplies, setVoiceReplies] = useState(false);
  // handleSend reads `pending` from state, but an utterance arrives from
  // outside React's render cycle and has to be able to check synchronously
  // whether a reply is already in flight.
  const pendingRef = useRef(false);

  // Indirection so speech output can drive the mic without either hook
  // having to be declared before the other.
  const suspendMicRef = useRef<() => void>(() => {});
  const resumeMicRef = useRef<() => void>(() => {});

  const speech = useSpeechOutput({
    // Cut the mic while it talks. Without this the recogniser hears the
    // assistant through the speakers and answers its own last sentence --
    // the single worst failure mode of a hands-free loop.
    onStart: () => suspendMicRef.current(),
    onEnd: () => resumeMicRef.current(),
  });

  // Set when speech arrives while a reply is still in flight. Dropping the
  // utterance is deliberate -- queueing it sends a stale message minutes
  // later -- but dropping it *silently* is how a voice assistant earns a
  // reputation for not listening, so the status line says so.
  const [droppedUtterance, setDroppedUtterance] = useState(false);

  // Hands-free: ignore everything until addressed by name. Opt-in per
  // session and never persisted -- see WAKE_WORD in @/lib/speech for why
  // an always-listening default is the wrong one.
  const [handsFree, setHandsFree] = useState(false);
  // Whether the follow-up window is currently open, so a conversation
  // does not require saying the name every single turn.
  //
  // Modelled as a flag plus a restart counter rather than an expiry
  // timestamp. A timestamp would mean comparing it against `Date.now()`
  // while deciding what to render, and a component that reads the clock
  // during render is not idempotent -- it can show a different thing on a
  // re-render nothing asked for. The counter drives an effect that owns
  // the timer, so re-arming restarts it and no render ever reads a clock.
  const [wakeArmed, setWakeArmed] = useState(false);
  const [wakeArmCount, setWakeArmCount] = useState(0);
  // The mic callback fires outside React's cycle and needs the current
  // values synchronously.
  const wakeArmedRef = useRef(false);
  const handsFreeRef = useRef(false);

  const armWakeWindow = useCallback(() => {
    wakeArmedRef.current = true;
    setWakeArmed(true);
    setWakeArmCount((n) => n + 1);
  }, []);

  const disarmWakeWindow = useCallback(() => {
    wakeArmedRef.current = false;
    setWakeArmed(false);
  }, []);

  useEffect(() => {
    handsFreeRef.current = handsFree;
  }, [handsFree]);

  useEffect(() => {
    if (!wakeArmed) return;
    const timer = setTimeout(() => {
      wakeArmedRef.current = false;
      setWakeArmed(false);
    }, WAKE_FOLLOW_UP_MS);
    // Re-arming bumps wakeArmCount, which re-runs this effect and clears
    // the previous timer -- the window restarts rather than expiring on
    // the first arm's schedule.
    return () => clearTimeout(timer);
  }, [wakeArmed, wakeArmCount]);

  // Only these lead-ins are treated as a spoken command rather than a
  // message. Deliberately narrow: "use" and "try" are excluded because
  // "use simpler words" is a perfectly ordinary thing to say to a chatbot,
  // and misreading it as a command would swallow the message entirely.
  const SWAP_COMMAND = /^\s*(switch|swap|change|go back)\b/i;

  /** Returns true when the utterance was handled as a command, not a message. */
  async function handleSpokenCommand(text: string): Promise<boolean> {
    if (!SWAP_COMMAND.test(text)) return false;

    // Personas first: "switch to FRIDAY" is about voice and tone, and would
    // otherwise be refused by the model registry with a confusing message
    // about which models exist.
    // Double-escaped on purpose: inside a template literal a single
    // backslash-b is a backspace character, so the word-boundary anchors
    // would silently never match and every spoken persona switch would
    // fall through to the model registry and be refused.
    const wanted = personas.find((p) => new RegExp(`\\b${p.id}\\b`, "i").test(text));
    if (wanted) {
      setPersona(wanted.id);
      setNotice(`Switched to ${wanted.display_name}.`);
      return true;
    }

    setSwapping(true);
    try {
      const active = await setActiveModel(text);
      setActiveModelState(active);
      setNotice(
        active.pinned
          ? `Now thinking on ${active.display_name}. It will not fall back to another model.`
          : `Back to default routing (${active.default_id}).`
      );
    } catch (err) {
      // The backend refused rather than guessing at the nearest model, and
      // its message lists what actually exists -- show it verbatim.
      setError(err instanceof ApiError ? err.message : "Could not change the model.");
    } finally {
      setSwapping(false);
    }
    return true;
  }

  const mic = useSpeechInput({
    onUtterance: (text) => {
      // Barge-in: talking over the assistant stops it, rather than queueing
      // a reply behind a paragraph you already interrupted.
      speech.cancel();
      if (pendingRef.current) {
        setDroppedUtterance(true);
        return;
      }
      setDroppedUtterance(false);

      let spoken = text;
      if (handsFreeRef.current) {
        const { matched, remainder } = matchWakePhrase(text);
        const withinFollowUp = wakeArmedRef.current;
        if (matched) {
          armWakeWindow();
          // The name on its own opens the floor rather than being sent
          // as a message -- "hey cipher" is not a question.
          if (!remainder) return;
          spoken = remainder;
        } else if (!withinFollowUp) {
          // Not addressed to us. Dropped silently and on purpose: the
          // entire point of hands-free is that ambient conversation in
          // the room is not a prompt.
          return;
        }
      }

      void (async () => {
        if (await handleSpokenCommand(spoken)) return;
        await handleSend(spoken);
      })();
    },
    onInterrupt: () => speech.cancel(),
  });

  useEffect(() => {
    suspendMicRef.current = mic.suspend;
    resumeMicRef.current = mic.resume;
  }, [mic.suspend, mic.resume]);

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
    listModels()
      .then((result) => {
        if (cancelled) return;
        setActiveModelState(result.active);
        setAvailableModels(result.available);
      })
      .catch(() => {
        // Non-fatal: the chip stays hidden and default routing applies.
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
      recalled_memories: [],
    };
    setMessages((prev) => [...prev, optimisticUserMessage]);
    pendingRef.current = true;
    setPending(true);

    try {
      const result = await sendMessage(content, activeId ?? undefined, persona);
      setActiveId(result.conversation_id);
      setMessages((prev) => [...prev, result.message]);
      // Keep the floor open for a follow-up without the wake word.
      if (handsFree) armWakeWindow();
      if (voiceReplies) {
        // Spoken in the voice of the persona that actually answered, which
        // after a mid-conversation switch is not necessarily the one now
        // selected in the switcher.
        speech.speak(result.message.content, result.message.persona ?? persona);
      }
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
      pendingRef.current = false;
      setPending(false);
    }
  }

  function handleToggleHandsFree() {
    disarmWakeWindow();
    setHandsFree((on) => !on);
  }

  function handleToggleMic() {
    setDroppedUtterance(false);
    if (mic.enabled) {
      mic.stop();
      speech.cancel();
      // Hands-free without a mic is a contradiction, and leaving it on
      // would silently swallow the next session's first utterance.
      setHandsFree(false);
      disarmWakeWindow();
      return;
    }
    mic.start();
    // Turning the mic on is the gesture that unblocks speech synthesis, and
    // wanting to talk to it implies wanting to hear it back.
    setVoiceReplies(true);
  }

  async function handleSelectModel(id: string) {
    setError(null);
    setNotice(null);
    setSwapping(true);
    try {
      setActiveModelState(await setActiveModel(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the model.");
    } finally {
      setSwapping(false);
    }
  }

  async function handleResetModel() {
    setError(null);
    setNotice(null);
    setSwapping(true);
    try {
      setActiveModelState(await clearActiveModel());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reset the model.");
    } finally {
      setSwapping(false);
    }
  }

  function handleToggleVoiceReplies() {
    setVoiceReplies((on) => {
      if (on) speech.cancel();
      return !on;
    });
  }

  const activeLabel = personaLabel(personas, persona);

  // Order matters. "dropped" deliberately outranks "thinking": the reply in
  // flight is the *reason* the utterance was dropped, so reporting "thinking"
  // instead would leave you waiting for an answer to a question that was
  // never sent. Driven by `mic.enabled`, not `mic.listening`, because Chrome
  // restarts recognition constantly and the label must not flicker.
  const voiceState: VoiceState = !mic.supported
    ? "unsupported"
    : droppedUtterance
      ? "dropped"
      : speech.speaking
        ? "speaking"
        : pending
          ? "thinking"
          : !mic.enabled
            ? "off"
            : handsFree && !wakeArmed
              ? "waiting"
              : "listening";

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
          <div className="flex items-center gap-3">
            <PersonaSwitcher personas={personas} value={persona} onChange={setPersona} disabled={pending} />
            <ModelChip
              active={activeModel}
              available={availableModels}
              busy={swapping}
              onSelect={handleSelectModel}
              onReset={handleResetModel}
            />
            <Link
              href="/memory"
              className="text-xs font-semibold uppercase tracking-wide text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
            >
              Memory
            </Link>
          </div>
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
        <VoiceControls
          state={voiceState}
          micOn={mic.enabled}
          handsFree={handsFree}
          onToggleHandsFree={handleToggleHandsFree}
          interim={mic.interim}
          error={mic.error}
          persona={persona}
          personaLabel={activeLabel}
          voiceReplies={voiceReplies}
          outputSupported={speech.supported}
          onToggleMic={handleToggleMic}
          onToggleVoiceReplies={handleToggleVoiceReplies}
          onStopSpeaking={speech.cancel}
        />
        <ChatInput disabled={pending} personaLabel={activeLabel} onSend={handleSend} />
      </div>
    </div>
  );
}
