"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import ConversationSidebar from "@/components/ConversationSidebar";
import MessageList from "@/components/MessageList";
import ChatInput from "@/components/ChatInput";
import NoticeStrip, { type NoticeTone } from "@/components/NoticeStrip";
import PersonaSwitcher from "@/components/PersonaSwitcher";
import VoiceControls, { type VoiceState } from "@/components/VoiceControls";
import ModelChip from "@/components/ModelChip";
import { useSpeechInput, useSpeechOutput } from "@/hooks/useVoice";
import { useWhisperInput } from "@/hooks/useWhisperInput";
import { matchWakePhrase, WAKE_FOLLOW_UP_MS } from "@/lib/speech";
import { useScreenShare } from "@/hooks/useScreenShare";
import {
  type ActiveModel,
  ApiError,
  askAboutImage,
  type ChatMessage,
  type ConversationSummary,
  clearActiveModel,
  deleteConversation,
  getConversation,
  listConversations,
  listModels,
  listPersonas,
  type ModelInfo,
  sendMessage,
  setActiveModel,
} from "@/lib/api";
import { DEFAULT_PERSONA, FALLBACK_PERSONAS, type Persona, personaLabel } from "@/lib/personas";

/** A notice carries its own tone, so the amber strip keeps meaning
    "degraded" rather than becoming the channel for every message. */
interface Notice {
  tone: NoticeTone;
  text: string;
}

//: Which transcription backend to open the microphone on, remembered across
//: sessions. Deliberately localStorage and not the database: it is a fact
//: about this browser on this machine, not about the user, and syncing it to
//: an account would carry a Brave laptop's answer over to a Chrome desktop
//: where the browser service works perfectly well.
const STT_BACKEND_KEY = "cipher.stt-backend";

type SttBackend = "browser" | "whisper";

// Read through useSyncExternalStore rather than an effect, the same way
// useVoice reads feature detection. localStorage does not exist during SSR,
// so probing in render would crash on the server, and probing in an effect
// is a synchronous setState in an effect body -- which React now rejects
// outright. This says the honest thing instead: the server has no stored
// preference, the client answers for itself, and the answer only changes
// when something here changes it.
let sttBackendCache: SttBackend | null = null;
const sttBackendListeners = new Set<() => void>();

function readSttBackend(): SttBackend {
  if (sttBackendCache === null) {
    try {
      const saved = window.localStorage.getItem(STT_BACKEND_KEY);
      sttBackendCache = saved === "whisper" || saved === "browser" ? saved : "browser";
    } catch {
      // Storage can throw outright in private windows. The default stands.
      sttBackendCache = "browser";
    }
  }
  return sttBackendCache;
}

function serverSttBackend(): SttBackend {
  return "browser";
}

function subscribeSttBackend(listener: () => void) {
  sttBackendListeners.add(listener);
  return () => {
    sttBackendListeners.delete(listener);
  };
}

function setSttBackend(backend: SttBackend) {
  sttBackendCache = backend;
  try {
    window.localStorage.setItem(STT_BACKEND_KEY, backend);
  } catch {
    // Private windows can throw on write. Not remembering is survivable.
  }
  for (const listener of sttBackendListeners) listener();
}

