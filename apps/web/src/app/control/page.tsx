"use client";

// Phase 7: the permission panel, the kill switch, and the audit log.
//
// This page exists to make the safety model visible rather than implied.
// Everything on it answers a question the user would otherwise have to trust
// an answer to: what can it do right now, what did I approve, and what has
// it actually attempted.
//
// Three deliberate choices:
//   - STOP is the first thing on the page, always reachable, and never asks
//     "are you sure?". A stop control that needs confirming is not one.
//   - A sensitive action shows its confirmation requirement permanently, not
//     only at the moment of use, so the escalation rule is legible before
//     anyone relies on it.
//   - The log shows denials and blocks alongside successes, because those
//     are the entries worth reviewing.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  type ActionInfo,
  type ActivityLogEntry,
  ApiError,
  type AutomationStatus,
  approveAction,
  engageKillSwitch,
  executeAction,
  getAutomationStatus,
  listActivityLog,
  releaseKillSwitch,
  revokeAction,
} from "@/lib/api";

const RISK_STYLES: Record<string, string> = {
  read_only: "border-zinc-300 text-zinc-500 dark:border-zinc-700 dark:text-zinc-400",
  session: "border-sky-400 text-sky-700 dark:border-sky-700 dark:text-sky-400",
  sensitive: "border-amber-500 text-amber-700 dark:border-amber-600 dark:text-amber-400",
};

const OUTCOME_STYLES: Record<string, string> = {
  executed: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  approved: "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-300",
  denied: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  blocked: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
  failed: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
};

const RISK_LABEL: Record<string, string> = {
  read_only: "reads only",
  session: "needs session approval",
  sensitive: "confirm every time",
};

