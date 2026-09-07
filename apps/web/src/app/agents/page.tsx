"use client";

// Phase 6: what the specialists actually did.
//
// Multi-agent systems fail in a particular way -- something invisible
// decides something, and the only evidence is an answer that is quietly
// worse than it should have been. This page is the antidote: every run,
// including the ones that failed, timed out, or decided to do nothing,
// with what it was given, what it produced, and how long it took.
//
// Listing only successes would defeat the purpose. "Why was that answer
// ungrounded?" is answerable only if the failures are here too -- which is
// why failed and timed-out rows are the LOUDEST things on the page rather
// than greyed out the way a dashboard would normally dim them.

import { useCallback, useEffect, useState } from "react";
import Icon from "@/components/Icon";
import NoticeStrip from "@/components/NoticeStrip";
import {
  type AgentInfo,
  type AgentRun,
  type AgentRunStatus,
  ApiError,
  listAgentRuns,
  listAgents,
  setAgentEnabled,
} from "@/lib/api";
import { clockTime, duration as formatDuration, relativeTime } from "@/lib/format";

const OUTCOME_STYLE: Record<AgentRunStatus, string> = {
  // Success is the quiet one. It is the expected outcome and does not need
  // to compete for attention with the rows that explain a bad answer.
  ok: "bg-zinc-900 border-zinc-700/80 text-zinc-200 font-medium",
  failed: "bg-red-950/70 border-red-500 text-red-300 font-semibold shadow-sm",
  timeout: "bg-amber-950/70 border-amber-500 text-amber-300 font-semibold shadow-sm",
  skipped: "bg-zinc-900/60 border-zinc-800 text-zinc-400 font-medium",
};

const OUTCOME_DOT: Record<AgentRunStatus, string> = {
  ok: "bg-emerald-400",
  failed: "bg-red-400",
  timeout: "bg-amber-400",
  skipped: "bg-zinc-500",
};

const ROW_STYLE: Record<AgentRunStatus, string> = {
  ok: "bg-zinc-900/20 hover:bg-zinc-900/60",
  failed: "bg-red-950/10 hover:bg-red-950/20",
  timeout: "bg-amber-950/10 hover:bg-amber-950/20",
  skipped: "bg-zinc-900/20 hover:bg-zinc-900/60",
};

const OUTCOMES: AgentRunStatus[] = ["ok", "failed", "timeout", "skipped"];

