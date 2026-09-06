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
// ungrounded?" is answerable only if the failures are here too.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  type AgentInfo,
  type AgentRun,
  ApiError,
  listAgentRuns,
  listAgents,
  setAgentEnabled,
} from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  ok: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
  timeout: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  skipped: "border-zinc-300 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400",
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col bg-white px-4 py-6 dark:bg-black">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-sm font-semibold tracking-wide text-zinc-900 dark:text-zinc-100">
          What the specialists did
        </h1>
        <div className="flex items-center gap-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          <Link href="/documents" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Docs
          </Link>
          <Link href="/memory" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Memory
          </Link>
          <Link href="/" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            ← Chat
          </Link>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      <section className="mb-6">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Specialists
        </h2>
        <ul className="flex flex-col gap-2">
          {agents.map((agent) => (
            <li
              key={agent.name}
              className="flex items-start justify-between gap-3 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{agent.name}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                  {agent.description}
                </p>
              </div>
              <button
                type="button"
                disabled={busy === agent.name}
                onClick={() => handleToggle(agent)}
                className={[
                  "shrink-0 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase disabled:opacity-40",
                  agent.enabled
                    ? "border-emerald-400 text-emerald-700 dark:border-emerald-700 dark:text-emerald-400"
                    : "border-zinc-300 text-zinc-400 dark:border-zinc-700",
                ].join(" ")}
              >
                {agent.enabled ? "on" : "off"}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex-1">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Recent runs
        </h2>

        {loading ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
        ) : runs.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Nothing yet. Ask something that needs looking up and it will show here.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {runs.map((run) => (
              <li key={run.id} className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${
                        STATUS_STYLES[run.status] ?? STATUS_STYLES.skipped
                      }`}
                    >
                      {run.status}
                    </span>
                    <span className="truncate text-sm text-zinc-900 dark:text-zinc-100">{run.agent_name}</span>
                  </div>
                  <span className="shrink-0 text-[11px] tabular-nums text-zinc-400">
                    {formatDuration(run.duration_ms)} · {new Date(run.created_at).toLocaleTimeString()}
                  </span>
                </div>

                <p className="mt-1 truncate text-[11px] text-zinc-500 dark:text-zinc-400">{run.input}</p>

                {run.error && (
                  <p className="mt-1 rounded border border-red-200 bg-red-50 px-2 py-1 text-[11px] text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                    {run.error}
                  </p>
                )}

                {run.output && (
                  <>
                    <button
                      type="button"
                      onClick={() => setExpanded(expanded === run.id ? null : run.id)}
                      className="mt-1 text-[11px] font-semibold text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                    >
                      {expanded === run.id ? "hide what it produced" : "show what it produced"}
                    </button>
                    {expanded === run.id && (
                      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-zinc-100 px-2 py-1 text-[11px] leading-relaxed text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                        {run.output}
                      </pre>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