export default function ControlPage() {
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [log, setLog] = useState<ActivityLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [args, setArgs] = useState<Record<string, string>>({});

  const refreshLog = useCallback(async () => {
    try {
      setLog(await listActivityLog());
    } catch {
      // Non-fatal: the log just stays stale.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAutomationStatus(), listActivityLog()])
      .then(([s, l]) => {
        if (cancelled) return;
        setStatus(s);
        setLog(l);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load automation status.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function run(label: string, work: () => Promise<AutomationStatus>) {
    setError(null);
    setNotice(null);
    setBusy(label);
    try {
      const next = await work();
      setStatus(next);
      if (next.notice) setNotice(next.notice);
      await refreshLog();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  async function handleExecute(action: ActionInfo) {
    setError(null);
    setNotice(null);
    setBusy(action.name);

    // The confirmation is asked for here, in the browser, AND enforced on the
    // server. The client prompt is a courtesy; the server check is the rule.
    let confirmed = false;
    if (action.needs_confirmation) {
      confirmed = window.confirm(
        `${action.name} is a sensitive action.\n\nIt runs immediately on this machine. ` +
          `Continue?`
      );
      if (!confirmed) {
        setBusy(null);
        setNotice("Cancelled.");
        return;
      }
    }

    const payload: Record<string, unknown> = {};
    const value = args[action.name]?.trim();
    if (value) {
      if (action.name === "open_url") payload.url = value;
      else if (action.name === "list_directory") payload.path = value;
      else if (action.name === "open_app") payload.app = value;
    }

    try {
      const result = await executeAction(action.name, payload, { confirmed });
      setNotice(`${result.action_name}: ${result.summary}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That action did not run.");
    } finally {
      setBusy(null);
      await refreshLog();
      try {
        setStatus(await getAutomationStatus());
      } catch {
        // keep the previous status
      }
    }
  }

  const needsArgument = (name: string) =>
    name === "open_url" || name === "list_directory" || name === "open_app";

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col bg-white px-4 py-6 dark:bg-black">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-sm font-semibold tracking-wide text-zinc-900 dark:text-zinc-100">
          What CIPHER may do to this computer
        </h1>
        <div className="flex items-center gap-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          <Link href="/agents" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Agents
          </Link>
          <Link href="/" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            ← Chat
          </Link>
        </div>
      </header>

      {/* First on the page and always reachable. */}
      <section className="mb-5 flex items-center gap-3 rounded-xl border border-zinc-200 px-3 py-3 dark:border-zinc-800">
        {status?.kill_switch_engaged ? (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => run("resume", releaseKillSwitch)}
            className="rounded-lg border border-emerald-500 px-4 py-2 text-sm font-bold uppercase tracking-wide text-emerald-700 disabled:opacity-40 dark:text-emerald-400"
          >
            Resume
          </button>
        ) : (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => run("stop", engageKillSwitch)}
            className="rounded-lg bg-red-600 px-5 py-2 text-sm font-bold uppercase tracking-wide text-white disabled:opacity-40"
          >
            Stop
          </button>
        )}
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          {status?.kill_switch_engaged
            ? "Halted. Nothing will run, including read-only actions, until this is released."
            : "Halts every action immediately. No confirmation, no exceptions."}
        </p>
      </section>

      {status && !status.enabled && (
        <div className="mb-4 rounded-lg border border-zinc-300 bg-zinc-50 px-4 py-2 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          Automation is switched off at the server. Nothing below can run. Set{" "}
          <code className="font-mono">AUTOMATION_ENABLED=true</code> in <code className="font-mono">.env</code> to
          change that — deliberately off by default, so a fresh checkout cannot act on your machine.
        </div>
      )}
      {notice && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          {notice}
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      <section className="mb-6">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Actions
        </h2>
        {loading ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {status?.actions.map((action) => (
              <li key={action.name} className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{action.name}</span>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${
                          RISK_STYLES[action.risk]
                        }`}
                      >
                        {RISK_LABEL[action.risk]}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                      {action.description}
                    </p>
                    {!action.allowed_now && action.reason && (
                      <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">{action.reason}</p>
                    )}
                  </div>

                  <div className="flex shrink-0 flex-col items-end gap-1">
                    {action.risk === "session" && (
                      <div className="flex gap-1">
                        <button
                          type="button"
                          disabled={busy !== null || !status?.enabled}
                          onClick={() => run(action.category, () => approveAction(action.category, "session"))}
                          className="rounded border border-zinc-300 px-2 py-0.5 text-[10px] font-semibold uppercase text-zinc-600 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300"
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          disabled={busy !== null || !status?.enabled}
                          onClick={() => run(action.category, () => revokeAction(action.category))}
                          className="rounded border border-zinc-300 px-2 py-0.5 text-[10px] font-semibold uppercase text-zinc-600 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300"
                        >
                          Revoke
                        </button>
                      </div>
                    )}
                    <button
                      type="button"
                      disabled={busy !== null || !status?.enabled}
                      onClick={() => handleExecute(action)}
                      className="rounded bg-zinc-900 px-3 py-1 text-[10px] font-semibold uppercase text-zinc-50 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
                    >
                      Run
                    </button>
                  </div>
                </div>

                {needsArgument(action.name) && (
                  <input
                    type="text"
                    value={args[action.name] ?? ""}
                    onChange={(e) => setArgs((prev) => ({ ...prev, [action.name]: e.target.value }))}
                    placeholder={
                      action.name === "open_url"
                        ? "https://example.com"
                        : action.name === "list_directory"
                          ? "an allowed absolute path"
                          : "an allowed application name"
                    }
                    className="mt-2 w-full rounded-lg border border-zinc-300 bg-transparent px-2 py-1 text-xs outline-none focus:border-zinc-500 dark:border-zinc-700"
                  />
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex-1">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Everything attempted
        </h2>
        {log.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Nothing has been attempted yet.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {log.map((entry) => (
              <li
                key={entry.id}
                className="flex items-start justify-between gap-3 rounded-lg border border-zinc-200 px-3 py-1.5 dark:border-zinc-800"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${
                        OUTCOME_STYLES[entry.outcome] ?? OUTCOME_STYLES.blocked
                      }`}
                    >
                      {entry.outcome}
                    </span>
                    <span className="truncate text-xs text-zinc-900 dark:text-zinc-100">{entry.action_name}</span>
                    {entry.persona && (
                      <span className="text-[10px] uppercase text-zinc-400">{entry.persona}</span>
                    )}
                  </div>
                  {(entry.reason || entry.result) && (
                    <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                      {entry.reason ?? entry.result}
                    </p>
                  )}
                </div>
                <span className="shrink-0 text-[10px] tabular-nums text-zinc-400">
                  {new Date(entry.created_at).toLocaleTimeString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
