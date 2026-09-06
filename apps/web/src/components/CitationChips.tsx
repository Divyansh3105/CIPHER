import type { Citation } from "@/lib/api";

// Presentational only, matching RecalledMemoryChips -- no "use client".
//
// Document citations name the page, because "handbook.pdf, page 4" is a
// citation the reader can act on and "handbook.pdf" is barely better than
// none on a 200-page file. Web citations link out, so the claim can be
// checked against the source rather than taken on trust.
export default function CitationChips({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;

  // The same page can be cited by more than one passage. Two identical
  // chips look like two sources and inflate how well-supported an answer
  // appears, so they collapse to one.
  const seen = new Set<string>();
  const unique = citations.filter((citation) => {
    const key =
      citation.kind === "document"
        ? `d:${citation.document_id}:${citation.page_number ?? ""}`
        : `w:${citation.url ?? citation.title ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
        Sources
      </span>

      {unique.map((citation, index) => {
        if (citation.kind === "web") {
          const label = citation.title || citation.url || "web result";
          return (
            <a
              key={`w-${index}`}
              href={citation.url ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              title={citation.snippet ?? label}
              className="max-w-[16rem] truncate rounded-full border border-sky-300 bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700 hover:border-sky-500 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300 dark:hover:border-sky-500"
            >
              ↗ {label}
            </a>
          );
        }

        const where =
          citation.page_number != null
            ? `${citation.filename}, p.${citation.page_number}`
            : citation.filename;
        return (
          <span
            key={`d-${index}`}
            title={citation.content ?? undefined}
            className="max-w-[16rem] truncate rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
          >
            {where}
          </span>
        );
      })}
    </div>
  );
}
