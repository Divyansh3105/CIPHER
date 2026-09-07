"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import Icon from "@/components/Icon";

// Grows with the message up to this, then scrolls. Past roughly eight lines
// the composer starts eating the transcript it is a reply to.
const MAX_HEIGHT_PX = 200;

export default function ChatInput({
  disabled,
  personaLabel,
  onSend,
  micOn,
  micSupported,
  onToggleMic,
  screenSupported,
  screenSharing,
  screenStatus,
  onToggleScreenShare,
}: {
  disabled: boolean;
  personaLabel: string;
  onSend: (content: string) => void;
  /**
   * The microphone switch — NOT the voice state machine.
   *
   * This has been wrong before precisely because it was derived from the
   * voice state: "thinking" and "speaking" happen during a typed
   * conversation too, so the button lit up when no microphone was open.
   */
  micOn: boolean;
  micSupported: boolean;
  onToggleMic: () => void;
  screenSupported: boolean;
  screenSharing: boolean;
  screenStatus: string;
  onToggleScreenShare: () => void;
}) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="p-4">
      <div className="mx-auto flex max-w-4xl flex-col rounded-lg border border-zinc-800 bg-zinc-900 transition-colors focus-within:border-zinc-700">
        <textarea
          ref={textareaRef}
          rows={2}
          placeholder={`Ask ${personaLabel}, switch persona, or pin a model…`}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          className="w-full resize-none bg-transparent px-3.5 pt-3 pb-2 font-sans text-[14px] leading-relaxed text-zinc-100 outline-none placeholder:text-zinc-500 disabled:opacity-50"
        />

        <div className="flex items-center justify-between gap-3 border-t border-zinc-800/60 px-3 py-2 font-mono text-[12px]">
          <div className="flex min-w-0 items-center gap-2 text-zinc-500">
            {screenSupported && (
              <>
                <button
                  type="button"
                  onClick={onToggleScreenShare}
                  aria-pressed={screenSharing}
                  title={screenSharing ? "Stop sharing your screen" : "Share a window and ask about it"}
                  className={[
                    "flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 transition-colors",
                    screenSharing
                      ? "border-red-500/50 bg-red-950/60 text-red-300 hover:bg-red-900/60"
                      : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200",
                  ].join(" ")}
                >
                  <Icon name="screen" className="h-3.5 w-3.5" />
                  <span className="text-[11px]">{screenSharing ? "Sharing" : "Screen"}</span>
                </button>
                <span className="text-zinc-700">|</span>
              </>
            )}
            <span className="truncate text-[11px]">
              {screenStatus || "Shift + Return for a new line"}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={onToggleMic}
              disabled={!micSupported}
              aria-pressed={micOn}
              aria-label={micOn ? "Turn the microphone off" : "Turn the microphone on"}
              title={
                micSupported
                  ? micOn
                    ? "Microphone on (click to mute)"
                    : "Microphone off (click to talk)"
                  : "Voice input is unavailable in this browser"
              }
              className={[
                "flex items-center gap-1.5 rounded border px-2.5 py-1.5 text-[12px] font-medium transition-all",
                "disabled:cursor-not-allowed disabled:opacity-40",
                micOn
                  ? "border-indigo-500/50 bg-indigo-950/80 text-indigo-300 shadow-sm hover:bg-indigo-900/90"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200",
              ].join(" ")}
            >
              <Icon
                name="mic"
                className={`h-3.5 w-3.5 ${micOn ? "text-indigo-400" : ""}`}
                strokeWidth={2}
              />
              <span className="font-mono text-[11px] tracking-tight">
                {micOn ? "MIC ON" : "MIC OFF"}
              </span>
              {micOn && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-indigo-400" />}
            </button>

            <button
              type="button"
              onClick={submit}
              disabled={disabled || !value.trim()}
              className="flex items-center gap-1.5 rounded bg-zinc-100 px-3 py-1.5 text-[12px] font-medium text-zinc-900 transition-colors hover:bg-white disabled:opacity-40 disabled:hover:bg-zinc-100"
            >
              <span>Send</span>
              <Icon name="send" className="h-3.5 w-3.5" strokeWidth={2.2} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
