// Does the voice detector actually detect voice?
//
// The server-transcription path is the microphone for every browser that is
// not Google Chrome, and its first version had no idea whether anyone was
// talking -- it closed a clip whenever 400ms of audio had accumulated and
// 900ms of it was quiet, which an idle mic satisfies every 1.3 seconds
// forever. That produced four symptoms that looked like four bugs: silence
// uploaded on a loop, the rate limit gone in half a minute, Whisper's filler
// ("Thank you.", "you") arriving as real chat messages, and the first
// syllable after every pause lost.
//
// None of that is reachable from a unit test of the UI, and all of it is
// reachable from here: @/lib/audio takes frames of samples and emits
// utterances, so synthesised audio drives it exactly as a microphone would.
// Check 1 is the regression test for the original bug and the reason this
// file exists.
//
//     node scripts/vad-check.mjs
//
// Runs on Node's own TypeScript support (24.x), so there is no build step
// and no test framework to install for one file.

import { createUtteranceDetector, encodeWav } from "../src/lib/audio.ts";

const RATE = 16000;
const FRAME = 512; // 32ms, close to a real worklet frame

function feed(detector, seconds, amplitude) {
  const frames = Math.floor((seconds * RATE) / FRAME);
  for (let f = 0; f < frames; f += 1) {
    const frame = new Float32Array(FRAME);
    for (let i = 0; i < FRAME; i += 1) {
      const t = (f * FRAME + i) / RATE;
      // A voice-ish tone plus a little hiss, so the floor is never exactly 0.
      frame[i] = amplitude * Math.sin(2 * Math.PI * 180 * t) + (Math.random() - 0.5) * 0.006;
    }
    detector.push(frame);
  }
}

function makeDetector() {
  const utterances = [];
  const detector = createUtteranceDetector({
    sampleRate: RATE,
    finishMs: 900,
    onUtterance: (samples) => utterances.push(samples.length / RATE),
  });
  // Calibration window: quiet room.
  feed(detector, 1.0, 0);
  return { detector, utterances };
}

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
  if (!ok) console.log(`        expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

// 1. The bug that started this: an idle mic must never produce an utterance.
{
  const { detector, utterances } = makeDetector();
  feed(detector, 30, 0); // half a minute of silence
  check("30s of silence uploads nothing", utterances.length, 0);
}

// 2. One phrase, surrounded by silence, comes out as exactly one utterance.
{
  const { detector, utterances } = makeDetector();
  feed(detector, 0.5, 0);
  feed(detector, 1.5, 0.12); // speech
  feed(detector, 2.0, 0);
  check("one phrase -> one utterance", utterances.length, 1);
  // Pre-roll means the clip is LONGER than the speech: the run-up is included.
  const longEnough = utterances[0] > 1.6 && utterances[0] < 2.2;
  check(`clip includes pre-roll (${utterances[0]?.toFixed(2)}s for 1.5s of speech)`, longEnough, true);
}

// 3. A short blip is not speech.
{
  const { detector, utterances } = makeDetector();
  feed(detector, 0.5, 0);
  feed(detector, 0.1, 0.2); // a door, a keyboard
  feed(detector, 2.0, 0);
  check("100ms blip is rejected", utterances.length, 0);
}

// 4. A mid-sentence pause must not split the utterance.
{
  const { detector, utterances } = makeDetector();
  feed(detector, 0.9, 0.12);
  feed(detector, 0.4, 0); // pause shorter than FINISH_MS
  feed(detector, 0.9, 0.12);
  feed(detector, 2.0, 0);
  check("400ms mid-sentence pause does not split", utterances.length, 1);
}

// 5. A real gap between two thoughts does split them.
{
  const { detector, utterances } = makeDetector();
  feed(detector, 0.9, 0.12);
  feed(detector, 1.5, 0); // longer than FINISH_MS
  feed(detector, 0.9, 0.12);
  feed(detector, 2.0, 0);
  check("1.5s gap yields two utterances", utterances.length, 2);
}

// 6. A dead microphone (samples of exactly zero) stays silent rather than
//    treating numerical noise as speech.
{
  const utterances = [];
  const detector = createUtteranceDetector({
    sampleRate: RATE,
    finishMs: 900,
    onUtterance: () => utterances.push(1),
  });
  for (let f = 0; f < 1000; f += 1) detector.push(new Float32Array(FRAME));
  check("dead mic (all zeros) uploads nothing", utterances.length, 0);
}

// 7. A loud room: the floor is measured high, so ordinary room noise at that
//    level still does not trigger. This is what the old fixed 0.018 got wrong.
{
  const utterances = [];
  const detector = createUtteranceDetector({
    sampleRate: RATE,
    finishMs: 900,
    onUtterance: () => utterances.push(1),
  });
  feed(detector, 1.0, 0.03); // calibrate against a noisy room (well above 0.018 RMS)
  feed(detector, 10, 0.03); // ...which then just continues
  check("noisy room calibrates and stays quiet", utterances.length, 0);
  // ...but a voice above that room still gets through.
  feed(detector, 1.2, 0.15);
  feed(detector, 1.5, 0.03);
  check("voice above a noisy room is still captured", utterances.length, 1);
}

// 8. WAV header sanity.
{
  const samples = new Float32Array(16000); // 1 second
  const blob = encodeWav(samples, RATE);
  check("wav blob size = 44 + 2 bytes/sample", blob.size, 44 + 32000);
  check("wav mime type", blob.type, "audio/wav");
}

console.log(failures === 0 ? "\nAll checks passed." : `\n${failures} check(s) FAILED.`);
process.exit(failures === 0 ? 0 : 1);
