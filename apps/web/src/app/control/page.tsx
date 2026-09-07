"use client";

// Phase 7: the permission panel, the kill switch, and the audit log.
//
// This page exists to make the safety model visible rather than implied.
// Everything on it answers a question the user would otherwise have to trust
// an answer to: what can it do right now, what did I approve, and what has
// it actually attempted.
//
// Four deliberate choices, none of them cosmetic:
//   - The default state is meant to LOOK inert. A fresh checkout cannot act
//     on anyone's machine, and the banner for that state is grey and calm.
//     Amber is reserved for the state where something can actually happen.
//   - STOP is reachable without scrolling and never asks "are you sure?".
//     A stop control that needs confirming is not one.
//   - A sensitive action shows its confirmation requirement permanently, not
//     only at the moment of use, so the escalation rule is legible before
//     anyone relies on it. No grant ever covers a sensitive action.
//   - The log shows denials and blocks alongside successes, and styles them
//     louder, because those are the entries worth reviewing.

import { useCallback, useEffect, useState } from "react";
import Icon from "@/components/Icon";
import NoticeStrip, { type NoticeTone } from "@/components/NoticeStrip";
import {
  type ActionInfo,
  type ActivityLogEntry,
  type ActivityOutcome,
  ApiError,
  type AutomationStatus,
  type RiskTier,
  approveAction,
  engageKillSwitch,
  executeAction,
  getAutomationStatus,
  listActivityLog,
  releaseKillSwitch,
  revokeAction,
} from "@/lib/api";
import { clockTime, relativeTime } from "@/lib/format";

const RISK_BADGE: Record<RiskTier, string> = {
  read_only: "border-zinc-700/60 bg-zinc-800 text-zinc-300",
  session: "border-amber-800/50 bg-amber-950/40 text-amber-300",
  sensitive: "border-red-800/60 bg-red-950/60 text-red-400 font-semibold",
};

const RISK_TITLE: Record<RiskTier, string> = {
  read_only: "Tier 0 · read-only",
  session: "Tier 1 · session",
  sensitive: "Tier 2 · sensitive",
};

const RISK_CARD: Record<RiskTier, string> = {
  read_only: "border-zinc-800/90",
  session: "border-zinc-800/90",
  sensitive: "border-red-900/30",
};

const OUTCOME_BADGE: Record<ActivityOutcome, string> = {
  executed: "border border-emerald-800/60 bg-emerald-950/50 text-emerald-400",
  approved: "border border-zinc-700 bg-zinc-800 text-zinc-300",
  // Denials and blocks are solid, not tinted: they must read at least as
  // strongly as a success, or the log quietly becomes a success log.
  denied: "bg-red-600 text-white font-bold",
  blocked: "bg-red-600 text-white font-bold",
  failed: "border border-red-800/60 bg-red-950/60 text-red-300 font-semibold",
};

const OUTCOME_ROW: Record<ActivityOutcome, string> = {
  executed: "hover:bg-zinc-900/60",
  approved: "hover:bg-zinc-900/60",
  denied: "bg-red-950/10 hover:bg-red-950/20",
  blocked: "bg-red-950/10 hover:bg-red-950/20",
  failed: "bg-red-950/10 hover:bg-red-950/20",
};

function needsArgument(name: string): boolean {
  return name === "open_url" || name === "list_directory" || name === "open_app";
}

function argumentPlaceholder(name: string): string {
  if (name === "open_url") return "https://example.com";
  if (name === "list_directory") return "an allowed absolute path";
  return "an allowed application name";
}

