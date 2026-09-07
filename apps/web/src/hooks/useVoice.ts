"use client";

// Phase 4: React lifecycle around the wrappers in @/lib/speech.
//
// Two hooks, deliberately separate: recognition and synthesis have nothing
// in common except that they fight over the same room. The one thing they
// must agree on is echo suppression -- see `suspend`/`resume` below.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  createTranscriptBuffer,
  isSpeechInputSupported,
  isSpeechOutputSupported,
  getSpeechRecognitionCtor,
  speakableText,
  type SpeechRecognitionLike,
  type TranscriptBuffer,
} from "@/lib/speech";
import { pickVoice, VOICE_PROFILES, type Persona } from "@/lib/personas";

// Feature detection read through useSyncExternalStore rather than an effect.
// `window` does not exist during SSR, so probing in render would crash on the
// server, and probing in an effect means an extra render plus a lint error
// (react-hooks/set-state-in-effect). This says the honest thing instead: the
// server never has speech, the client answers for itself, and the answer
// never changes for the life of the page.
const NEVER_CHANGES = () => () => {};
const UNSUPPORTED_ON_SERVER = () => false;

export interface SpeechInput {
  supported: boolean;
  /**
   * Whether the user has the mic switched on.
   *
   * Not the same as `listening`, and the difference matters for the UI:
   * Chrome ends and restarts recognition constantly during normal use, so
   * `listening` flickers false several times a minute. Drive buttons and
   * status off `enabled`; use `listening` only to show the live mic state.
   */
  enabled: boolean;
  listening: boolean;
  /** Live, not-yet-final words, for the status line. */
  interim: string;
  error: string | null;
  start: () => void;
  stop: () => void;
  /** Cut the mic while the assistant talks, so it can't hear itself. */
  suspend: () => void;
  resume: () => void;
}

export function useSpeechInput({
  onUtterance,
  onInterrupt,
  onFatal,
}: {
  onUtterance: (text: string) => void;
  onInterrupt: () => void;
  /**
   * Called when this backend cannot work at all in this browser, so the
   * caller can fall back rather than showing an error and stopping.
   */
  onFatal?: (reason: string) => void;
}): SpeechInput {
  const supported = useSyncExternalStore(NEVER_CHANGES, isSpeechInputSupported, UNSUPPORTED_ON_SERVER);
  const [enabled, setEnabled] = useState(false);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  // What the *user* wants, as opposed to whether the recogniser happens to
  // be running. Chrome ends recognition on its own after a stretch of
  // silence, so `onend` has to know whether that was intentional.
  const wantListeningRef = useRef(false);
  const suspendedRef = useRef(false);

  // Held in refs so the recogniser's handlers, which are attached once, always
  // call the current render's callbacks instead of the first render's.
  const onUtteranceRef = useRef(onUtterance);
  const onInterruptRef = useRef(onInterrupt);
  const onFatalRef = useRef(onFatal);
  useEffect(() => {
    onUtteranceRef.current = onUtterance;
    onInterruptRef.current = onInterrupt;
    onFatalRef.current = onFatal;
  }, [onUtterance, onInterrupt, onFatal]);

  // Built on first use rather than during render: it closes over refs, and
  // reading a ref while rendering is exactly what react-hooks/refs forbids.
  const bufferRef = useRef<TranscriptBuffer | null>(null);
  const getBuffer = useCallback((): TranscriptBuffer => {
    bufferRef.current ??= createTranscriptBuffer({
      onUtterance: (text) => {
        setInterim("");
        onUtteranceRef.current(text);
      },
      onInterrupt: () => {
        setInterim("");
        onInterruptRef.current();
      },
    });
    return bufferRef.current;
  }, []);

  const ensureRecognition = useCallback((): SpeechRecognitionLike | null => {
    if (recognitionRef.current) return recognitionRef.current;

    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) return null;

    const recognition = new Ctor();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.lang = typeof navigator !== "undefined" ? navigator.language || "en-US" : "en-US";

    recognition.onstart = () => setListening(true);

    recognition.onresult = (event) => {
      let pendingInterim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) getBuffer().pushFinal(transcript);
        else pendingInterim += transcript;
      }
      setInterim(pendingInterim);
    };

    recognition.onerror = (event) => {
      switch (event.error) {
        case "no-speech":
        case "aborted":
          // Routine. `onend` decides whether to restart.
          break;
        case "not-allowed":
        case "service-not-allowed":
          wantListeningRef.current = false;
          setEnabled(false);
          // Not a fallback case: permission was refused, and the recorder
          // path needs the same permission, so switching would just fail
          // again with a less clear message.
          setError("Microphone access was blocked. Allow it in the browser's site settings, then try again.");
          break;
        case "network":
          // Almost never the user's network. Outside Google Chrome this API
          // frequently exists and always fails this way: Chromium forks ship
          // the interface without Google's speech backend credentials, so
          // every attempt reaches nothing. The old message blamed the
          // connection and sent people to check their wifi.
          //
          // Reported through onUnsupported so the caller can switch to the
          // recorder-based path instead of leaving the user with an error.
          wantListeningRef.current = false;
          setEnabled(false);
          setError(
            "This browser's speech service is unreachable — common outside Google Chrome. Switching to server transcription."
          );
          onFatalRef.current?.("network");
          break;
        default:
          setError(`Speech recognition failed (${event.error}).`);
      }
    };

    recognition.onend = () => {
      setListening(false);
      // Chrome stops on its own after silence. Restart only if the user
      // never asked us to stop and we are not muted for playback.
      if (wantListeningRef.current && !suspendedRef.current) {
        try {
          recognition.start();
        } catch {
          // Already starting; the next onend will try again.
        }
      }
    };

    recognitionRef.current = recognition;
    return recognition;
  }, [getBuffer]);

  const start = useCallback(() => {
    const recognition = ensureRecognition();
    if (!recognition) return;
    setError(null);
    wantListeningRef.current = true;
    suspendedRef.current = false;
    setEnabled(true);
    try {
      recognition.start();
    } catch {
      // start() throws if it is already running -- harmless.
    }
  }, [ensureRecognition]);

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    suspendedRef.current = false;
    setEnabled(false);
    // Send whatever was said before the mic went off rather than dropping a
    // half-finished sentence on the floor.
    getBuffer().flush();
    setInterim("");
    recognitionRef.current?.stop();
  }, [getBuffer]);

  const suspend = useCallback(() => {
    if (!wantListeningRef.current) return;
    suspendedRef.current = true;
    // abort(), not stop(): stop() delivers a final result for whatever the
    // mic caught, and what it caught is the assistant's own voice.
    getBuffer().reset();
    setInterim("");
    recognitionRef.current?.abort();
  }, [getBuffer]);

  const resume = useCallback(() => {
    if (!wantListeningRef.current || !suspendedRef.current) return;
    suspendedRef.current = false;
    try {
      recognitionRef.current?.start();
    } catch {
      // Already running.
    }
  }, []);

  useEffect(
    () => () => {
      wantListeningRef.current = false;
      bufferRef.current?.reset();
      recognitionRef.current?.abort();
    },
    []
  );

  return { supported, enabled, listening, interim, error, start, stop, suspend, resume };
}

