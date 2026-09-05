"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import MemoryCard from "@/components/MemoryCard";
import {
  ApiError,
  type Memory,
  createMemory,
  deleteAllMemories,
  deleteMemory,
  listMemories,
  updateMemory,
} from "@/lib/api";

export default function MemoryPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listMemories()
      .then((result) => {
        if (!cancelled) setMemories(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load memories.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCreate() {
    const content = draft.trim();
    if (content.length === 0) return;

    setError(null);
    setNotice(null);
    setPending(true);
    try {
      const result = await createMemory(content);
      setDraft("");
      if (result.deduplicated) {
        setNotice("You already remember something very similar.");
        setMemories((prev) => prev.map((m) => (m.id === result.memory.id ? result.memory : m)));
      } else {
        setMemories((prev) => [result.memory, ...prev]);
      }
      if (result.memory.embedding_pending) {
        setNotice((prev) =>
          prev ? `${prev} Also: saved, but not searchable yet.` : "Saved, but not searchable yet."
        );
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
      setNotice(`Forgot ${result.deleted} ${result.deleted === 1 ? "memory" : "memories"}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to forget everything.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col bg-white px-4 py-6 dark:bg-black">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-sm font-semibold tracking-wide text-zinc-900 dark:text-zinc-100">
          What CIPHER remembers
        </h1>
        <Link
          href="/"
          className="text-xs font-semibold uppercase tracking-wide text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Chat
        </Link>
      </header>

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
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Teach it something
        </label>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. I use Neovim, not VS Code."
          rows={2}
          disabled={pending}
          className="w-full resize-none rounded-xl border border-zinc-300 bg-transparent p-3 text-sm text-zinc-900 outline-none focus:border-zinc-500 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-100"
        />
        <button
          type="button"
          disabled={pending || draft.trim().length === 0}
          onClick={handleCreate}
          className="mt-2 rounded-lg bg-zinc-900 px-4 py-1.5 text-xs font-semibold text-zinc-50 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
        >
          Save
        </button>
      </section>

      <section className="flex-1">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {memories.length} {memories.length === 1 ? "memory" : "memories"}
          </span>
          {memories.length > 0 && (
            <button
              type="button"
              disabled={pending}
              onClick={handleForgetEverything}
              className="text-xs font-semibold text-red-600 hover:underline disabled:opacity-40 dark:text-red-400"
            >
              Forget everything
            </button>
          )}
        </div>

        {loading ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
        ) : memories.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Nothing stored yet.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {memories.map((memory) => (
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
      </section>
    </div>
  );
}
