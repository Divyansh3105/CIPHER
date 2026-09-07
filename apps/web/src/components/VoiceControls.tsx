"use client";

// Phase 4: the voice status line, between the transcript and the composer.
//
// Its whole job is saying whose turn it is. In a voice loop the user cannot
// see who the machine thinks is talking, and every confusing moment in
// testing came down to that -- speaking when you thought it was listening,
// listening while it was still reading the last answer aloud. So the state
// is always on screen, in words, at a size that is readable from across a
// desk rather than at the 11px the rest of the chrome uses.
//
// The microphone button is NOT here: it lives in the composer, and is driven
// by the mic switch rather than by this state machine. That separation is
// load-bearing -- see ChatInput.

import Icon from "@/components/Icon";
import type { Persona } from "@/lib/personas";
import { WAKE_WORD } from "@/lib/speech";

export type VoiceState =
  | "unsupported"
  | "off"
  | "listening"
  | "thinking"
  | "speaking"
  | "dropped"
  // Hands-free is on but nothing has been addressed to us yet: heard,
  // not listened to. Distinct from "listening" because that difference
  // is exactly what the user needs to know before they start talking.
  | "waiting";

const DOT_CLASS: Record<VoiceState, string> = {
  unsupported: "bg-zinc-600",
  off: "bg-zinc-600",
  dropped: "bg-amber-400",
  waiting: "bg-zinc-500",
  listening: "bg-emerald-500",
  thinking: "bg-amber-500",
  speaking: "bg-indigo-500",
};

// Only the states where something is actively happening get the ping halo.
// An idle mic that pulses reads as listening when it is not.
const PINGS: Record<VoiceState, boolean> = {
  unsupported: false,
  off: false,
  dropped: false,
  waiting: false,
  listening: true,
  thinking: true,
  speaking: true,
};

const TEXT_CLASS: Record<VoiceState, string> = {
  unsupported: "text-zinc-500",
  off: "text-zinc-400",
  dropped: "text-amber-200",
  waiting: "text-zinc-400",
  listening: "text-zinc-100",
  thinking: "text-zinc-100",
  speaking: "text-zinc-100",
};

/**
 * The input-level meter.
 *
 * The single most useful thing on this bar when something is wrong, and it
 * was the thing missing when "the mic hears nothing" and "the mic is sending
 * things I never said" looked identical from the outside. A bar that moves
 * when you speak separates a dead input device from a transcription problem
 * in about one second, without opening a console.
 *
 * Rendered only for the server-transcription path: the browser recogniser
 * hands back words and no audio at all, so there is nothing to measure and
 * drawing a bar anyway would be a decoration pretending to be an instrument.
 */
function LevelMeter({ level, noiseFloor }: { level: number; noiseFloor: number | null }) {
  const bars = 5;
  const lit = Math.round(level * bars);
  return (
    <span
      className="flex h-3.5 items-end gap-0.5 border-l border-zinc-800 pl-2.5"
      title={
        noiseFloor === null
          ? "Measuring this microphone's noise floor…"
          : `Input level. Noise floor measured at ${noiseFloor.toFixed(4)} RMS; speech has to exceed about ${(noiseFloor * 3).toFixed(4)} to be captured.`
      }
      aria-hidden
    >
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          className={`w-1 rounded-full transition-colors ${
            i < lit ? "bg-emerald-500" : "bg-zinc-800"
          }`}
          style={{ height: `${5 + i * 2.2}px` }}
        />
      ))}
    </span>
  );
}

