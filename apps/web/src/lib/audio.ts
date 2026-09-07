// Voice activity detection and WAV encoding for the server-transcription path.
//
// This module exists because the first version of that path had no idea
// whether anyone was talking. It recorded in fixed segments and closed one
// whenever 400ms of audio had accumulated and 900ms of it was quiet -- a
// condition an idle microphone satisfies every 1.3 seconds, forever. The
// consequences all showed up at once, and all four looked like separate
// bugs:
//
//   * a clip of pure silence uploaded roughly every 1.3s,
//   * the per-user rate limit (20/min) exhausted about 26 seconds after
//     switching the mic on, so every real utterance after that failed with
//     "that last bit could not be transcribed",
//   * Whisper's well-documented behaviour on silence -- it emits plausible
//     filler like "Thank you." or "you" rather than nothing -- arriving as
//     genuine chat messages,
//   * the first syllable after every pause lost in the gap between one
//     MediaRecorder stopping and the next one starting.
//
// So the rule this module enforces is: audio is only ever sent when speech
// was actually heard. Everything else follows from that.
//
// Deliberately free of React, `window`, and the Web Audio API. It takes
// frames of samples and hands back utterances; a test or a REPL can drive it
// with a synthesised array. That is the same split `createTranscriptBuffer`
// in @/lib/speech uses, and for the same reason -- the part with the logic
// in it should not need a browser.

// --- WAV ---------------------------------------------------------------

/**
 * Encode mono float samples as a 16-bit PCM WAV blob.
 *
 * Chosen over MediaRecorder's compressed output on purpose, despite being
 * perhaps twenty times larger on the wire. MediaRecorder hands back a
 * container -- webm/opus in Chrome, ogg/opus in Firefox, mp4 in Safari --
 * whose chunks after the first are not independently decodable, which is
 * what forces the "stop the recorder to get a usable clip" design and with
 * it the gap at every boundary. Raw samples have no such structure: they can
 * be sliced anywhere, buffered before speech starts, and joined without
 * touching a header.
 *
 * The size is affordable here. At 16 kHz mono a ten-second utterance is
 * about 320 KB against an 8 MB server cap, and 16 kHz is Whisper's own
 * working rate, so nothing is thrown away by sending it.
 */
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM header length
  view.setUint16(20, 1, true); // format: PCM
  view.setUint16(22, 1, true); // channels: mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (const sample of samples) {
    // Clamp before scaling: a sample above 1.0 (possible after gain) would
    // otherwise wrap around and arrive as a loud click.
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }

  return new Blob([buffer], { type: "audio/wav" });
}

/** Root-mean-square amplitude of a frame: how loud it is, in one number. */
export function rms(frame: Float32Array): number {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (const sample of frame) sum += sample * sample;
  return Math.sqrt(sum / frame.length);
}

// --- Tuning ------------------------------------------------------------

/**
 * Audio kept from *before* speech was detected, in milliseconds.
 *
 * Detection is necessarily late: a threshold can only be crossed after the
 * sound that crosses it. Without a pre-roll every utterance loses its first
 * consonant, which is why the old path turned "send that to Priya" into
 * "end that to Priya". 300ms is comfortably longer than the detection lag
 * and short enough to add nothing audible at the front.
 */
export const PRE_ROLL_MS = 300;

/**
 * Speech shorter than this is not speech.
 *
 * A door, a keyboard, a chair -- all cross an amplitude threshold and none
 * of them are worth a request. This is the difference between a VAD and a
 * loudness trigger.
 */
export const MIN_SPEECH_MS = 250;

/** How long the noise floor is measured for before the mic goes live. */
export const CALIBRATION_MS = 600;

/**
 * Speech starts at this multiple of the measured noise floor, and does not
 * end until the level falls back below `RELEASE_RATIO` times it.
 *
 * Two numbers rather than one because a single threshold chatters: a voice
 * sits right at the boundary between words, so an utterance would be chopped
 * into one clip per syllable. The gap between them is the hysteresis.
 *
 * Ratios rather than the old fixed `SILENCE_RMS = 0.018`, which was the
 * other half of the original problem. That constant was measured on one
 * microphone in one room. Automatic gain control -- which every browser
 * applies by default -- raises the noise floor on a cheap laptop mic until
 * an empty room reads *above* 0.018, at which point the clip never closes
 * and the recorder runs to its 25-second ceiling. Measuring the floor at
 * the start of each session costs 600ms and removes the assumption.
 */