export interface SpeechOutput {
  supported: boolean;
  speaking: boolean;
  speak: (markdown: string, persona: Persona) => void;
  cancel: () => void;
}

export function useSpeechOutput({
  onStart,
  onEnd,
}: {
  onStart?: () => void;
  onEnd?: () => void;
} = {}): SpeechOutput {
  const supported = useSyncExternalStore(NEVER_CHANGES, isSpeechOutputSupported, UNSUPPORTED_ON_SERVER);
  const [speaking, setSpeaking] = useState(false);
  // A ref, not state: the voice list is never rendered, only read at the
  // moment we speak. Keeping it out of state also sidesteps the fact that
  // getVoices() returns a fresh array each call.
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);

  const onStartRef = useRef(onStart);
  const onEndRef = useRef(onEnd);
  useEffect(() => {
    onStartRef.current = onStart;
    onEndRef.current = onEnd;
  }, [onStart, onEnd]);

  useEffect(() => {
    if (!isSpeechOutputSupported()) return;

    // getVoices() is empty on first call in Chrome and populated
    // asynchronously, so read it both now and on the change event.
    const load = () => {
      voicesRef.current = window.speechSynthesis.getVoices();
    };
    load();
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", load);
  }, []);

  const cancel = useCallback(() => {
    if (!isSpeechOutputSupported()) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, []);

  const speak = useCallback(
    (markdown: string, persona: Persona) => {
      if (!isSpeechOutputSupported()) return;
      const text = speakableText(markdown);
      if (!text) return;

      window.speechSynthesis.cancel();

      const utterance = new SpeechSynthesisUtterance(text);
      const profile = VOICE_PROFILES[persona];
      const voice = pickVoice(voicesRef.current, profile);
      if (voice) {
        utterance.voice = voice;
        utterance.lang = voice.lang;
      } else {
        utterance.lang = profile.lang;
      }
      utterance.rate = profile.rate;
      utterance.pitch = profile.pitch;

      const finish = () => {
        setSpeaking(false);
        onEndRef.current?.();
      };
      utterance.onstart = () => {
        setSpeaking(true);
        onStartRef.current?.();
      };
      utterance.onend = finish;
      // Fires on cancel() too, which is exactly when the mic must come back.
      utterance.onerror = finish;

      window.speechSynthesis.speak(utterance);
    },
    []
  );

  useEffect(
    () => () => {
      if (isSpeechOutputSupported()) window.speechSynthesis.cancel();
    },
    []
  );

  return { supported, speaking, speak, cancel };
}
