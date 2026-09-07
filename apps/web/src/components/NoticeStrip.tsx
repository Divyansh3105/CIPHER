// The thin degraded-state strips that sit directly under a screen's header.
//
// Three tones, and the difference is not cosmetic:
//   zinc  -- something changed because you asked it to (persona switched,
//            model pinned). Nothing is wrong.
//   amber -- the answer arrived, but not the way you asked for it (fallback
//            model, transcription moved to the server, a specialist failed
//            and the reply is ungrounded).
//   red   -- the answer itself was changed or could not be produced (the
//            persona's safety filter replaced it, or the request errored).
//
// Keeping the informational case OUT of amber is the point of having three:
// if "switched to FRIDAY" and "answered on the fallback model" look the
// same, the amber strip stops meaning anything.
//
// None of them is dismissible by accident: there is no close button, because
// what they report stays true until the next message.

export type NoticeTone = "zinc" | "amber" | "red";

const TONE_CLASS: Record<NoticeTone, string> = {
  zinc: "bg-zinc-900/80 border-zinc-800 text-zinc-400",
  amber: "bg-amber-500/10 border-amber-500/30 text-amber-400",
  red: "bg-red-500/10 border-red-500/30 text-red-400",
};

const DOT_CLASS: Record<NoticeTone, string> = {
  zinc: "bg-zinc-500",
  amber: "bg-amber-400",
  red: "bg-red-400",
};

export default function NoticeStrip({
  tone,
  children,
}: {
  tone: NoticeTone;
  children: React.ReactNode;
}) {
  return (
    <div
      role="status"
      className={`flex w-full shrink-0 items-center gap-2 border-b px-4 py-1.5 font-mono text-[12px] ${TONE_CLASS[tone]}`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_CLASS[tone]}`} aria-hidden />
      <span className="min-w-0">{children}</span>
    </div>
  );
}