export const SPEECH_RATIO = 3.0;
export const RELEASE_RATIO = 1.6;

/**
 * The floor is never trusted below this.
 *
 * A muted or disconnected microphone delivers samples of exactly zero. Three
 * times zero is still zero, so a purely relative threshold would classify
 * the faintest numerical noise as speech and upload silence again by a
 * different route. This is the guard against that, and it is why a dead mic
 * now shows a flat level meter instead of a stream of phantom messages.
 */
export const ABSOLUTE_FLOOR = 0.004;

/** Hard ceiling on one utterance, so a noisy room cannot record forever. */
export const MAX_UTTERANCE_MS = 25000;

/**
 * Trailing silence kept on a finished utterance.
 *
 * The whole finish window is silence by definition -- that is what ended the
 * utterance -- so shipping all of it means roughly a second of nothing at the
 * end of every clip. Two reasons to cut it: it is dead weight on the upload,
 * and trailing silence is the exact condition under which Whisper invents
 * filler, which is the failure this module exists to stop. A little is kept
 * because a hard cut at the last loud sample clips the release of the final
 * consonant.
 */
export const TRAILING_SILENCE_MS = 200;

//: How fast the idle noise floor follows the room. Slow on purpose: it must
//: track a fan switching on over seconds, and must not be dragged upward by
//: the speech it is supposed to be distinguishing from.
const FLOOR_ADAPT = 0.02;

//: Smoothing for the displayed level only. Nothing decides anything from
//: this; it exists so the meter reads as a voice rather than a strobe.
const LEVEL_SMOOTHING = 0.3;

export interface UtteranceDetector {
  /** Feed one frame of mono samples, in capture order. */
  push(frame: Float32Array): void;
  /** Emit whatever speech is buffered now (the mic is being switched off). */
  flush(): void;
  /** Drop everything buffered without emitting it. */
  reset(): void;
  /** Smoothed 0..1 input level, for a meter. */
  level(): number;
  /** Whether speech is being captured right now. */
  speaking(): boolean;
  /** False during the opening calibration window. */
  ready(): boolean;
  /** The measured noise floor, for the diagnostics readout. */
  noiseFloor(): number;
}

