"use client";

// Phase 5: uploaded documents, and whether they are actually searchable.
//
// Status is the whole reason this page is more than a file list. Ingestion
// runs in the background, so a document exists before it can answer
// anything, and "uploaded" and "searchable" are different states the user
// has to be able to tell apart. A failed document shows why, in words,
// rather than disappearing or sitting at "pending" forever.
//
// The right-hand pane exists for the same reason: a citation reads
// "handbook.pdf · page 4", and this is where you go to read page 4 and check
// that the answer actually came from it.

import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon";
import NoticeStrip, { type NoticeTone } from "@/components/NoticeStrip";
import {
  ApiError,
  type DocumentChunk,
  type DocumentRecord,
  type ToolInfo,
  deleteDocument,
  listDocumentChunks,
  listDocuments,
  listTools,
  uploadDocument,
} from "@/lib/api";
import { fileSize, relativeTime } from "@/lib/format";

//: How often to re-check while something is still ingesting. Polling only
//: runs while at least one document is pending, so an idle page is silent.
const POLL_MS = 2500;

// Three states, not four. The backend has exactly `pending | ready | failed`
// -- there is no separate queued/processing distinction to draw, and
// inventing one would be a label with nothing behind it.
const STATUS_STYLE: Record<string, string> = {
  pending: "bg-indigo-950/40 text-indigo-300 border-indigo-800/50",
  ready: "bg-emerald-950/40 text-emerald-400 border-emerald-800/50",
  failed: "bg-red-950/50 text-red-400 border-red-800/60",
};

const STATUS_DOT: Record<string, string> = {
  pending: "bg-indigo-400 animate-ping",
  ready: "bg-emerald-400",
  failed: "bg-red-400",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Indexing",
  ready: "Ready",
  failed: "Failed",
};