export default function ControlPage() {
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [log, setLog] = useState<ActivityLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; text: string } | null>(null);
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
      if (next.notice) setNotice({ tone: "zinc", text: next.notice });
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
        `${action.name} is a sensitive action.\n\nIt runs immediately on this machine. Continue?`
      );
      if (!confirmed) {
        setBusy(null);
        setNotice({ tone: "zinc", text: "Cancelled." });
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
      setNotice({ tone: "zinc", text: `${result.action_name}: ${result.summary}` });
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

  const live = Boolean(status?.enabled) && !status?.kill_switch_engaged;
  const actions = status?.actions ?? [];
  // The only honest grant list available: an action whose tier requires an
  // approval AND that is allowed right now is one a standing grant covers.
  // There is no endpoint listing grants with expiry times, so none is shown
  // rather than invented.
  const grants = actions.filter((a) => a.risk === "session" && a.allowed_now);
  const denials = log.filter((e) => e.outcome === "denied" || e.outcome === "blocked").length;
  const allowed = log.filter((e) => e.outcome === "executed" || e.outcome === "approved").length;

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <header className="z-20 flex h-12 shrink-0 select-none items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-925 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="shrink-0 text-[13px] font-semibold tracking-tight text-zinc-100">Control</h1>
          <span className="text-zinc-700">/</span>
          <span className="hidden font-mono text-xs text-zinc-400 md:inline">
            Permission &amp; safety console
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2 font-mono text-[11px] text-zinc-500">
          <span className="hidden lg:inline">
            {actions.length} {actions.length === 1 ? "action" : "actions"} in the allowlist
          </span>
          <span className="hidden text-zinc-700 lg:inline">·</span>
          <span>no shell action exists</span>
        </div>
      </header>

      {notice && <NoticeStrip tone={notice.tone}>{notice.text}</NoticeStrip>}
      {error && <NoticeStrip tone="red">{error}</NoticeStrip>}

      <main className="flex-1 space-y-6 overflow-y-auto p-6">
        <section className="grid gap-4 xl:grid-cols-3">
          <div className="space-y-3 xl:col-span-2">
            {loading ? (
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 font-mono text-xs text-zinc-500">
                Loading…
              </div>
            ) : live ? (
              <div className="rounded-lg border border-amber-500/40 bg-amber-950/20 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="mt-0.5 h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-amber-400 ring-4 ring-amber-500/20" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-bold uppercase tracking-wider text-amber-400">
                          Automation enabled
                        </span>
                        <span className="rounded border border-amber-600/40 bg-amber-900/60 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                          can act on this machine
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-amber-200/80">
                        Allowlisted actions can run on the machine this backend is on. Sensitive
                        actions still require a confirmation every single time — no grant covers them.
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              // Inert, and it looks inert. This is the default state, and a
              // fresh clone must never look armed.
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full bg-zinc-500 ring-4 ring-zinc-800/80" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-bold uppercase tracking-wider text-zinc-300">
                          {status?.kill_switch_engaged ? "Halted" : "Automation disabled"}
                        </span>
                        <span className="rounded border border-zinc-700/60 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                          default &amp; safe
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-zinc-400">
                        {status?.kill_switch_engaged
                          ? "Nothing will run, including read-only actions, until this is released."
                          : "Nothing below can run. Automation is off at the server — deliberately, so a fresh checkout cannot act on your machine."}
                      </p>
                    </div>
                  </div>
                  <span className="hidden shrink-0 rounded border border-zinc-800/80 bg-black/40 px-2 py-1 font-mono text-[11px] text-zinc-500 sm:inline">
                    AUTOMATION_ENABLED={String(Boolean(status?.enabled))}
                  </span>
                </div>
              </div>
            )}

            {status && !status.enabled && (
              <p className="px-1 font-mono text-[11px] leading-relaxed text-zinc-500">
                Set <code className="text-zinc-400">AUTOMATION_ENABLED=true</code> in{" "}
                <code className="text-zinc-400">.env</code> to change that. Never enable it on a
                hosted backend — these actions act on the machine the backend runs on.
              </p>
            )}
          </div>

          {/* First thing reachable on the page, and it never asks twice. */}
          <div className="xl:col-span-1">
            <div className="flex h-full flex-col justify-between rounded-lg border border-red-900/50 bg-gradient-to-b from-red-950/30 to-black p-4">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-red-400">
                    {live && <span className="h-2 w-2 animate-ping rounded-full bg-red-500" />}
                    <span>{live ? "Armed · one-click halt" : "Idle"}</span>
                  </span>
                </div>
                <h2 className="mt-1 text-sm font-bold tracking-wide text-red-200">
                  {status?.kill_switch_engaged ? "Halted" : "Kill switch"}
                </h2>
                <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">
                  {status?.kill_switch_engaged
                    ? "Every action is refused, including read-only ones. Release it to resume."
                    : "Halts every action immediately. No confirmation, no exceptions."}
                </p>
              </div>

              <div className="mt-4">
                {status?.kill_switch_engaged ? (
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => run("resume", releaseKillSwitch)}
                    className="w-full rounded border border-emerald-500/40 bg-emerald-950/40 px-4 py-2.5 font-mono text-xs font-bold uppercase tracking-wider text-emerald-300 transition-all hover:bg-emerald-900/50 disabled:opacity-40"
                  >
                    Release &amp; resume
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => run("stop", engageKillSwitch)}
                    // Always present and always reachable -- a stop control
                    // you have to go looking for is not one. But it only
                    // GLOWS when something can actually be stopped: on a
                    // fresh checkout, where nothing can run, the loudest
                    // thing on the page must not be an alarm for a system
                    // that is already inert.
                    className={[
                      "w-full rounded border px-4 py-2.5 font-mono text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-40",
                      live
                        ? "border-red-400/30 bg-red-600 text-white shadow-[0_0_15px_rgba(220,38,38,0.35)] hover:bg-red-500 active:bg-red-700"
                        : "border-red-900/60 bg-red-950/40 text-red-300/80 hover:bg-red-900/40",
                    ].join(" ")}
                  >
                    Stop everything
                  </button>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="space-y-3">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-xs uppercase tracking-widest text-zinc-400">
                Permitted action allowlist
              </h2>
              <span className="font-mono text-[11px] text-zinc-600">
                ({actions.length} registered — there is no runtime registration path)
              </span>
            </div>
            <span className="hidden font-mono text-[11px] text-zinc-500 lg:inline">
              A grant never covers a sensitive action
            </span>
          </div>

          {loading ? (
            <p className="font-mono text-xs text-zinc-500">Loading…</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {actions.map((action) => (
                <div
                  key={action.name}
                  className={`flex flex-col justify-between rounded-lg border bg-zinc-900/30 p-3.5 transition hover:bg-zinc-900/50 ${RISK_CARD[action.risk]}`}
                >
                  <div>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className="rounded border border-zinc-800 bg-black/60 px-2 py-0.5 font-mono text-xs font-semibold text-zinc-200">
                          {action.name}
                        </span>
                        <span
                          className={`rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase ${RISK_BADGE[action.risk]}`}
                        >
                          {RISK_TITLE[action.risk]}
                        </span>
                      </div>
                      {action.needs_confirmation && (
                        <span className="shrink-0 rounded border border-amber-700/50 bg-amber-950/40 px-2 py-0.5 font-mono text-[10px] text-amber-400/90">
                          confirm every time
                        </span>
                      )}
                    </div>

                    <p className="mt-2 text-xs leading-relaxed text-zinc-400">{action.description}</p>

                    {!action.allowed_now && action.reason && (
                      <p className="mt-1.5 font-mono text-[11px] text-amber-400/90">{action.reason}</p>
                    )}

                    {needsArgument(action.name) && (
                      <input
                        type="text"
                        value={args[action.name] ?? ""}
                        onChange={(e) => setArgs((prev) => ({ ...prev, [action.name]: e.target.value }))}
                        placeholder={argumentPlaceholder(action.name)}
                        className="mt-2.5 w-full rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-xs text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-zinc-600"
                      />
                    )}
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-zinc-800/60 pt-2.5 font-mono text-[11px]">
                    <span
                      className={`flex items-center gap-1 ${action.allowed_now ? "text-emerald-400" : "text-zinc-500"}`}
                    >
                      <span
                        className={`inline-block h-1.5 w-1.5 rounded-full ${action.allowed_now ? "bg-emerald-400" : "bg-zinc-600"}`}
                      />
                      {action.allowed_now ? "allowed now" : "not allowed"}
                    </span>

                    <div className="flex items-center gap-1.5">
                      {action.risk === "session" && (
                        <>
                          <button
                            type="button"
                            disabled={busy !== null || !live}
                            onClick={() => run(action.category, () => approveAction(action.category, "session"))}
                            className="rounded border border-zinc-700 bg-zinc-800/80 px-2 py-1 text-zinc-300 transition hover:text-zinc-100 disabled:opacity-40"
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            disabled={busy !== null || !live}
                            onClick={() => run(action.category, () => revokeAction(action.category))}
                            className="rounded border border-zinc-700 bg-zinc-800/80 px-2 py-1 text-zinc-300 transition hover:border-red-800 hover:text-red-300 disabled:opacity-40"
                          >
                            Revoke
                          </button>
                        </>
                      )}
                      <button
                        type="button"
                        disabled={busy !== null || !live}
                        onClick={() => handleExecute(action)}
                        className="rounded bg-zinc-200 px-2.5 py-1 font-medium text-zinc-900 transition hover:bg-white disabled:opacity-40"
                      >
                        Run
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-xs uppercase tracking-widest text-zinc-400">
                Active grants
              </h2>
              <span className="rounded border border-emerald-800/50 bg-emerald-950/40 px-1.5 py-0.5 font-mono text-[11px] text-emerald-400">
                {grants.length} live
              </span>
            </div>
            <span className="hidden font-mono text-[11px] text-zinc-500 lg:inline">
              Session-scoped — gone when the backend restarts
            </span>
          </div>

          <div className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/20">
            {grants.length === 0 ? (
              <p className="p-3.5 font-mono text-xs text-zinc-500">
                No standing grants. Every action that needs one will ask.
              </p>
            ) : (
              <div className="divide-y divide-zinc-800/70 font-mono text-xs">
                {grants.map((grant) => (
                  <div
                    key={grant.name}
                    className="flex flex-col justify-between gap-2 p-3.5 transition hover:bg-zinc-900/40 sm:flex-row sm:items-center"
                  >
                    <div className="min-w-0">
                      <span className="font-semibold text-zinc-200">{grant.name}</span>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
                        <span>
                          Category: <span className="text-zinc-300">{grant.category}</span>
                        </span>
                        <span className="text-zinc-700">·</span>
                        <span>{grant.description}</span>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-3 self-end sm:self-center">
                      <span className="text-emerald-400">Until this session ends</span>
                      <button
                        type="button"
                        disabled={busy !== null || !live}
                        onClick={() => run(grant.category, () => revokeAction(grant.category))}
                        className="rounded border border-zinc-700 bg-zinc-800/80 px-2 py-1 text-[11px] text-zinc-300 transition hover:border-red-800 hover:bg-red-950/60 hover:text-red-300 disabled:opacity-40"
                      >
                        Revoke
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="space-y-3 pt-2">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
            <div className="flex items-center gap-3">
              <h2 className="font-mono text-xs uppercase tracking-widest text-zinc-400">
                Audit log
              </h2>
              <span className="hidden font-mono text-[11px] text-zinc-500 md:inline">
                Every attempt, newest first — including the refused ones
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2 font-mono text-[11px]">
              <span className="rounded border border-red-900/50 bg-red-950/40 px-2 py-0.5 text-red-400">
                {denials} refused
              </span>
              <span className="rounded border border-emerald-900/50 bg-emerald-950/40 px-2 py-0.5 text-emerald-400">
                {allowed} allowed
              </span>
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-950">
            {log.length === 0 ? (
              <p className="p-3.5 font-mono text-xs text-zinc-500">Nothing has been attempted yet.</p>
            ) : (
              <table className="w-full border-collapse text-left font-mono text-xs">
                <thead>
                  <tr className="border-b border-zinc-800 bg-black/40 text-[11px] uppercase tracking-wider text-zinc-500">
                    <th className="px-4 py-2.5 font-medium">Time</th>
                    <th className="px-4 py-2.5 font-medium">Action</th>
                    <th className="px-4 py-2.5 font-medium">Persona</th>
                    <th className="px-4 py-2.5 font-medium">Outcome</th>
                    <th className="px-4 py-2.5 font-medium">Reason / result</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {log.map((entry) => (
                    <tr key={entry.id} className={`transition-colors ${OUTCOME_ROW[entry.outcome]}`}>
                      <td
                        className="whitespace-nowrap px-4 py-3 tabular-nums text-zinc-400"
                        title={new Date(entry.created_at).toLocaleString()}
                      >
                        {clockTime(entry.created_at)}
                        <span className="ml-2 text-[10px] text-zinc-600">
                          {relativeTime(entry.created_at)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={
                            entry.outcome === "denied" || entry.outcome === "blocked"
                              ? "font-semibold text-red-300"
                              : "text-zinc-200"
                          }
                        >
                          {entry.action_name}
                        </span>
                        <span
                          className={`ml-2 rounded border px-1 py-0.5 text-[10px] uppercase ${RISK_BADGE[entry.risk]}`}
                        >
                          {entry.risk.replace("_", "-")}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 uppercase text-zinc-400">
                        {entry.persona ?? "—"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] uppercase tracking-wider ${OUTCOME_BADGE[entry.outcome]}`}
                        >
                          {(entry.outcome === "denied" || entry.outcome === "blocked") && (
                            <Icon name="warning" className="h-3 w-3" strokeWidth={2} />
                          )}
                          {entry.outcome}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-zinc-400">{entry.reason ?? entry.result ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <p className="px-1 font-mono text-[11px] leading-relaxed text-zinc-600">
            This log has no foreign keys and no cascades — nothing else being deleted can erase it.
          </p>
        </section>
      </main>
    </div>
  );
}
