"use client";

// Phase 7: letting CIPHER see the screen, without the backend being able to.
//
// The browser owns the capture. `getDisplayMedia` makes the user choose
// exactly what to share and shows them a recording indicator for as long as
// it lasts; a server-side screenshot would do neither. So nothing here sends
// anything until a question is asked, and what it sends is a single frame
// grabbed at that moment.
//
// The rule that makes this trustworthy is the one that is easy to get wrong:
// **the frame is captured when the question is asked, never cached from when
// the share started.** A cached frame answers about a screen that has since
// changed, confidently and wrongly. And if the share has ended, `capture()`
// returns null so the caller must say the share ended rather than answering
// from a stale image.

import { useCallback, useEffect, useRef, useState } from "react";

export interface ScreenShare {
  supported: boolean;
  sharing: boolean;
  error: string | null;
  start: () => Promise<boolean>;
  stop: () => void;
  /** A JPEG of the screen right now, or null if the share has ended. */
  capture: () => Promise<Blob | null>;
}

export function useScreenShare(): ScreenShare {
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const supported =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getDisplayMedia === "function";

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    videoRef.current = null;
    setSharing(false);
  }, []);

  const start = useCallback(async () => {
    setError(null);
    if (!supported) {
      setError("This browser cannot share the screen.");
      return false;
    }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      streamRef.current = stream;

      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      videoRef.current = video;

      // The user can stop the share from the browser's own indicator, which
      // never tells this component. Without this listener the UI would go on
      // claiming to be sharing a stream that has already ended.
      stream.getVideoTracks()[0]?.addEventListener("ended", () => stop());

      setSharing(true);
      return true;
    } catch (err) {
      // Cancelling the picker is a NotAllowedError and is not a failure.
      const name = err instanceof DOMException ? err.name : "";
      if (name !== "NotAllowedError" && name !== "AbortError") {
        setError("The screen share could not be started.");
      }
      return false;
    }
  }, [supported, stop]);

  const capture = useCallback(async (): Promise<Blob | null> => {
    const video = videoRef.current;
    const stream = streamRef.current;
    // Checked at capture time, not at question time: a share that ended a
    // second ago must produce null rather than the last good frame.
    if (!video || !stream || stream.getVideoTracks()[0]?.readyState !== "live") {
      stop();
      return null;
    }

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    if (canvas.width === 0 || canvas.height === 0) return null;

    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    // JPEG, and the backend sniffs the bytes rather than trusting a declared
    // type -- mismatching those two is the classic way this feature appears
    // completely broken when one string is wrong.
    return new Promise((resolve) => {
      canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.85);
    });
  }, [stop]);

  useEffect(() => () => stop(), [stop]);

  return { supported, sharing, error, start, stop, capture };
}
