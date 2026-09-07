"use client";

// Speech input that works in any browser (Phase 4, revisited).
//
// `webkitSpeechRecognition` was Phase 4's only input, and it has a failure
// mode that reads as a network fault and is not: outside Google Chrome the
// API frequently exists and always fails with `error: "network"`, because
// Chromium forks ship the interface without Google's speech backend
// credentials. Firefox and Safari do not implement it at all.
//
// This records with MediaRecorder and posts each utterance to
// /voice/transcribe, which uses Groq's hosted Whisper. Same `SpeechInput`
// interface as useSpeechInput, so the page does not know or care which one
// is running.
//
// The hard part is where an utterance ends. The Web Speech API decides that
// itself; here it has to be detected, and the same rule from `FINISH_MS`
// applies: people pause mid-sentence, so a cut on the first quiet moment
// truncates roughly every other utterance. Silence is measured from the
// audio itself and has to persist for the whole finish window before the
// clip is closed and sent.

import { useCallback, useEffect, useRef, useState } from "react";
import { FINISH_MS, isInterrupt } from "@/lib/speech";
import { transcribe } from "@/lib/api";
import type { SpeechInput } from "@/hooks/useVoice";

//: RMS below this counts as silence. Measured against real microphone input
//: rather than guessed: an open mic in a quiet room idles around 0.005-0.01,
//: and speech sits an order of magnitude above it. Too low and background
//: hum reads as talking, so the clip never closes.
const SILENCE_RMS = 0.018;

//: Ignore silence until this much audio exists. Without it the recorder
//: closes immediately, because the moment it opens there is nothing but
//: silence.
const MIN_UTTERANCE_MS = 400;

//: A hard ceiling, so a noisy room cannot record forever.
const MAX_UTTERANCE_MS = 25000;

const ANALYSER_INTERVAL_MS = 100;

export function useWhisperInput({
  onUtterance,
  onInterrupt,
}: {
  onUtterance: (text: string) => void;
  onInterrupt: () => void;
}): SpeechInput {
  const [enabled, setEnabled] = useState(false);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const silenceMsRef = useRef(0);
  const utteranceMsRef = useRef(0);
  // startSegment reopens the recorder from inside its own onstop handler.
  // Calling it directly would close over the binding as it was when the
  // handler was created; going through a ref always reaches the current one,
  // and satisfies the rule against using a value before it is declared.
  const startSegmentRef = useRef<() => void>(() => {});
  const suspendedRef = useRef(false);
  const wantListeningRef = useRef(false);

  const onUtteranceRef = useRef(onUtterance);
  const onInterruptRef = useRef(onInterrupt);
  useEffect(() => {
    onUtteranceRef.current = onUtterance;
    onInterruptRef.current = onInterrupt;
  }, [onUtterance, onInterrupt]);

  const supported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof window.MediaRecorder !== "undefined";

  const teardown = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      // onstop still fires; chunksRef is cleared so it sends nothing.
      chunksRef.current = [];
      recorderRef.current.stop();
    }
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void audioContextRef.current?.close().catch(() => {});
    audioContextRef.current = null;
    analyserRef.current = null;
    setListening(false);
    setInterim("");
  }, []);

  const send = useCallback(async (blob: Blob) => {
    try {
      const text = await transcribe(blob);
      const trimmed = text.trim();
      if (!trimmed) return;
      // Interrupt words are controls, not messages -- same rule as the
      // browser recogniser, so behaviour does not change with the backend.
      if (isInterrupt(trimmed)) {
        onInterruptRef.current();
        return;
      }
      onUtteranceRef.current(trimmed);
    } catch {
      // A failed transcription must not stop the microphone: the next
      // utterance is likely to work, and silently dropping one clip is far
      // better than the mic switching itself off mid-conversation.
      setError("That last bit could not be transcribed. Still listening.");
    }
  }, []);

  const startSegment = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || suspendedRef.current) return;

    const recorder = new MediaRecorder(stream);
    chunksRef.current = [];
    silenceMsRef.current = 0;
    utteranceMsRef.current = 0;

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const chunks = chunksRef.current;
      chunksRef.current = [];
      if (chunks.length > 0) {
        void send(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
      }
      // Immediately open the next segment, so speech during transcription is
      // still captured rather than falling into a gap.
      if (wantListeningRef.current && !suspendedRef.current) startSegmentRef.current();
    };

    recorder.start();
    recorderRef.current = recorder;
    setListening(true);
  }, [send]);

  useEffect(() => {
    startSegmentRef.current = startSegment;
  }, [startSegment]);

  const start = useCallback(async () => {
    if (!supported) {
      setError("This browser cannot record audio.");
      return;
    }
    setError(null);
    wantListeningRef.current = true;
    suspendedRef.current = false;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      setEnabled(true);

      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      context.createMediaStreamSource(stream).connect(analyser);
      audioContextRef.current = context;
      analyserRef.current = analyser;

      const samples = new Float32Array(analyser.fftSize);
      timerRef.current = setInterval(() => {
        const node = analyserRef.current;
        const recorder = recorderRef.current;
        if (!node || !recorder || recorder.state !== "recording") return;

        node.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const sample of samples) sum += sample * sample;
        const rms = Math.sqrt(sum / samples.length);

        utteranceMsRef.current += ANALYSER_INTERVAL_MS;
        silenceMsRef.current = rms < SILENCE_RMS ? silenceMsRef.current + ANALYSER_INTERVAL_MS : 0;
        setInterim(rms < SILENCE_RMS ? "" : "…");

        const longEnough = utteranceMsRef.current >= MIN_UTTERANCE_MS;
        const finished = silenceMsRef.current >= FINISH_MS;
        const tooLong = utteranceMsRef.current >= MAX_UTTERANCE_MS;

        // The same rule as the browser path: a pause only ends the thought
        // once it has lasted the whole finish window.
        if ((longEnough && finished) || tooLong) recorder.stop();
      }, ANALYSER_INTERVAL_MS);

      startSegment();
    } catch {
      wantListeningRef.current = false;
      setEnabled(false);
      setError("Microphone access was blocked. Allow it in the browser's site settings, then try again.");
    }
  }, [supported, startSegment]);

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    suspendedRef.current = false;
    setEnabled(false);
    // Unlike teardown's discard, a deliberate stop sends what was captured
    // rather than dropping a half-finished sentence.
    const recorder = recorderRef.current;
    if (recorder && recorder.state === "recording") recorder.stop();
    teardown();
  }, [teardown]);

  const suspend = useCallback(() => {
    if (!wantListeningRef.current) return;
    suspendedRef.current = true;
    // Discard rather than send: what the mic caught while the assistant was
    // talking is the assistant.
    chunksRef.current = [];
    const recorder = recorderRef.current;
    if (recorder && recorder.state === "recording") recorder.stop();
    setListening(false);
    setInterim("");
  }, []);

  const resume = useCallback(() => {
    if (!wantListeningRef.current || !suspendedRef.current) return;
    suspendedRef.current = false;
    startSegment();
  }, [startSegment]);

  useEffect(() => () => teardown(), [teardown]);

  return {
    supported,
    enabled,
    listening,
    interim,
    error,
    start: () => void start(),
    stop,
    suspend,
    resume,
  };
}
