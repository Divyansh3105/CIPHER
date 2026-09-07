"use client";

// Speech input that works in any browser (Phase 4, revisited twice).
//
// `webkitSpeechRecognition` was Phase 4's only input, and it has a failure
// mode that reads as a network fault and is not: outside Google Chrome the
// API frequently exists and always fails with `error: "network"`, because
// Chromium forks ship the interface without Google's speech backend
// credentials. Firefox and Safari do not implement it at all. So this path
// is not a fallback for most people -- it is the microphone.
//
// The first version of it recorded fixed segments with MediaRecorder and
// decided an utterance was over whenever 400ms of audio had accumulated and
// 900ms of it was quiet. An idle microphone meets that condition every 1.3
// seconds, forever, which is where all four reported symptoms came from at
// once -- silence uploaded on a loop, the 20/min rate limit gone in about
// 26 seconds, Whisper's filler ("Thank you.", "you") arriving as real chat
// messages, and the first syllable after each pause lost in the gap between
// one recorder stopping and the next starting.
//
// It now captures raw samples continuously and asks @/lib/audio whether
// anyone is talking. Audio is only ever uploaded when speech was heard, the
// threshold is measured against this microphone in this room rather than
// hardcoded, and a rolling pre-roll means the run-up to a word is already
// buffered by the time the word is recognised as one.
//
// Same `SpeechInput` interface as useSpeechInput, so the page does not know
// or care which one is running.

import { useCallback, useEffect, useRef, useState } from "react";
import { FINISH_MS, isInterrupt } from "@/lib/speech";
import { createUtteranceDetector, encodeWav, type UtteranceDetector } from "@/lib/audio";
import { transcribe, ApiError } from "@/lib/api";
import type { SpeechInput } from "@/hooks/useVoice";

//: Whisper's own working rate. Asking the AudioContext for it means no
//: resampling anywhere -- not in the browser, not on the server -- and makes
//: the upload about a third the size of the 48 kHz default for audio the
//: model cannot use the extra bandwidth of anyway.
const TARGET_SAMPLE_RATE = 16000;

//: Frames per callback in the ScriptProcessor fallback. 4096 at 16 kHz is
//: 256ms, which is coarse enough to be cheap and fine enough that the VAD
//: still reacts within one frame.
const FALLBACK_BUFFER_SIZE = 4096;

//: How many consecutive upload failures before the mic gives up and says so.
//: One failure is a blip and must not interrupt a conversation; a run of
//: them means the backend is down or the key is rejected, and silently
//: swallowing that is how the old version looked like a broken microphone.
const MAX_CONSECUTIVE_FAILURES = 3;

/**
 * The worklet is four lines and lives here as a string on purpose.
 *
 * An AudioWorklet module has to be fetched by URL, which normally means a
 * file in `public/` -- a served asset, a path to keep in sync, and one more
 * thing that can 404 in a deployment. A blob URL keeps it next to the code
 * that uses it. `slice(0)` is not optional: the buffer handed to `process`
 * is reused on the next call, so posting it without copying delivers audio
 * that has already been overwritten.
 */
const CAPTURE_WORKLET = `
class CipherCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor('cipher-capture', CipherCaptureProcessor);
`;

interface Capture {
  context: AudioContext;
  stream: MediaStream;
  disconnect: () => void;
}

/**
 * Open the microphone and deliver frames of mono samples.
 *
 * Prefers an AudioWorklet, which runs on the audio thread and cannot be
 * starved by React rendering. Falls back to ScriptProcessorNode -- deprecated
 * but implemented everywhere, and this path is specifically for the browsers
 * that do not implement things.
 */
