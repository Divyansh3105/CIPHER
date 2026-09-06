"use client";

// Phase 4: the voice bar that sits between the transcript and the input box.
//
// Its real job is the status line. In a voice loop the user cannot see who
// the machine thinks is talking, and every confusing moment in testing came
// down to that -- speaking when you thought it was listening, listening
// while it was still reading the last answer aloud. So the state is always
// on screen, in words.

import type { Persona } from "@/lib/personas";

export type VoiceState = "unsupported" | "off" | "listening" | "thinking" | "speaking" | "dropped";

const DOT_CLASSES: Record<VoiceState, string> = {
  unsupported: "bg-zinc-400",
  off: "bg-zinc-400",
  dropped: "bg-red-500",
  listening: "bg-emerald-500 animate-pulse",
  thinking: "bg-amber-500 animate-pulse",
  speaking: "bg-sky-500 animate-pulse",
};

export default function VoiceControls({
  state,
  micOn,
  interim,
  error,
  persona,
  personaLabel,
  voiceReplies,
  outputSupported,
  onToggleMic,
  onToggleVoiceReplies,
  onStopSpeaking,
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
  interim: string;
  error: string | null;
  persona: Persona;
  personaLabel: string;
  voiceReplies: boolean;
  outputSupported: boolean;
  onToggleMic: () => void;
  onToggleVoiceReplies: () => void;
  onStopSpeaking: () => void;
}) {
  function statusText(): string {
    if (state === "unsupported") {
      return "Voice input needs Chrome or Edge — everything else still works by typing.";
    }
    if (error) return error;
    switch (state) {
      case "off":
        return `Click the mic and talk to ${personaLabel}.`;
      case "dropped":
        return `Didn't catch that — ${personaLabel} was still answering. Say it again.`;
      case "listening":
        return interim
          ? `Listening — “${interim}”`
          : "Listening… keep going, a short pause won’t cut you off.";
      case "thinking":
        return `${personaLabel} is thinking…`;
      case "speaking":
        return `${personaLabel} is speaking — say “stop” to cut in.`;
    }
  }

  return (
    <div className="flex items-center gap-3 border-t border-zinc-200 px-4 py-2 dark:border-zinc-800">
      <button
        type="button"
        onClick={onToggleMic}
        disabled={state === "unsupported"}
        aria-pressed={micOn}
        aria-label={micOn ? "Turn the microphone off" : "Turn the microphone on"}
        className={[
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-base transition",
          "disabled:cursor-not-allowed disabled:opacity-40",
          micOn
            ? "border-emerald-500 bg-emerald-500 text-white"
            : "border-zinc-300 text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-300",
        ].join(" ")}
      >
        {micOn ? "◉" : "🎙"}
      </button>

      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${DOT_CLASSES[state]}`} aria-hidden />
        <p
          className={[
            "truncate text-xs",
            error ? "text-red-600 dark:text-red-400" : "text-zinc-500 dark:text-zinc-400",
          ].join(" ")}
          // Announced to screen readers as it changes, since the whole point
          // of this line is telling you whose turn it is.
          role="status"
          aria-live="polite"
        >
          {statusText()}
        </p>
      </div>

      {state === "speaking" && (
        <button
          type="button"
          onClick={onStopSpeaking}
          className="shrink-0 rounded-lg border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-600 hover:border-zinc-500 dark:border-zinc-700 dark:text-zinc-300"
        >
          Stop
        </button>
      )}

      <label
        className={[
          "flex shrink-0 items-center gap-1.5 text-xs",
          outputSupported ? "text-zinc-500 dark:text-zinc-400" : "text-zinc-400 opacity-50",
        ].join(" ")}
        title={
          outputSupported
            ? `Read replies aloud in ${personaLabel}'s voice`
            : "This browser has no speech synthesis voices."
        }
      >
        <input
          type="checkbox"
          className="accent-zinc-900 dark:accent-zinc-100"
          checked={voiceReplies && outputSupported}
          disabled={!outputSupported}
          onChange={onToggleVoiceReplies}
        />
        Speak replies
        <span className="sr-only">{` in ${persona}'s voice`}</span>
      </label>
    </div>
  );
}