export function createUtteranceDetector({
  sampleRate,
  finishMs,
  preRollMs = PRE_ROLL_MS,
  minSpeechMs = MIN_SPEECH_MS,
  maxUtteranceMs = MAX_UTTERANCE_MS,
  calibrationMs = CALIBRATION_MS,
  onUtterance,
}: {
  sampleRate: number;
  /** Silence that ends an utterance. Shares `FINISH_MS` with the typed path. */
  finishMs: number;
  preRollMs?: number;
  minSpeechMs?: number;
  maxUtteranceMs?: number;
  calibrationMs?: number;
  onUtterance: (samples: Float32Array, sampleRate: number) => void;
}): UtteranceDetector {
  const preRollSamples = Math.floor((preRollMs / 1000) * sampleRate);
  const minSpeechSamples = Math.floor((minSpeechMs / 1000) * sampleRate);
  const maxUtteranceSamples = Math.floor((maxUtteranceMs / 1000) * sampleRate);

  // Rolling window of the most recent audio, kept while idle so that the
  // moment speech is detected the run-up to it is already in hand.
  let preRoll: Float32Array[] = [];
  let preRollLength = 0;

  // The utterance under construction, pre-roll included.
  let captured: Float32Array[] = [];
  let capturedLength = 0;

  let inSpeech = false;
  let silenceSamples = 0;
  let speechSamples = 0;

  let floor = 0;
  let calibrationSamples = 0;
  const calibrationTarget = Math.floor((calibrationMs / 1000) * sampleRate);
  const calibrationReadings: number[] = [];
  let smoothedLevel = 0;

  function concat(frames: Float32Array[], length: number): Float32Array {
    const out = new Float32Array(length);
    let offset = 0;
    for (const frame of frames) {
      out.set(frame, offset);
      offset += frame.length;
    }
    return out;
  }

  function emit() {
    const wasSpeech = speechSamples;
    const trailing = silenceSamples;
    const frames = captured;
    const length = capturedLength;

    captured = [];
    capturedLength = 0;
    inSpeech = false;
    silenceSamples = 0;
    speechSamples = 0;

    // The blip guard. Reached by anything loud and brief, and rejected here
    // rather than at the threshold, because a real word can dip below the
    // release level in the middle and still be a word.
    if (wasSpeech < minSpeechSamples || length === 0) return;

    const keep = Math.floor((TRAILING_SILENCE_MS / 1000) * sampleRate);
    const trimmed = Math.max(0, length - Math.max(0, trailing - keep));
    onUtterance(concat(frames, length).subarray(0, trimmed), sampleRate);
  }

  return {
    push(frame: Float32Array) {
      if (frame.length === 0) return;
      const level = rms(frame);
      smoothedLevel = smoothedLevel + (level - smoothedLevel) * LEVEL_SMOOTHING;

      // Opening calibration. The mic is live but nothing is captured yet:
      // whatever the room sounds like right now is the baseline.
      if (calibrationSamples < calibrationTarget) {
        calibrationSamples += frame.length;
        calibrationReadings.push(level);
        if (calibrationSamples >= calibrationTarget) {
          // Median, not mean. A single cough during calibration would drag a
          // mean up far enough to deafen the detector for the whole session.
          const sorted = [...calibrationReadings].sort((a, b) => a - b);
          floor = Math.max(sorted[Math.floor(sorted.length / 2)] ?? 0, ABSOLUTE_FLOOR);
        }
        return;
      }

      const speechThreshold = floor * SPEECH_RATIO;
      const releaseThreshold = floor * RELEASE_RATIO;
      const loud = inSpeech ? level > releaseThreshold : level > speechThreshold;

      if (!inSpeech) {
        // Follow the room while it is quiet, so a fan or an air conditioner
        // starting mid-session raises the floor instead of pinning the
        // detector open.
        if (!loud) {
          floor = Math.max(floor + (level - floor) * FLOOR_ADAPT, ABSOLUTE_FLOOR);
        }

        preRoll.push(frame);
        preRollLength += frame.length;
        while (preRollLength > preRollSamples && preRoll.length > 1) {
          preRollLength -= preRoll[0].length;
          preRoll.shift();
        }

        if (!loud) return;

        // Speech begins. The pre-roll becomes the head of the utterance,
        // which is the whole reason it was kept.
        inSpeech = true;
        captured = preRoll;
        capturedLength = preRollLength;
        preRoll = [];
        preRollLength = 0;
        silenceSamples = 0;
        speechSamples = 0;
      }

      captured.push(frame);
      capturedLength += frame.length;
      if (loud) {
        speechSamples += frame.length;
        silenceSamples = 0;
      } else {
        silenceSamples += frame.length;
      }

      const finishSamples = Math.floor((finishMs / 1000) * sampleRate);
      // The same rule the typed path uses: a pause only ends the thought
      // once it has outlasted the whole finish window. People pause
      // mid-sentence, and cutting on the first quiet moment truncates
      // roughly every other utterance.
      if (silenceSamples >= finishSamples || capturedLength >= maxUtteranceSamples) emit();
    },

    flush() {
      if (inSpeech) emit();
    },

    reset() {
      captured = [];
      capturedLength = 0;
      preRoll = [];
      preRollLength = 0;
      inSpeech = false;
      silenceSamples = 0;
      speechSamples = 0;
    },

    level() {
      // Scaled for a meter rather than reported raw: conversational speech
      // lands around 0.05-0.15 RMS, which as a bar is indistinguishable from
      // nothing at all.
      return Math.min(1, smoothedLevel * 8);
    },

    speaking() {
      return inSpeech;
    },

    ready() {
      return calibrationSamples >= calibrationTarget;
    },

    noiseFloor() {
      return floor;
    },
  };
}
