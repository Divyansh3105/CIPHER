"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import Icon from "@/components/Icon";
import MemoryCard, { MEMORY_TYPE_LABEL } from "@/components/MemoryCard";
import MemoryGalaxy from "@/components/MemoryGalaxy";
import NoticeStrip, { type NoticeTone } from "@/components/NoticeStrip";
import {
  ApiError,
  type Memory,
  type MemoryGraph,
  type MemoryType,
  createMemory,
  deleteAllMemories,
  deleteMemory,
  getMemoryGraph,
  listMemories,
  updateMemory,
} from "@/lib/api";

type View = "list" | "galaxy";

const MEMORY_TYPES: MemoryType[] = ["long_term", "semantic", "episodic", "short_term"];

// Long enough that typing a word does not fire four searches, short enough
// that the list feels live. The query goes to the backend rather than being
// filtered client-side, because it is a vector search there and a substring
// match here would answer a different question.
const SEARCH_DEBOUNCE_MS = 250;

/**
 * `useSearchParams` bails out of prerendering up to the nearest Suspense
 * boundary, and a production build of a static route fails outright without
 * one (next/dist/docs, use-search-params). The boundary lives here rather
 * than in a layout so the fallback can look like this page.
 */
export default function MemoryPageRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center font-mono text-xs text-zinc-500">
          Loading memories…
        </div>
      }
    >
      <MemoryPage />
    </Suspense>
  );
}

function MemoryPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);
  const [pending, setPending] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; text: string } | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<MemoryType | "all">("all");

  // Arriving from a "Recalled" chip in chat deep-links straight to the node
  // that answered: /memory?view=galaxy&focus=<memory id>.
  const searchParams = useSearchParams();
  const focusId = searchParams.get("focus");
  const [view, setView] = useState<View>(
    searchParams.get("view") === "galaxy" || focusId ? "galaxy" : "list"
  );
  const [graph, setGraph] = useState<MemoryGraph | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);

  const loadGraph = useCallback(
    async (options: { neighbours: number; minSimilarity: number | null }) => {
      setGraphLoading(true);
      try {
        setGraph(await getMemoryGraph(options));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Failed to load the memory graph.");
      } finally {
        setGraphLoading(false);
      }
    },
    []
  );

  // Loaded from the tab click rather than an effect on `view`. It is an
  // O(n^2) similarity query, so it should run when someone asks for it, not
  // as a side effect of a state change -- and fetching here means switching
  // back always reflects edits made in the list view instead of showing a
  // stale picture.
  function showGalaxy() {
    setView("galaxy");
    void loadGraph({ neighbours: 3, minSimilarity: null });
  }

  // Only for the deep-linked case, where the galaxy is the landing view and
  // there was no click to hang the fetch off. Deliberately uses the promise
  // callback rather than `loadGraph`: setting state synchronously inside an
  // effect body triggers a cascading render (react-hooks/set-state-in-effect),
  // and the component shows its own loading state meanwhile.
  useEffect(() => {
    if (view !== "galaxy") return;
    getMemoryGraph({ neighbours: 3 })
      .then(setGraph)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load the memory graph.")
      );
    // Mount only: every later switch into the galaxy goes through showGalaxy().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // Also the initial load: on mount the query is empty, which is exactly the
  // "everything" request.
  useEffect(() => {
    let cancelled = false;
    listMemories(debouncedQuery || undefined)
      .then((result) => {
        if (cancelled) return;
        setMemories(result);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load memories.");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  async function handleCreate() {
    const content = draft.trim();
    if (content.length === 0) return;

    setError(null);
    setNotice(null);
    setPending(true);
    try {
      const result = await createMemory(content);
      setDraft("");
      setComposing(false);
      if (result.deduplicated) {
        setNotice({ tone: "zinc", text: "You already remember something very similar." });
        setMemories((prev) => prev.map((m) => (m.id === result.memory.id ? result.memory : m)));
      } else {
        setMemories((prev) => [result.memory, ...prev]);
      }
      if (result.memory.embedding_pending) {
        // Amber, not zinc: it was saved but it cannot be recalled, which is
        // a degraded outcome rather than a confirmation.
        setNotice({
          tone: "amber",
          text: "Saved, but not searchable yet — it has no embedding, so CIPHER will not recall it.",
        });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save that memory.");
    } finally {
      setPending(false);
    }
  }

  async function handleSave(id: string, content: string) {
    setError(null);
    setBusyId(id);
    try {
      const updated = await updateMemory(id, content);
      setMemories((prev) => prev.map((m) => (m.id === id ? updated : m)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update that memory.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    setBusyId(id);
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete that memory.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleForgetEverything() {
    if (!window.confirm("Forget everything? This deletes every stored memory and can't be undone.")) {
      return;
    }
    setError(null);
    setNotice(null);
    setPending(true);
    try {
      const result = await deleteAllMemories();
      setMemories([]);
      setNotice({
        tone: "zinc",
        text: `Forgot ${result.deleted} ${result.deleted === 1 ? "memory" : "memories"}.`,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to forget everything.");
    } finally {
      setPending(false);
    }
  }

  const visible =
    typeFilter === "all" ? memories : memories.filter((m) => m.memory_type === typeFilter);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <header className="z-20 flex h-12 shrink-0 select-none items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-925 px-4">
        <div className="flex min-w-0 items-center gap-4">
          <div className="flex shrink-0 items-baseline gap-2">
            <h1 className="text-[13px] font-semibold tracking-tight text-zinc-100">Memory</h1>
            <span className="font-mono text-[11px] text-zinc-500">
              {view === "galaxy"
                ? graph
                  ? `${graph.total} ${graph.total === 1 ? "item" : "items"}`
                  : "…"
                : `${visible.length} ${visible.length === 1 ? "item" : "items"}`}
            </span>
          </div>

          <div className="relative hidden w-72 lg:block">
            <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5 text-zinc-500">
              <Icon name="search" className="h-3.5 w-3.5" />
            </span>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search memories…"
              className="w-full rounded border border-zinc-800 bg-zinc-900 py-1.5 pl-8 pr-3 text-xs text-zinc-200 outline-none placeholder:text-zinc-500 focus:border-zinc-600"
            />
          </div>
        </div>

        <div
          role="tablist"
          aria-label="Memory view"
          className="flex shrink-0 items-center rounded-md border border-zinc-800 bg-zinc-900 p-0.5"
        >
          {(["list", "galaxy"] as const).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={view === option}
              onClick={() => (option === "galaxy" ? showGalaxy() : setView("list"))}
              className={[
                "flex items-center gap-1.5 rounded px-3 py-1 text-xs font-medium transition",
                view === option
                  ? "bg-zinc-800 text-zinc-100 shadow-sm"
                  : "text-zinc-400 hover:text-zinc-200",
              ].join(" ")}
            >
              <Icon name={option === "list" ? "list" : "globe"} className="h-3.5 w-3.5" />
              <span>{option === "list" ? "List" : "Galaxy"}</span>
            </button>
          ))}
        </div>

        <div className="flex shrink-0 items-center gap-3">
          <label className="hidden items-center gap-1.5 text-xs xl:flex">
            <span className="text-[11px] text-zinc-500">Type:</span>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value as MemoryType | "all")}
              className="rounded border border-zinc-800 bg-zinc-900 py-1 pl-2 pr-6 text-xs text-zinc-300 outline-none focus:border-zinc-700"
            >
              <option value="all">All types</option>
              {MEMORY_TYPES.map((type) => (
                <option key={type} value={type}>
                  {MEMORY_TYPE_LABEL[type]}
                </option>
              ))}
            </select>
          </label>

          <span className="hidden h-3 w-px bg-zinc-800 xl:block" />

          {/* Kept quiet and visually far from everything else: it is the one
              action on this page that cannot be undone. */}
          <button
            type="button"
            disabled={pending || memories.length === 0}
            onClick={handleForgetEverything}
            title="Permanently delete every stored memory"
            className="rounded border border-zinc-850 px-2.5 py-1 font-mono text-[11px] text-zinc-500 transition duration-150 hover:border-red-900/50 hover:bg-red-950/20 hover:text-red-400 disabled:opacity-40 disabled:hover:border-zinc-850 disabled:hover:bg-transparent disabled:hover:text-zinc-500"
          >
            Forget everything
          </button>
        </div>
      </header>

      {notice && <NoticeStrip tone={notice.tone}>{notice.text}</NoticeStrip>}
      {error && <NoticeStrip tone="red">{error}</NoticeStrip>}

      <main className="relative flex-1 overflow-hidden bg-zinc-950">
        {view === "galaxy" ? (
          <MemoryGalaxy
            graph={graph}
            loading={graphLoading}
            onReload={loadGraph}
            focusId={focusId}
          />
        ) : (
          <div className="absolute inset-0 overflow-y-auto p-6">
            <div className="mx-auto max-w-4xl space-y-4">
              <div className="flex items-center justify-between border-b border-zinc-850 pb-3">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-zinc-200">
                    What CIPHER remembers
                  </span>
                  <span className="font-mono text-[11px] text-zinc-500">
                    {debouncedQuery ? "Sorted by relevance" : "Sorted by recency descending"}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setComposing((open) => !open)}
                  className="rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 transition hover:bg-zinc-800"
                >
                  {composing ? "Cancel" : "+ Add memory manually"}
                </button>
              </div>

              {composing && (
                <div className="rounded-lg border border-zinc-850 bg-zinc-900/70 p-3.5">
                  <label
                    htmlFor="memory-draft"
                    className="mb-2 block font-mono text-[11px] uppercase tracking-wider text-zinc-500"
                  >
                    Teach it something
                  </label>
                  <textarea
                    id="memory-draft"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="e.g. I use Neovim, not VS Code."
                    rows={2}
                    disabled={pending}
                    autoFocus
                    className="w-full resize-none rounded border border-zinc-800 bg-zinc-950 p-2.5 text-xs leading-relaxed text-zinc-100 outline-none focus:border-zinc-600 disabled:opacity-50"
                  />
                  <button
                    type="button"
                    disabled={pending || draft.trim().length === 0}
                    onClick={handleCreate}
                    className="mt-2 rounded bg-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-900 transition hover:bg-white disabled:opacity-40"
                  >
                    Save memory
                  </button>
                </div>
              )}

              {loading ? (
                <p className="font-mono text-xs text-zinc-500">Loading…</p>
              ) : visible.length === 0 ? (
                <p className="font-mono text-xs text-zinc-500">
                  {debouncedQuery
                    ? `Nothing matches “${debouncedQuery}”.`
                    : typeFilter !== "all"
                      ? `No ${MEMORY_TYPE_LABEL[typeFilter].toLowerCase()} memories.`
                      : "Nothing stored yet. Add one above, or say “remember that…” in chat."}
                </p>
              ) : (
                <div className="space-y-2">
                  {visible.map((memory) => (
                    <MemoryCard
                      key={memory.id}
                      memory={memory}
                      onSave={handleSave}
                      onDelete={handleDelete}
                      busy={busyId === memory.id}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
