// Small display formatters shared across the redesigned screens.
//
// All of them take an ISO string or a number and return a string. None of
// them read the clock during render on their own behalf -- callers do, and
// the values they format are already-fetched server timestamps, so a
// re-render showing "3m ago" instead of "2m ago" is the intended behaviour
// rather than a hydration hazard worth engineering around.

/** "2m ago", "1h ago", "Yesterday", "May 14" -- the sidebar's time column. */
export function relativeTime(iso: string): string {
  const then = new Date(iso);
  const seconds = Math.floor((Date.now() - then.getTime()) / 1000);

  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 172800) return "Yesterday";
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** 24-hour clock, seconds included -- message and log rows are dense and
    monospaced, and a 12-hour "2:02:18 PM" is wider and harder to scan. */
export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Durations stay in milliseconds below a second, because the agents table
    is where sub-second differences actually matter. */
export function duration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