export default function Home() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
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

  // Phase 7: screen sharing. The browser owns the capture, so nothing
  // server-side can see the screen, and a frame is only ever taken at
  // the moment a question is asked.
  const screen = useScreenShare();

  // Which speech backend is in use. The browser's is tried first because it
  // is instant and free; the recorder path costs a round trip per utterance
  // but works in browsers where the browser's own service does not exist or
  // cannot be reached -- which is most of them outside Google Chrome.
  const sttBackend = useSyncExternalStore(subscribeSttBackend, readSttBackend, serverSttBackend);
  // Set when the mic was on across a backend change -- either the browser
  // service failing mid-attempt, or a deliberate switch -- so the
  // replacement picks up where it left off. The user clicked the microphone
  // once; making them click again after a change they did not cause, or one
  // they made in a different control, is friction with no purpose.
  //
  // A ref, not state: nothing renders from it, and clearing it inside the
  // effects below would be a synchronous setState in an effect body.
  const resumeAfterSwitchRef = useRef(false);

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

  // The spoken command grammar, and it is deliberately narrow.
  //
  // This used to be `/^(switch|swap|change|go back)\b/`, which matched the
  // FIRST WORD and nothing else -- so "change the wording of that", "switch
  // the order of those two" and "change that to a shorter version" were all
  // silently consumed as attempts to swap the model, sent to the registry,
  // refused, and never delivered as messages. Saying an ordinary sentence
  // and watching the app do something unrelated is exactly that bug.
  //
  // The real grammar is `<verb> [back] to <target>`: the "to" has to follow
  // the verb directly. "change the wording" has no target and is a message;
  // "change to ULTRON" has one and is a command.
  const SWAP_COMMAND = /^\s*(?:switch|swap|change|go back)\s+(?:back\s+)?to\s+(.+?)[.!?]*\s*$/i;

  // A target longer than this is a sentence, not a name. "switch to using
  // shorter sentences from now on please" is something you say to an
  // assistant, not a model id.
  const MAX_TARGET_WORDS = 6;

  /** Every name the backend would accept, lowercased, for detection only. */
  function knownTargets(): string[] {
    const names = ["auto", "default"];
    for (const model of availableModels) {
      names.push(model.id, model.display_name, ...model.aliases);
    }
    for (const p of personas) names.push(p.id, p.display_name);
    return names.map((n) => n.toLowerCase()).filter(Boolean);
  }

  /** Returns true when the utterance was handled as a command, not a message. */
  async function handleSpokenCommand(text: string): Promise<boolean> {
    const match = text.match(SWAP_COMMAND);
    if (!match) return false;

    const target = match[1].trim();
    if (target.split(/\s+/).length > MAX_TARGET_WORDS) return false;

    // Detection, not resolution. The client only decides "is this a command
    // at all"; which model a name means stays the backend registry's single
    // decision (app/llm/registry.py), which is why the whole spoken string
    // is still handed to it below rather than a name resolved here.
    const lowered = target.toLowerCase();
    if (!knownTargets().some((name) => lowered.includes(name))) return false;

    // Personas first: "switch to FRIDAY" is about voice and tone, and would
    // otherwise be refused by the model registry with a confusing message
    // about which models exist.
    // Double-escaped on purpose: inside a template literal a single
    // backslash-b is a backspace character, so the word-boundary anchors
    // would silently never match and every spoken persona switch would
    // fall through to the model registry and be refused.
    const wanted = personas.find((p) => new RegExp(`\\b${p.id}\\b`, "i").test(target));
    if (wanted) {
      setPersona(wanted.id);
      // Quotes what was heard, not just what was done. When speech is
      // mis-transcribed the only way to tell is seeing the words the app
      // acted on -- "Switched to FRIDAY" alone hides the misheard input.
      setNotice({ tone: "zinc", text: `Heard “${text}” — switched to ${wanted.display_name}.` });
      return true;
    }

    setSwapping(true);
    try {
      const active = await setActiveModel(text);
      setActiveModelState(active);
      setNotice({
        tone: "zinc",
        text: active.pinned
          ? `Heard “${text}” — now thinking on ${active.display_name}. It will not fall back to another model.`
          : `Heard “${text}” — back to default routing (${active.default_id}).`,
      });
    } catch (err) {
      // The backend refused rather than guessing at the nearest model, and
      // its message lists what actually exists -- show it verbatim.
      setError(err instanceof ApiError ? err.message : "Could not change the model.");
    } finally {
      setSwapping(false);
    }
    return true;
  }

  // Both hooks are always mounted: React requires a stable hook order, and
  // whichever is not selected simply never gets started, so it holds no
  // microphone and does no work.
  const handleUtterance = (text: string) => {
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
  };

  const handleInterrupt = () => speech.cancel();

  const browserMic = useSpeechInput({
    onUtterance: handleUtterance,
    onInterrupt: handleInterrupt,
    // The browser's speech service is unreachable in this browser. Switch
    // rather than surface an error: the user asked to talk, and there is a
    // backend that works.
    onFatal: () => {
      setSttBackend("whisper");
      resumeAfterSwitchRef.current = true;
      setNotice({
        tone: "amber",
        text: "This browser's speech service is unavailable, so speech is now being transcribed on the server instead.",
      });
    },
  });

  const whisperMic = useWhisperInput({
    onUtterance: handleUtterance,
    onInterrupt: handleInterrupt,
  });

  const mic = sttBackend === "whisper" ? whisperMic : browserMic;

  // Start the replacement once it is the selected backend. Done in an effect
  // rather than inside onFatal because the switch is a state change: at the
  // moment onFatal runs, `mic` is still the backend that just failed, and
  // starting it again would fail again.
  useEffect(() => {
    if (sttBackend !== "whisper" || !resumeAfterSwitchRef.current) return;
    resumeAfterSwitchRef.current = false;
    whisperMic.start();
  }, [sttBackend, whisperMic]);

  // The same, in the other direction, for a deliberate switch back.
  useEffect(() => {
    if (sttBackend !== "browser" || !resumeAfterSwitchRef.current) return;
    resumeAfterSwitchRef.current = false;
    browserMic.start();
  }, [sttBackend, browserMic]);

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

  async function handleDeleteConversation(conversation: ConversationSummary) {
    const title = conversation.title || "this conversation";
    // Confirmed in the browser because it cannot be undone -- there is no
    // trash and no restore. Matches the memory dashboard's "Forget
    // everything", which is the other irreversible action in the app.
    if (!window.confirm(`Delete “${title}”? Its messages are gone for good.`)) return;

    setError(null);
    setNotice(null);
    setDeletingId(conversation.id);
    try {
      const result = await deleteConversation(conversation.id);
      setConversations((prev) => prev.filter((c) => c.id !== conversation.id));
      // Deleting the thread you are reading has to clear the thread too,
      // or the transcript stays on screen pointing at a conversation the
      // next message would fail to append to.
      if (activeId === conversation.id) {
        setActiveId(null);
        setMessages([]);
      }
      setNotice({
        tone: "zinc",
        text:
          result.agent_runs_detached > 0
            ? `Deleted “${title}”. Its ${result.agent_runs_detached} agent ${result.agent_runs_detached === 1 ? "run" : "runs"} stay in the activity trail.`
            : `Deleted “${title}”.`,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that conversation.");
    } finally {
      setDeletingId(null);
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

    // While a share is live, questions go to the vision endpoint with a
    // freshly grabbed frame. If the share has ended, that is said plainly
    // rather than answered from an old frame or from memory.
    if (screen.sharing) {
      const frame = await screen.capture();
      if (!frame) {
        setNotice({
          tone: "amber",
          text: "The screen share ended, so there was nothing to look at. Start it again to ask about your screen.",
        });
        return;
      }
      const optimistic: ChatMessage = {
        id: `pending-${crypto.randomUUID()}`,
        role: "user",
        content,
        persona,
        created_at: new Date().toISOString(),
        recalled_memories: [],
        citations: [],
      };
      setMessages((prev) => [...prev, optimistic]);
      pendingRef.current = true;
      setPending(true);
      try {
        const seen = await askAboutImage(frame, content, persona);
        setMessages((prev) => [
          ...prev,
          {
            id: `vision-${crypto.randomUUID()}`,
            role: "assistant",
            content: seen.answer,
            persona,
            created_at: new Date().toISOString(),
            recalled_memories: [],
            citations: [],
          },
        ]);
        if (voiceReplies) speech.speak(seen.answer, persona);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not look at that screen.");
      } finally {
        pendingRef.current = false;
        setPending(false);
      }
      return;
    }

    const optimisticUserMessage: ChatMessage = {
      id: `pending-${crypto.randomUUID()}`,
      role: "user",
      content,
      persona,
      created_at: new Date().toISOString(),
      recalled_memories: [],
      citations: [],
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
      if (result.activity && !result.agent_used) {
        // A specialist was attempted and failed. Saying so matters more than
        // it looks: the reply that follows is ungrounded, and the user would
        // otherwise assume it had been looked up.
        setNotice({
          tone: "amber",
          text: `Couldn't look that up — ${result.activity}. Answered without it.`,
        });
      } else if (result.filtered) {
        setNotice({
          tone: "red",
          text: `${personaLabel(personas, result.message.persona)}'s safety filter replaced that reply — it crossed a line the persona enforces.`,
        });
      } else if (result.fell_back) {
        setNotice({
          tone: "amber",
          text: `Primary model was unavailable — answered on the fallback model (${result.model_used}).`,
        });
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

  function handleToggleSttBackend() {
    const next = sttBackend === "whisper" ? "browser" : "whisper";
    // Carry the mic across the switch. Making someone click the microphone
    // again after changing a setting is friction with no purpose -- the same
    // reasoning as the automatic fallback below.
    resumeAfterSwitchRef.current = mic.enabled;
    if (mic.enabled) mic.stop();
    setSttBackend(next);
    setNotice({
      tone: "zinc",
      text:
        next === "whisper"
          ? "Speech is now transcribed on the server. This is the reliable path outside Google Chrome."
          : "Speech is now transcribed by this browser's own speech service. It is instant, and it does not work in every browser.",
    });
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
  const activeTagline = personas.find((p) => p.id === persona)?.tagline ?? "";

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

  const screenStatus = screen.error
    ? screen.error
    : screen.sharing
      ? `${activeLabel} can see the shared window — every question grabs a fresh frame.`
      : "";

  return (
    <div className="flex h-full w-full overflow-hidden">
      <ConversationSidebar
        conversations={conversations}
        activeId={activeId}
        personas={personas}
        busyId={deletingId}
        onSelect={handleSelectConversation}
        onNewChat={handleNewChat}
        onDelete={handleDeleteConversation}
      />

      <main className="relative flex h-full flex-1 flex-col overflow-hidden bg-zinc-950">
        <header className="z-20 flex h-12 shrink-0 select-none items-center justify-between border-b border-zinc-800 bg-zinc-925 px-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="hidden font-mono text-[11px] uppercase tracking-wider text-zinc-500 sm:inline-block">
              Persona:
            </span>
            <PersonaSwitcher
              personas={personas}
              value={persona}
              onChange={setPersona}
              disabled={pending}
            />
            {/* Hidden below xl rather than md: at 1024 it truncates to a few
                characters and sits flush against the model chip, which reads
                as an overlap. */}
            {activeTagline && (
              <span className="hidden truncate border-l border-zinc-800 pl-3 font-mono text-[11px] text-zinc-500 xl:inline-block">
                {activeTagline}
              </span>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <ModelChip
              active={activeModel}
              available={availableModels}
              busy={swapping}
              onSelect={handleSelectModel}
              onReset={handleResetModel}
            />
          </div>
        </header>

        {/* Degraded-state strips sit directly under the header, above the
            thread — where they are read before the reply they qualify. */}
        {notice && <NoticeStrip tone={notice.tone}>{notice.text}</NoticeStrip>}
        {error && <NoticeStrip tone="red">{error}</NoticeStrip>}

        <MessageList
          messages={messages}
          pending={pending}
          activePersonaLabel={activeLabel}
          personas={personas}
        />

        <div className="flex shrink-0 flex-col border-t border-zinc-800 bg-zinc-925/90">
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
            onToggleVoiceReplies={handleToggleVoiceReplies}
            onStopSpeaking={speech.cancel}
            level={mic.level}
            noiseFloor={mic.noiseFloor}
            devices={mic.devices}
            deviceId={mic.deviceId}
            onSelectDevice={(id) => mic.setDeviceId?.(id)}
            sttBackend={sttBackend}
            onToggleSttBackend={handleToggleSttBackend}
          />
          <ChatInput
            disabled={pending}
            personaLabel={activeLabel}
            onSend={handleSend}
            micOn={mic.enabled}
            micSupported={mic.supported}
            onToggleMic={handleToggleMic}
            screenSupported={screen.supported}
            screenSharing={screen.sharing}
            screenStatus={screenStatus}
            onToggleScreenShare={() => (screen.sharing ? screen.stop() : void screen.start())}
          />
        </div>
      </main>
    </div>
  );
}