function formatKind(document: DocumentRecord): string {
  const extension = document.filename.split(".").pop();
  return (extension ?? "file").slice(0, 5).toUpperCase();
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; text: string } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  // Which document `chunks` actually belongs to. Loading is DERIVED from the
  // gap between this and `selectedId` rather than being its own flag set at
  // the top of the effect -- a synchronous setState in an effect body causes
  // a cascading render (react-hooks/set-state-in-effect).
  const [chunksFor, setChunksFor] = useState<string | null>(null);
  const [chunkFilter, setChunkFilter] = useState("");
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

  // Passages are fetched per selection rather than eagerly for every
  // document: a 140-page log is hundreds of passages nobody asked to see.
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    listDocumentChunks(selectedId)
      .then((result) => {
        if (cancelled) return;
        setChunks(result);
        setChunksFor(selectedId);
      })
      .catch((err) => {
        if (cancelled) return;
        setChunks([]);
        setChunksFor(selectedId);
        setError(err instanceof ApiError ? err.message : "Could not read that document's passages.");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    setNotice(null);
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        const result = await uploadDocument(file);
        if (result.deduplicated) {
          setNotice({ tone: "zinc", text: `${file.name} is already uploaded — nothing was added.` });
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
      if (selectedId === id) {
        setSelectedId(null);
        setChunks([]);
      }
      // Deleting the last document turns the document_search tool off, so
      // the capability line has to be re-read, not assumed.
      listTools().then(setTools).catch(() => {});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that document.");
    } finally {
      setBusyId(null);
    }
  }

  const chunksLoading = selectedId !== null && chunksFor !== selectedId;
  const docSearch = tools.find((t) => t.name === "document_search");
  const selected = documents.find((d) => d.id === selectedId) ?? null;
  const totalPassages = documents.reduce((sum, d) => sum + d.chunk_count, 0);
  const totalBytes = documents.reduce((sum, d) => sum + d.size_bytes, 0);
  const visibleChunks = chunkFilter.trim()
    ? chunks.filter((c) => c.content.toLowerCase().includes(chunkFilter.trim().toLowerCase()))
    : chunks;

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
      <header className="z-20 flex h-12 shrink-0 select-none items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-925 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <h1 className="shrink-0 text-[13px] font-semibold tracking-tight text-zinc-100">Documents</h1>
          <span className="hidden rounded border border-zinc-800 bg-zinc-900 px-2 py-0.5 font-mono text-[11px] text-zinc-400 md:inline-flex">
            {documents.length} {documents.length === 1 ? "file" : "files"} · {totalPassages}{" "}
            {totalPassages === 1 ? "passage" : "passages"} · {fileSize(totalBytes)}
          </span>
        </div>

        {docSearch && (
          <div
            className="flex shrink-0 items-center gap-1.5 rounded border border-zinc-800/80 bg-zinc-900/60 px-2 py-1 font-mono text-[11px]"
            title={docSearch.available ? "CIPHER can answer from these files" : docSearch.reason}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${docSearch.available ? "bg-emerald-500" : "bg-zinc-600"}`}
            />
            <span className={docSearch.available ? "text-zinc-300" : "text-zinc-500"}>
              {docSearch.available ? "document search on" : `document search off — ${docSearch.reason}`}
            </span>
          </div>
        )}
      </header>

      {notice && <NoticeStrip tone={notice.tone}>{notice.text}</NoticeStrip>}
      {error && <NoticeStrip tone="red">{error}</NoticeStrip>}

      {/* Understated on purpose: a giant dashed rectangle is a consumer
          pattern, and this page is used more for reading status than for
          uploading. */}
      <section
        aria-label="Upload"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        className={[
          "mx-4 mt-3 mb-2 flex shrink-0 items-center justify-between gap-4 rounded-lg border border-dashed p-2.5 text-xs transition",
          dragging ? "border-zinc-600 bg-zinc-900/60" : "border-zinc-800/90 bg-zinc-950/40 hover:border-zinc-700",
        ].join(" ")}
      >
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-zinc-800 bg-zinc-900 text-zinc-400">
            <Icon name="upload" className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <span className="font-medium text-zinc-200">Drop PDF, DOCX, TXT or MD to ingest</span>
            <span className="mx-1.5 text-zinc-500">or</span>
            <button
              type="button"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
              className="font-medium text-zinc-300 underline underline-offset-2 hover:text-white disabled:opacity-50"
            >
              browse files
            </button>
            <span className="ml-2 font-mono text-[11px] text-zinc-500">
              {uploading ? "· uploading…" : "· chunked without spanning pages, so citations name a real page"}
            </span>
          </div>
        </div>

        <div className="hidden shrink-0 items-center gap-2 font-mono text-[11px] text-zinc-500 lg:flex">
          {["PDF", "DOCX", "TXT", "MD"].map((format) => (
            <span key={format} className="rounded border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-zinc-400">
              {format}
            </span>
          ))}
          <span className="text-zinc-700">|</span>
          <span>Max 10MB</span>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md,.markdown"
          disabled={uploading}
          onChange={(e) => handleFiles(e.target.files)}
          className="hidden"
        />
      </section>

      <div className="mx-4 mb-3 flex min-h-0 flex-1 overflow-hidden rounded-lg border border-zinc-800/80 bg-zinc-950">
        {/* Left: the file table. */}
        <main aria-label="Documents" className="flex w-[58%] min-w-0 flex-col border-r border-zinc-800/80">
          <div className="grid shrink-0 grid-cols-12 border-b border-zinc-800 bg-zinc-900/40 px-3.5 py-2 font-mono text-[11px] text-zinc-500">
            <div className="col-span-5">NAME / FORMAT</div>
            <div className="col-span-2 text-right">SIZE / PAGES</div>
            <div className="col-span-2 text-right">PASSAGES</div>
            <div className="col-span-3 pl-4">STATUS</div>
          </div>

          <div className="flex-1 divide-y divide-zinc-800/50 overflow-y-auto">
            {loading ? (
              <p className="p-4 font-mono text-xs text-zinc-500">Loading…</p>
            ) : documents.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-400">
                  <Icon name="document" className="h-5 w-5" />
                </div>
                <h2 className="text-sm font-semibold text-zinc-100">No documents ingested yet</h2>
                <p className="max-w-sm text-xs leading-relaxed text-zinc-400">
                  Drop a PDF, spec or set of notes above and CIPHER can answer questions from it,
                  with citations that name the page.
                </p>
              </div>
            ) : (
              documents.map((document) => {
                const isSelected = document.id === selectedId;
                return (
                  <article
                    key={document.id}
                    onClick={() => setSelectedId(document.id)}
                    className={[
                      "grid cursor-pointer grid-cols-12 items-center px-3.5 py-2.5 transition",
                      isSelected
                        ? "border-l-2 border-zinc-300 bg-zinc-800/40 text-zinc-200 hover:bg-zinc-800/60"
                        : "border-l-2 border-transparent text-zinc-300 hover:bg-zinc-800/30",
                    ].join(" ")}
                  >
                    <div className="col-span-5 flex min-w-0 items-center gap-2.5">
                      <span
                        className={[
                          "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px]",
                          document.status === "failed"
                            ? "border-red-900/50 bg-red-950/40 text-red-400"
                            : "border-zinc-800 bg-zinc-900 text-zinc-400",
                        ].join(" ")}
                      >
                        {formatKind(document)}
                      </span>
                      <div className="min-w-0 truncate">
                        <p
                          className={[
                            "truncate font-mono text-xs",
                            document.status === "failed"
                              ? "text-zinc-300 line-through decoration-zinc-600"
                              : "text-zinc-200",
                          ].join(" ")}
                        >
                          {document.filename}
                        </p>
                        <p className="font-mono text-[10px] text-zinc-500">
                          {relativeTime(document.created_at)}
                        </p>
                      </div>
                    </div>

                    <div className="col-span-2 text-right font-mono text-zinc-400">
                      <div className="text-xs">{fileSize(document.size_bytes)}</div>
                      <div className="text-[10px] text-zinc-500">
                        {document.page_count ? `${document.page_count} pages` : "—"}
                      </div>
                    </div>

                    <div className="col-span-2 text-right font-mono text-zinc-300">
                      <div className="text-xs">
                        {document.status === "ready" ? document.chunk_count : "—"}
                      </div>
                      <div className="text-[10px] text-zinc-500">
                        {document.status === "ready"
                          ? "searchable"
                          : document.status === "pending"
                            ? "embedding…"
                            : "aborted"}
                      </div>
                    </div>

                    <div className="col-span-3 flex items-center justify-between gap-2 pl-4">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[11px] ${STATUS_STYLE[document.status]}`}
                      >
                        <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[document.status]}`} />
                        <span className="truncate">{STATUS_LABEL[document.status]}</span>
                      </span>
                      <button
                        type="button"
                        disabled={busyId === document.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDelete(document.id);
                        }}
                        title="Delete document"
                        aria-label={`Delete ${document.filename}`}
                        className="rounded p-1 text-zinc-500 transition hover:bg-red-950/40 hover:text-red-400 disabled:opacity-40"
                      >
                        <Icon name="trash" className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    {document.error && (
                      <p className="col-span-12 mt-2 rounded border border-red-900/50 bg-red-950/30 px-2 py-1 font-mono text-[11px] text-red-300">
                        {document.error}
                      </p>
                    )}
                  </article>
                );
              })
            )}
          </div>
        </main>

        {/* Right: the passage inspector. */}
        <aside aria-label="Passages" className="flex w-[42%] min-w-0 flex-col bg-zinc-925/60">
          {!selected ? (
            <div className="flex h-full items-center justify-center px-6 text-center font-mono text-xs text-zinc-500">
              Select a document to read the passages CIPHER can quote from it.
            </div>
          ) : (
            <>
              <div className="shrink-0 space-y-2.5 border-b border-zinc-800 bg-zinc-950/60 p-3.5">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate font-mono text-xs font-semibold text-zinc-100">
                      {selected.filename}
                    </h2>
                    <span className="shrink-0 rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">
                      {formatKind(selected)}
                    </span>
                  </div>
                  <p className="mt-0.5 font-mono text-[11px] text-zinc-500">
                    {selected.status === "ready"
                      ? `${selected.chunk_count} ${selected.chunk_count === 1 ? "passage" : "passages"} extracted`
                      : selected.status === "pending"
                        ? "Still ingesting — passages appear as they are embedded."
                        : "Ingestion failed, so there is nothing to quote."}
                  </p>
                </div>

                {selected.status === "ready" && (
                  <div className="relative">
                    <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5 text-zinc-500">
                      <Icon name="search" className="h-3.5 w-3.5" />
                    </span>
                    <input
                      type="search"
                      value={chunkFilter}
                      onChange={(e) => setChunkFilter(e.target.value)}
                      placeholder="Filter passages…"
                      className="w-full rounded border border-zinc-800 bg-zinc-950 py-1 pl-8 pr-3 font-mono text-xs text-zinc-200 outline-none placeholder:text-zinc-500 focus:border-zinc-700"
                    />
                  </div>
                )}
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto p-3.5">
                {selected.error && (
                  <p className="rounded border border-red-900/50 bg-red-950/30 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-red-300">
                    {selected.error}
                  </p>
                )}

                {chunksLoading ? (
                  <p className="font-mono text-xs text-zinc-500">Loading passages…</p>
                ) : visibleChunks.length === 0 ? (
                  <p className="font-mono text-xs text-zinc-500">
                    {chunkFilter.trim()
                      ? `No passage contains “${chunkFilter.trim()}”.`
                      : "No passages stored for this document."}
                  </p>
                ) : (
                  visibleChunks.map((chunk) => (
                    <div
                      key={chunk.id}
                      className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-900/60 p-3 transition hover:border-zinc-700"
                    >
                      <div className="flex items-center justify-between font-mono text-[11px]">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-zinc-200">
                            Passage {chunk.chunk_index + 1}
                          </span>
                          {chunk.page_number != null && (
                            <>
                              <span className="text-zinc-600">·</span>
                              <span className="text-zinc-400">page {chunk.page_number}</span>
                            </>
                          )}
                        </div>
                        {chunk.embedded ? (
                          <span className="rounded border border-emerald-800/60 bg-emerald-950/50 px-1.5 py-0.5 text-[10px] text-emerald-400">
                            searchable
                          </span>
                        ) : (
                          <span
                            className="rounded border border-amber-800/60 bg-amber-950/50 px-1.5 py-0.5 text-[10px] text-amber-400"
                            title="Stored, but never embedded — CIPHER cannot retrieve or quote this passage."
                          >
                            no embedding
                          </span>
                        )}
                      </div>
                      <p className="rounded border border-zinc-800/60 bg-zinc-950/60 p-2.5 font-mono text-[11.5px] leading-relaxed text-zinc-300">
                        {chunk.content}
                      </p>
                    </div>
                  ))
                )}
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