export default function VoiceControls({
  state,
  micOn,
  handsFree,
  onToggleHandsFree,
  interim,
  error,
  persona,
  personaLabel,
  voiceReplies,
  outputSupported,
  onToggleVoiceReplies,
  onStopSpeaking,
  level,
  noiseFloor,
  devices,
  deviceId,
  onSelectDevice,
  sttBackend,
  onToggleSttBackend,
}: {
  state: VoiceState;
  /**
   * Whether the mic switch is on.
   *
   * Passed in rather than derived from `state`, which was the original bug:
   * "thinking" and "speaking" happen when you type too, so deriving it lit
   * the mic button up during an ordinary typed conversation.
   */
  micOn: boolean;
  /** Ignore speech until addressed by name. */
  handsFree: boolean;
  onToggleHandsFree: () => void;
  interim: string;
  error: string | null;
  persona: Persona;
  personaLabel: string;
  voiceReplies: boolean;
  outputSupported: boolean;
  onToggleVoiceReplies: () => void;
  onStopSpeaking: () => void;

  /** Undefined on the browser recogniser, which exposes no audio. */
  level?: number;
  noiseFloor?: number | null;
  devices?: { id: string; label: string }[];
  deviceId?: string | null;
  onSelectDevice?: (id: string) => void;
  /**
   * Which transcription backend is running, and a way to change it by hand.
   *
   * Previously the server path was reachable only by the browser recogniser
   * failing outright with `error: "network"`. That covers the browsers where
   * it does not work at all, and none of the ones where it works badly --
   * dropping words, stopping after a minute, mishearing a name every time.
   * There was no way out of a backend that was merely bad, which is a worse
   * trap than one that is plainly broken.
   */
  sttBackend: "browser" | "whisper";
  onToggleSttBackend: () => void;
}) {
  function statusText(): string {
    if (state === "unsupported") {
      return "Voice input is unavailable in this browser — everything still works by typing.";
    }
    if (error) return error;
    switch (state) {
      case "off":
        return `Microphone off — click the mic to talk to ${personaLabel}.`;
      case "waiting":
        return `Hands-free — say “Hey ${WAKE_WORD}” to get ${personaLabel}’s attention.`;
      case "dropped":
        return `Didn't catch that, ${personaLabel} was still answering — say it again.`;
      case "listening":
        if (interim) return `Listening — “${interim}”`;
        return handsFree
          ? `Go ahead — no need to say “${WAKE_WORD}” again for a moment.`
          : "Listening… keep going, a short pause won’t cut you off.";
      case "thinking":
        return `${personaLabel} is thinking…`;
      case "speaking":
        return `${personaLabel} is speaking — say “stop” to cut in.`;
    }
  }

  const dropped = state === "dropped";

  return (
    <div className="shrink-0 border-b border-zinc-800/80 bg-zinc-900/90 px-6 py-2.5">
      <div
        className={[
          "flex items-center justify-between gap-4",
          dropped
            ? "rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-1"
            : "",
        ].join(" ")}
      >
        <div className="flex min-w-0 items-center gap-3">
          {dropped ? (
            <span className="shrink-0 rounded bg-amber-500/20 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-amber-400">
              Dropped input
            </span>
          ) : (
            <span className="relative flex h-3 w-3 shrink-0">
              {PINGS[state] && (
                <span
                  className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${DOT_CLASS[state]}`}
                />
              )}
              <span className={`relative inline-flex h-3 w-3 rounded-full ${DOT_CLASS[state]}`} />
            </span>
          )}

          {/* The one deliberately large string in the chrome. */}
          <p
            className={[
              "truncate font-mono text-[15px] font-semibold tracking-tight",
              error ? "text-red-400" : TEXT_CLASS[state],
            ].join(" ")}
            role="status"
            aria-live="polite"
          >
            {statusText()}
          </p>

          {micOn && level !== undefined && state !== "speaking" && (
            <LevelMeter level={level} noiseFloor={noiseFloor ?? null} />
          )}

          {state === "speaking" && (
            <span className="flex h-3.5 items-center gap-1 border-l border-zinc-800 pl-2.5" aria-hidden>
              <span className="h-2 w-1 animate-pulse rounded-full bg-indigo-400/70" />
              <span className="h-3.5 w-1 animate-pulse rounded-full bg-indigo-400" />
              <span className="h-1.5 w-1 animate-pulse rounded-full bg-indigo-400/60" />
              <span className="h-3 w-1 animate-pulse rounded-full bg-indigo-400" />
              <span className="h-2 w-1 animate-pulse rounded-full bg-indigo-400/80" />
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 font-mono text-[11px]">
          {micOn && devices !== undefined && devices.length > 1 && (
            <select
              value={deviceId ?? ""}
              onChange={(event) => onSelectDevice?.(event.target.value)}
              aria-label="Microphone input device"
              title="Which microphone to listen on"
              className="max-w-[9rem] truncate rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-zinc-400 transition-colors hover:text-zinc-200 focus:border-zinc-600 focus:outline-none"
            >
              {devices.map((device) => (
                <option key={device.id} value={device.id}>
                  {device.label}
                </option>
              ))}
            </select>
          )}

          <button
            type="button"
            onClick={onToggleSttBackend}
            title={
              sttBackend === "whisper"
                ? "Transcribing on the server (Whisper). Click to try this browser's own speech service."
                : "Using this browser's speech service. Click to transcribe on the server instead — more reliable outside Google Chrome."
            }
            className="rounded border border-zinc-800 bg-zinc-900 px-2 py-0.5 text-zinc-500 transition-colors hover:text-zinc-300"
          >
            {sttBackend === "whisper" ? "Server STT" : "Browser STT"}
          </button>

          {state === "speaking" && (
            <button
              type="button"
              onClick={onStopSpeaking}
              className="flex items-center gap-1 rounded border border-zinc-700 bg-zinc-850 px-2 py-0.5 text-zinc-300 transition-colors hover:border-zinc-600 hover:text-zinc-100"
            >
              <Icon name="stop" className="h-3 w-3" strokeWidth={2} />
              Stop
            </button>
          )}

          <button
            type="button"
            onClick={onToggleHandsFree}
            disabled={!micOn}
            aria-pressed={handsFree && micOn}
            title={`Ignore speech until you say “Hey ${WAKE_WORD}”`}
            className={[
              "flex items-center gap-1.5 rounded border px-2 py-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              handsFree && micOn
                ? "border-zinc-700 bg-zinc-850 text-zinc-200"
                : "border-zinc-800 bg-zinc-900 text-zinc-500 hover:text-zinc-300",
            ].join(" ")}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                handsFree && micOn ? "bg-emerald-500" : "bg-zinc-700"
              }`}
            />
            Wake word
          </button>

          <button
            type="button"
            onClick={onToggleVoiceReplies}
            disabled={!outputSupported}
            aria-pressed={voiceReplies && outputSupported}
            title={
              outputSupported
                ? `Read replies aloud in ${personaLabel}'s voice`
                : "This browser has no speech synthesis voices."
            }
            className={[
              "flex items-center gap-1.5 rounded border px-2 py-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              voiceReplies && outputSupported
                ? "border-zinc-700 bg-zinc-850 text-zinc-200"
                : "border-zinc-800 bg-zinc-900 text-zinc-500 hover:text-zinc-300",
            ].join(" ")}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                voiceReplies && outputSupported ? "bg-emerald-500" : "bg-zinc-700"
              }`}
            />
            Speak replies
            <span className="sr-only">{` in ${persona}'s voice`}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
