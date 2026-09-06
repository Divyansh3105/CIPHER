// Phase 4: browser-native speech, both directions.
//
// This module is deliberately the ONLY place that touches the Web Speech
// API. Everything above it (hooks, components) talks to these wrappers, so
// swapping in the blueprint's Whisper + Edge-TTS backend later means adding
// a second implementation of this surface -- not rewriting the UI. That is
// the same LLMProvider trick services/backend/app/llm/base.py plays.
//
// Trade-off taken knowingly (docs/architecture.md Section 5 names Whisper +
// Edge-TTS): `webkitSpeechRecognition` is Chrome/Edge-only and streams audio
// to Google's servers. It costs nothing, needs no install, and works today,
// which is why it ships first. `isSpeechInputSupported()` exists so the UI
// degrades to text instead of pretending.

// --- Minimal structural types ------------------------------------------
//
// Hand-rolled rather than relying on lib.dom, whose SpeechRecognition types
// are still not guaranteed across TS versions. Suffixed `Like` so they can
// never collide with a lib.dom declaration that does exist.

interface SpeechRecognitionAlternativeLike {
  readonly transcript: string;
  readonly confidence: number;
}

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  readonly [index: number]: SpeechRecognitionResultLike;
}

export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

export interface SpeechRecognitionErrorEventLike {
  readonly error: string;
  readonly message: string;
}

export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

/** Returns the vendor-prefixed constructor, or null when unsupported. */
export function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function isSpeechInputSupported(): boolean {
  return getSpeechRecognitionCtor() !== null;
}

export function isSpeechOutputSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

// --- The finish window --------------------------------------------------

/**
 * How long to wait after a "final" result before deciding the user is
 * actually done talking, in milliseconds.
 *
 * This is the single most important number in the voice loop, and the one
 * thing worth tuning by hand. Speech recognition finalises a phrase every
 * time you pause -- but people pause mid-sentence, so dispatching on the
 * first final result cuts you off constantly ("remind me to call the—"
 * sent as a whole message). Instead every final result is buffered and this
 * timer restarts; only the pause that outlasts it ends the thought.
 *
 * Too low and it interrupts you. Too high and it feels sluggish. 900ms is a
 * starting point, not a measured answer: use it for a day of real speech,
 * then change THIS constant rather than sprinkling timings elsewhere.
 */
export const FINISH_MS = 900;

/**
 * Words that cancel whatever the assistant is currently saying instead of
 * being sent to it.
 *
 * Deliberate divergence from the usual advice, which is to dispatch these
 * instantly as messages. Sending "stop" to an LLM produces a reply, which is
 * the opposite of what "stop" means while it is talking over you. Here they
 * bypass the buffer and fire `onInterrupt` locally, and are never sent as
 * chat. Matched only when the utterance is JUST the word, so "stop the
 * backend from restarting" is still a real question.
 */
export const INTERRUPT_WORDS = ["stop", "wait", "quiet", "cancel", "shut up", "enough"];

export function isInterrupt(text: string): boolean {
  const normalised = text
    .toLowerCase()
    .replace(/[^a-z\s]/g, "")
    .trim();
  return INTERRUPT_WORDS.includes(normalised);
}

export interface TranscriptBuffer {
  /** Feed one finalised phrase from the recogniser. */
  pushFinal(text: string): void;
  /** Send whatever is buffered right now (used when the mic is switched off). */
  flush(): void;
  /** Drop anything buffered and cancel the pending timer. */
  reset(): void;
  /** What would be sent if the finish window expired this instant. */
  peek(): string;
}

/**
 * Accumulates finalised phrases and emits them as one utterance once the
 * speaker has been quiet for `finishMs`.
 *
 * Kept free of React and of the Web Speech API on purpose: this is the part
 * with the actual logic in it, so it should be testable without a browser.
 */
export function createTranscriptBuffer({
  finishMs = FINISH_MS,
  onUtterance,
  onInterrupt,
}: {
  finishMs?: number;
  onUtterance: (text: string) => void;
  onInterrupt?: () => void;
}): TranscriptBuffer {
  let parts: string[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function dispatch() {
    clearTimer();
    const text = parts.join(" ").replace(/\s+/g, " ").trim();
    parts = [];
    if (text) onUtterance(text);
  }

  return {
    pushFinal(text: string) {
      const trimmed = text.trim();
      if (!trimmed) return;

      // An interrupt only counts when it stands alone. Mid-utterance it is
      // an ordinary word: "wait" in "wait for the migration to finish"
      // must not silently cancel the reply and vanish from the message.
      if (parts.length === 0 && isInterrupt(trimmed)) {
        clearTimer();
        onInterrupt?.();
        return;
      }

      parts.push(trimmed);
      clearTimer();
      timer = setTimeout(dispatch, finishMs);
    },
    flush() {
      if (parts.length > 0) dispatch();
      else clearTimer();
    },
    reset() {
      clearTimer();
      parts = [];
    },
    peek() {
      return parts.join(" ");
    },
  };
}

// --- Reading model output aloud ----------------------------------------

/** Longest utterance we will hand to speechSynthesis in one go. */
const MAX_SPOKEN_CHARS = 700;

/**
 * Strip an LLM reply down to something worth hearing.
 *
 * Markdown is written to be looked at. Read aloud verbatim, a reply with a
 * fenced code block or a bulleted list turns into "asterisk asterisk star
 * backtick backtick backtick" on some voices and a long unpunctuated run-on
 * on others. Code blocks are dropped entirely and announced instead -- the
 * text is already on screen, which is exactly where code belongs.
 */
export function speakableText(markdown: string): string {
  let text = markdown;

  const hadCodeBlock = /```/.test(text);
  text = text.replace(/```[\s\S]*?```/g, " ");
  text = text.replace(/`([^`]+)`/g, "$1");
  text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, " ");
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  text = text.replace(/^\s{0,3}#{1,6}\s+/gm, "");
  text = text.replace(/^\s*[-*+]\s+/gm, "");
  text = text.replace(/^\s*\d+\.\s+/gm, "");
  text = text.replace(/(\*\*|__|\*|_)/g, "");
  text = text.replace(/^\s*>\s?/gm, "");
  text = text.replace(/\s+/g, " ").trim();

  if (hadCodeBlock) {
    text = text ? `${text} The code is on screen.` : "The code is on screen.";
  }

  if (text.length > MAX_SPOKEN_CHARS) {
    // Cut at the last sentence end inside the budget so it stops on a full
    // stop rather than mid-word.
    const clipped = text.slice(0, MAX_SPOKEN_CHARS);
    const lastStop = Math.max(clipped.lastIndexOf(". "), clipped.lastIndexOf("? "), clipped.lastIndexOf("! "));
    text = (lastStop > MAX_SPOKEN_CHARS / 3 ? clipped.slice(0, lastStop + 1) : clipped.trimEnd()) +
      " The rest is on screen.";
  }

  return text;
}