function monogram(name: string): string {
  return name.slice(0, 2).toUpperCase();
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<AgentRunStatus | "all">("all");
  const [query, setQuery] = useState("");

  const refresh = useCallback(async () => {
    const [a, r] = await Promise.all([listAgents(), listAgentRuns()]);
    setAgents(a);
    setRuns(r);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAgents(), listAgentRuns()])
      .then(([a, r]) => {
        if (cancelled) return;
        setAgents(a);
        setRuns(r);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load agent activity.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleToggle(agent: AgentInfo) {
    setError(null);
    setBusy(agent.name);
    try {
      await setAgentEnabled(agent.name, !agent.enabled);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change that setting.");
    } finally {
      setBusy(null);
    }
  }

  // Every number in the header and the card footers is derived from the runs
  // actually returned. Nothing here is a placeholder: an activity page that
  // shows a made-up success rate is worse than one that shows none.
  const succeeded = runs.filter((r) => r.status === "ok").length;
  const successRate = runs.length > 0 ? Math.round((succeeded / runs.length) * 100) : null;

  function statsFor(name: string) {
    const own = runs.filter((r) => r.agent_name === name);
    if (own.length === 0) return { invoked: 0, avg: null as number | null };
    const total = own.reduce((sum, r) => sum + r.duration_ms, 0);
    return { invoked: own.length, avg: Math.round(total / own.length) };
  }

  const visibleRuns = runs.filter((run) => {
    if (outcomeFilter !== "all" && run.status !== outcomeFilter) return false;
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return (
      run.agent_name.toLowerCase().includes(needle) ||
      run.input.toLowerCase().includes(needle) ||
      (run.output ?? "").toLowerCase().includes(needle)
    );
  });

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <header className="z-20 flex h-12 shrink-0 select-none items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-925 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="shrink-0 text-[13px] font-semibold tracking-tight text-zinc-100">Agents</h1>
          <span className="hidden h-4 w-px bg-zinc-800 md:block" />
          <div className="hidden items-center gap-2 font-mono text-xs text-zinc-400 md:flex">
            <span>{agents.length} registered</span>
            <span className="text-zinc-700">·</span>
            <span>
              {runs.length} {runs.length === 1 ? "run" : "runs"} recorded
            </span>
            {successRate !== null && (
              <>
                <span className="text-zinc-700">·</span>
                <span className={successRate === 100 ? "text-emerald-400" : "text-zinc-300"}>
                  {successRate}% ok
                </span>
              </>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter runs…"
            className="hidden w-56 rounded border border-zinc-800 bg-zinc-900/80 px-3 py-1.5 font-mono text-xs text-zinc-200 outline-none placeholder:text-zinc-500 focus:border-zinc-600 lg:block"
          />
          <label className="flex items-center gap-1 rounded border border-zinc-800 bg-zinc-900/80 px-2 py-1 text-xs">
            <span className="text-zinc-500">Outcome:</span>
            <select
              value={outcomeFilter}
              onChange={(e) => setOutcomeFilter(e.target.value as AgentRunStatus | "all")}
              className="bg-transparent font-medium text-zinc-200 outline-none"
            >
              <option value="all">All (incl. errors)</option>
              {OUTCOMES.map((outcome) => (
                <option key={outcome} value={outcome}>
                  {outcome}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void refresh().catch(() => setError("Could not refresh."))}
            title="Reload agents and runs"
            className="flex items-center gap-1.5 rounded border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5 font-mono text-xs text-zinc-400 transition-colors hover:bg-zinc-800/80 hover:text-zinc-200"
          >
            <Icon name="refresh" className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>
      </header>

      {error && <NoticeStrip tone="red">{error}</NoticeStrip>}

      <div className="flex-1 space-y-6 overflow-y-auto p-6">
        <section>
          <div className="mb-3 flex items-center justify-between">
            <span className="font-mono text-xs font-semibold uppercase tracking-wider text-zinc-400">
              Agent pool
            </span>
            <span className="font-mono text-[11px] text-zinc-500">
              The orchestrator picks one; a disabled agent is never offered
            </span>
          </div>

          {loading ? (
            <p className="font-mono text-xs text-zinc-500">Loading…</p>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {agents.map((agent) => {
                const stats = statsFor(agent.name);
                return (
                  <div
                    key={agent.name}
                    className="flex flex-col justify-between rounded-lg border border-zinc-800/90 bg-zinc-900/70 p-4 transition-all hover:border-zinc-700/80"
                  >
                    <div>
                      <div className="mb-2 flex items-start justify-between gap-3">
                        <div className="flex min-w-0 items-center gap-2">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-zinc-700/60 bg-zinc-800/80 font-mono text-xs font-bold text-zinc-200">
                            {monogram(agent.name)}
                          </div>
                          <div className="flex min-w-0 items-center gap-2">
                            <h2 className="truncate text-sm font-semibold capitalize text-zinc-100">
                              {agent.name}
                            </h2>
                            <span className="shrink-0 rounded border border-zinc-700/50 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                              {agent.timeout_seconds}s
                            </span>
                          </div>
                        </div>

                        <button
                          type="button"
                          role="switch"
                          aria-checked={agent.enabled}
                          aria-label={`Toggle the ${agent.name} agent`}
                          disabled={busy === agent.name}
                          onClick={() => handleToggle(agent)}
                          className={[
                            "relative h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors disabled:opacity-40",
                            agent.enabled ? "bg-zinc-200" : "bg-zinc-800",
                          ].join(" ")}
                        >
                          <span
                            className={[
                              "absolute top-[3px] h-3.5 w-3.5 rounded-full transition-all",
                              agent.enabled ? "right-[3px] bg-black" : "left-[3px] bg-zinc-500",
                            ].join(" ")}
                          />
                        </button>
                      </div>

                      <p className="mt-2.5 text-xs leading-relaxed text-zinc-400">
                        {agent.description}
                      </p>
                    </div>

                    <div className="mt-4 flex items-center justify-between border-t border-zinc-800/80 pt-4 font-mono text-[11px] text-zinc-500">
                      <span>
                        Avg:{" "}
                        <strong className="font-normal text-zinc-300">
                          {stats.avg === null ? "—" : formatDuration(stats.avg)}
                        </strong>
                      </span>
                      <span>
                        Runs: <strong className="font-normal text-zinc-300">{stats.invoked}</strong>
                      </span>
                      <span
                        className={`flex items-center gap-1 ${agent.enabled ? "text-emerald-400" : "text-zinc-500"}`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${agent.enabled ? "bg-emerald-400" : "bg-zinc-600"}`}
                        />
                        {agent.enabled ? "active" : "off"}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <div className="flex items-end justify-between gap-4">
            <div>
              <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-zinc-400">
                Activity trail &amp; run history
              </h2>
              <p className="mt-0.5 text-[12px] text-zinc-500">
                Why the assistant answered the way it did. Failures and timeouts are kept at full
                visibility — they are the entries this view exists for.
              </p>
            </div>
            <span className="shrink-0 font-mono text-[11px] text-zinc-500">
              {visibleRuns.length} of {runs.length} shown
            </span>
          </div>

          <div className="overflow-hidden rounded-lg border border-zinc-800/90 bg-zinc-950 shadow-sm">
            <div className="grid grid-cols-12 gap-3 border-b border-zinc-800 bg-zinc-900/90 px-4 py-2.5 font-mono text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              <div className="col-span-2">Agent</div>
              <div className="col-span-4">Routing decision</div>
              <div className="col-span-2 text-right">Duration</div>
              <div className="col-span-2 text-center">Outcome</div>
              <div className="col-span-2 text-right">Timestamp</div>
            </div>

            <div className="divide-y divide-zinc-800/70">
              {loading ? (
                <p className="p-4 font-mono text-xs text-zinc-500">Loading…</p>
              ) : visibleRuns.length === 0 ? (
                <p className="p-4 font-mono text-xs text-zinc-500">
                  {runs.length === 0
                    ? "Nothing yet. Ask something that needs looking up and it will show here."
                    : "No run matches that filter."}
                </p>
              ) : (
                visibleRuns.map((run) => {
                  const open = expanded === run.id;
                  return (
                    <div key={run.id} className={ROW_STYLE[run.status]}>
                      <button
                        type="button"
                        onClick={() => setExpanded(open ? null : run.id)}
                        aria-expanded={open}
                        className="grid w-full cursor-pointer grid-cols-12 items-center gap-3 px-4 py-3 text-left text-[13px]"
                      >
                        <div className="col-span-2 flex min-w-0 items-center gap-2">
                          <span className="truncate font-mono text-xs font-semibold capitalize text-zinc-200">
                            {run.agent_name}
                          </span>
                        </div>

                        <div className="col-span-4 truncate font-mono text-xs text-zinc-300" title={run.input}>
                          {run.input}
                        </div>

                        <div
                          className={[
                            "col-span-2 text-right font-mono text-xs tabular-nums",
                            run.status === "timeout" ? "font-semibold text-amber-300" : "text-zinc-300",
                          ].join(" ")}
                        >
                          {formatDuration(run.duration_ms)}
                        </div>

                        <div className="col-span-2 flex justify-center">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded border px-2.5 py-0.5 font-mono text-xs ${OUTCOME_STYLE[run.status]}`}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${OUTCOME_DOT[run.status]}`} />
                            {run.status}
                          </span>
                        </div>

                        <div
                          className="col-span-2 text-right font-mono text-xs tabular-nums text-zinc-400"
                          title={new Date(run.created_at).toLocaleString()}
                        >
                          {clockTime(run.created_at)}
                        </div>
                      </button>

                      {/* A failure's diagnostic is shown without expanding:
                          the reason a run failed is the point of the row, and
                          hiding it behind a click hides the answer. */}
                      {run.error && (
                        <div className="border-t border-red-900/40 bg-red-950/15 px-6 py-3 font-mono text-xs">
                          <div className="flex items-start justify-between gap-4 text-zinc-300">
                            <div className="flex min-w-0 items-start gap-2">
                              <span className="shrink-0 font-semibold text-red-400">
                                Failure diagnostic:
                              </span>
                              <span className="min-w-0 text-zinc-300">{run.error}</span>
                            </div>
                            <span className="shrink-0 text-[11px] text-zinc-500">
                              {relativeTime(run.created_at)}
                            </span>
                          </div>
                        </div>
                      )}

                      {open && (
                        <div className="grid gap-6 border-t border-zinc-800/80 bg-black/40 px-6 py-4 text-xs lg:grid-cols-2">
                          <div className="min-w-0 space-y-2">
                            <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                              What the agent was asked
                            </span>
                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-zinc-800/80 bg-zinc-950 p-3 font-mono text-[12px] leading-relaxed text-zinc-300">
                              {run.input}
                            </pre>
                          </div>

                          <div className="min-w-0 space-y-2">
                            <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                              What it contributed
                            </span>
                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-zinc-800/80 bg-zinc-950 p-3 font-mono text-[12px] leading-relaxed text-zinc-300">
                              {run.output ??
                                "Nothing — this run contributed no text to the reply, so the answer was composed without it."}
                            </pre>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