async function openCapture(
  deviceId: string | null,
  onFrame: (frame: Float32Array) => void
): Promise<Capture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      // Stated rather than left to the browser's defaults. Echo cancellation
      // is what stops the assistant's own replies being transcribed back as
      // user speech through the laptop speakers; the mic is muted while it
      // talks as well, but the two failures overlap and only one of them is
      // under this hook's control.
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
    },
  });

  // A requested rate the hardware cannot honour throws rather than being
  // approximated, so the default-rate context is the fallback. Either way
  // the real rate is read back off the context and travels with the WAV.
  let context: AudioContext;
  try {
    context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  } catch {
    context = new AudioContext();
  }
  // Chrome starts contexts suspended until a gesture; turning the mic on is
  // one, but the resume has to be asked for explicitly.
  if (context.state === "suspended") await context.resume().catch(() => {});

  const source = context.createMediaStreamSource(stream);
  // Every capture node still has to be connected to the destination for the
  // graph to pull audio through it. At zero gain that costs nothing and,
  // crucially, does not play the microphone back through the speakers.
  const sink = context.createGain();
  sink.gain.value = 0;
  sink.connect(context.destination);

  let workletUrl: string | null = null;
  try {
    workletUrl = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: "text/javascript" }));
    await context.audioWorklet.addModule(workletUrl);
    const node = new AudioWorkletNode(context, "cipher-capture");
    node.port.onmessage = (event) => onFrame(event.data as Float32Array);
    source.connect(node);
    node.connect(sink);
    return {
      context,
      stream,
      disconnect: () => {
        node.port.onmessage = null;
        node.disconnect();
        source.disconnect();
        sink.disconnect();
        if (workletUrl) URL.revokeObjectURL(workletUrl);
      },
    };
  } catch {
    if (workletUrl) URL.revokeObjectURL(workletUrl);
    const node = context.createScriptProcessor(FALLBACK_BUFFER_SIZE, 1, 1);
    node.onaudioprocess = (event) => {
      // Copied for the same reason the worklet copies: this buffer is the
      // node's, and it is reused.
      onFrame(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(node);
    node.connect(sink);
    return {
      context,
      stream,
      disconnect: () => {
        node.onaudioprocess = null;
        node.disconnect();
        source.disconnect();
        sink.disconnect();
      },
    };
  }
}

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
  const [level, setLevel] = useState(0);
  // Surfaced for the diagnostics readout. Held in state rather than read off
  // the detector during render, because reading a ref while rendering is
  // exactly what react-hooks/refs forbids.
  const [noiseFloor, setNoiseFloor] = useState<number | null>(null);
  const [devices, setDevices] = useState<{ id: string; label: string }[]>([]);
  const [deviceId, setDeviceIdState] = useState<string | null>(null);

  const captureRef = useRef<Capture | null>(null);
  const detectorRef = useRef<UtteranceDetector | null>(null);
  const meterRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const suspendedRef = useRef(false);
  const wantListeningRef = useRef(false);
  const failuresRef = useRef(0);
  //: Set when the server says 429. Until it passes, utterances are dropped
  //: locally rather than sent -- retrying into a rate limit is what turns a
  //: brief overage into a lockout.
  const cooldownUntilRef = useRef(0);
  const deviceIdRef = useRef<string | null>(null);

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
    typeof window.AudioContext !== "undefined";

  const send = useCallback(async (samples: Float32Array, sampleRate: number) => {
    if (Date.now() < cooldownUntilRef.current) return;
    try {
      const text = await transcribe(encodeWav(samples, sampleRate));
      failuresRef.current = 0;
      setError(null);
      const trimmed = text.trim();
      if (!trimmed) return;
      // Interrupt words are controls, not messages -- same rule as the
      // browser recogniser, so behaviour does not change with the backend.
      if (isInterrupt(trimmed)) {
        onInterruptRef.current();
        return;
      }
      onUtteranceRef.current(trimmed);
    } catch (err) {
      failuresRef.current += 1;

      if (err instanceof ApiError && err.status === 429) {
        // Should now be unreachable -- silence is no longer uploaded, and a
        // person cannot speak thirty separate utterances in a minute -- but
        // if it happens the honest thing is to say the limit was hit rather
        // than blame the audio, which is what the old message did.
        cooldownUntilRef.current = Date.now() + 20000;
        setError("Transcription rate limit reached. Pausing for a moment, then listening again.");
        return;
      }

      // One failure is a blip; a conversation must not stop for it, and the
      // next utterance is very likely to work.
      if (failuresRef.current < MAX_CONSECUTIVE_FAILURES) {
        setError("That last bit could not be transcribed. Still listening.");
        return;
      }

      setError(
        err instanceof ApiError && err.status === 0
          ? "The backend is unreachable, so nothing can be transcribed. Check that it is running."
          : "Transcription keeps failing. Check the backend logs and GROQ_API_KEY."
      );
    }
  }, []);

  const teardown = useCallback(() => {
    if (meterRef.current !== null) {
      clearInterval(meterRef.current);
      meterRef.current = null;
    }
    captureRef.current?.disconnect();
    captureRef.current?.stream.getTracks().forEach((track) => track.stop());
    void captureRef.current?.context.close().catch(() => {});
    captureRef.current = null;
    detectorRef.current = null;
    setListening(false);
    setInterim("");
    setLevel(0);
    setNoiseFloor(null);
  }, []);

  const start = useCallback(async () => {
    if (!supported) {
      setError("This browser cannot capture audio.");
      return;
    }
    if (captureRef.current) return;

    setError(null);
    failuresRef.current = 0;
    cooldownUntilRef.current = 0;
    wantListeningRef.current = true;
    suspendedRef.current = false;

    try {
      const capture = await openCapture(deviceIdRef.current, (frame) => {
        // Muted while the assistant is talking. Dropped at the very front so
        // the detector never sees the assistant's own voice and cannot
        // mistake it for a turn.
        if (suspendedRef.current) return;
        detectorRef.current?.push(frame);
      });
      captureRef.current = capture;

      detectorRef.current = createUtteranceDetector({
        sampleRate: capture.context.sampleRate,
        finishMs: FINISH_MS,
        onUtterance: (samples, sampleRate) => void send(samples, sampleRate),
      });

      setEnabled(true);
      setListening(true);

      // The meter is polled rather than pushed: frames arrive every few
      // milliseconds and setting React state on each one would re-render the
      // page a hundred times a second to move a bar a pixel.
      meterRef.current = setInterval(() => {
        const detector = detectorRef.current;
        if (!detector) return;
        setLevel(suspendedRef.current ? 0 : detector.level());
        setInterim(detector.speaking() ? "…" : "");
        setNoiseFloor(detector.ready() ? detector.noiseFloor() : null);
      }, 100);

      // Labels are empty until permission is granted, so this is enumerated
      // after getUserMedia rather than before it. Without that the picker
      // reads "Microphone 1 / Microphone 2" and helps nobody choose.
      void navigator.mediaDevices
        .enumerateDevices()
        .then((all) => {
          setDevices(
            all
              .filter((device) => device.kind === "audioinput")
              .map((device, index) => ({
                id: device.deviceId,
                label: device.label || `Microphone ${index + 1}`,
              }))
          );
          const active = capture.stream.getAudioTracks()[0]?.getSettings().deviceId;
          if (active) setDeviceIdState((current) => current ?? active);
        })
        .catch(() => {
          // Non-fatal: the picker just stays empty and the default mic is used.
        });
    } catch (err) {
      wantListeningRef.current = false;
      setEnabled(false);
      setError(
        err instanceof DOMException && err.name === "NotFoundError"
          ? "No microphone was found. Check that one is connected and selected as the input device."
          : "Microphone access was blocked. Allow it in the browser's site settings, then try again."
      );
    }
  }, [supported, send]);

  const stop = useCallback(() => {
    wantListeningRef.current = false;
    suspendedRef.current = false;
    setEnabled(false);
    // A deliberate stop sends what was captured rather than dropping a
    // half-finished sentence on the floor.
    detectorRef.current?.flush();
    teardown();
  }, [teardown]);

  const suspend = useCallback(() => {
    if (!wantListeningRef.current) return;
    suspendedRef.current = true;
    // Discard rather than emit: what the mic caught while the assistant was
    // talking is the assistant. The capture graph stays open -- tearing the
    // microphone down and back up between every reply is what made the old
    // path lose the first word of the answer to a follow-up question.
    detectorRef.current?.reset();
    setListening(false);
    setInterim("");
    setLevel(0);
  }, []);

  const resume = useCallback(() => {
    if (!wantListeningRef.current || !suspendedRef.current) return;
    suspendedRef.current = false;
    detectorRef.current?.reset();
    setListening(true);
  }, []);

  const setDeviceId = useCallback(
    (id: string | null) => {
      deviceIdRef.current = id;
      setDeviceIdState(id);
      // Switching input means a new stream, a new context and a new noise
      // floor. Only restart if the mic was actually on; otherwise the choice
      // simply applies the next time it is switched on.
      if (!wantListeningRef.current) return;
      teardown();
      void start();
    },
    [teardown, start]
  );

  useEffect(() => () => teardown(), [teardown]);

  return {
    supported,
    enabled,
    listening,
    interim,
    error,
    level,
    devices,
    deviceId,
    setDeviceId,
    noiseFloor,
    start: () => void start(),
    stop,
    suspend,
    resume,
  };
}
