"use client";

// Phase 5: uploaded documents, and whether they are actually searchable.
//
// Status is the whole reason this page is more than a file list. Ingestion
// runs in the background, so a document exists before it can answer
// anything, and "uploaded" and "searchable" are different states the user
// has to be able to tell apart. A failed document shows why, in words,
// rather than disappearing or sitting at "pending" forever.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  type DocumentRecord,
  type ToolInfo,
  deleteDocument,
  listDocuments,
  listTools,
  uploadDocument,
} from "@/lib/api";

//: How often to re-check while something is still ingesting. Polling only
//: runs while at least one document is pending, so an idle page is silent.
const POLL_MS = 2500;

const STATUS_STYLES: Record<string, string> = {
  pending: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  ready: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const [docs, toolList] = await Promise.all([
      listDocuments(),
      listTools().catch(() => [] as ToolInfo[]),
    ]);
    setDocuments(docs);
    if (toolList.length) setTools(toolList);
    return docs;
  }, []);

  useEffect(() => {
    let cancelled = false;
    listDocuments()
      .then((result) => {
        if (!cancelled) setDocuments(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load documents.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    listTools()
      .then((result) => {
        if (!cancelled) setTools(result);
      })
      .catch(() => {
        // Non-fatal: the capability line just stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll only while something is actually ingesting. A permanent interval
  // would keep a finished page talking to the backend forever for nothing.
  const hasPending = documents.some((d) => d.status === "pending");
  useEffect(() => {
    if (!hasPending) return;
    const timer = setInterval(() => {
      void refresh().catch(() => {
        // Transient failures are not worth a banner mid-poll.
      });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [hasPending, refresh]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    setNotice(null);
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        const result = await uploadDocument(file);
        if (result.deduplicated) {
          setNotice(`${file.name} is already uploaded — nothing was added.`);
        }
      }
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That upload failed.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    setNotice(null);
    setBusyId(id);
    try {
      await deleteDocument(id);
      setDocuments((prev) => prev.filter((d) => d.id !== id));
      // Deleting the last document turns the document_search tool off, so
      // the capability line has to be re-read, not assumed.
      listTools().then(setTools).catch(() => {});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that document.");
    } finally {
      setBusyId(null);
    }
  }

  const docSearch = tools.find((t) => t.name === "document_search");
  const webSearch = tools.find((t) => t.name === "web_search");

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col bg-white px-4 py-6 dark:bg-black">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-sm font-semibold tracking-wide text-zinc-900 dark:text-zinc-100">
          Documents CIPHER can read
        </h1>
        <div className="flex items-center gap-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          <Link href="/memory" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            Memory
          </Link>
          <Link href="/" className="hover:text-zinc-900 dark:hover:text-zinc-100">
            ← Chat
          </Link>
        </div>
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
          Upload a file
        </label>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md,.markdown"
          disabled={uploading}
          onChange={(e) => handleFiles(e.target.files)}
          className="block w-full text-xs text-zinc-600 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-900 file:px-4 file:py-1.5 file:text-xs file:font-semibold file:text-zinc-50 disabled:opacity-50 dark:text-zinc-400 dark:file:bg-zinc-100 dark:file:text-zinc-900"
        />
        <p className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400">
          PDF, DOCX, TXT or Markdown, up to 10 MB. Scanned PDFs are images, not text — those need
          OCR first, which CIPHER does not do yet.
          {uploading && " Uploading…"}
        </p>
      </section>

      {(docSearch || webSearch) && (
        <section className="mb-6 rounded-lg border border-zinc-200 px-3 py-2 text-[11px] text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span className="font-semibold uppercase tracking-wide">What it can look up</span>
          <ul className="mt-1 space-y-0.5">
            {[docSearch, webSearch].filter(Boolean).map((tool) => (
              <li key={tool!.name}>
                <span className={tool!.available ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-400"}>
                  {tool!.available ? "●" : "○"}
                </span>{" "}
                {tool!.name.replace("_", " ")}
                {!tool!.available && tool!.reason ? ` — ${tool!.reason}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="flex-1">
        <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {documents.length} {documents.length === 1 ? "document" : "documents"}
        </div>

        {loading ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>
        ) : documents.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Nothing uploaded yet. Add a file and CIPHER can answer questions from it, with citations.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {documents.map((document) => (
              <li
                key={document.id}
                className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-zinc-900 dark:text-zinc-100">{document.filename}</p>
                    <p className="mt-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                      {formatSize(document.size_bytes)}
                      {document.page_count ? ` · ${document.page_count} pages` : ""}
                      {document.status === "ready"
                        ? ` · ${document.chunk_count} searchable passage${document.chunk_count === 1 ? "" : "s"}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${
                        STATUS_STYLES[document.status] ?? STATUS_STYLES.pending
                      }`}
                    >
                      {document.status === "pending" ? "indexing…" : document.status}
                    </span>
                    <button
                      type="button"
                      disabled={busyId === document.id}
                      onClick={() => handleDelete(document.id)}
                      className="text-[11px] font-semibold text-red-600 hover:underline disabled:opacity-40 dark:text-red-400"
                    >
                      Delete
                    </button>
                  </div>
                </div>
                {document.error && (
                  <p className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-[11px] text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                    {document.error}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
