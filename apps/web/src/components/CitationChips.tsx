import Icon from "@/components/Icon";
import type { Citation } from "@/lib/api";

// Presentational only, matching RecalledMemoryChips -- no "use client".
//
// Document citations name the page, because "handbook.pdf · page 4" is a
// citation the reader can act on and "handbook.pdf" is barely better than
// none on a 200-page file. The page number is trustworthy for one structural
// reason: chunks never span pages. Web citations link out, so the claim can
// be checked against the source rather than taken on trust.
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
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 font-mono text-[11px] uppercase tracking-tight text-zinc-500">
        Citations:
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
              className="inline-flex max-w-[20rem] items-center gap-1 rounded border border-zinc-800/80 bg-zinc-900/60 px-2 py-0.5 font-mono text-[12px] text-zinc-400 transition-colors hover:bg-zinc-850 hover:text-zinc-200"
            >
              <Icon name="external" className="h-3 w-3 shrink-0 text-zinc-500" strokeWidth={2} />
              <span className="truncate">{label}</span>
            </a>
          );
        }

        // The page number is deliberately OUTSIDE the truncating span. Long
        // filenames are common and the page is the part that makes the
        // citation actionable -- truncating "handbook-v2-final · page 4" down
        // to "handbook-v2-fin…" throws away the only bit that distinguishes
        // one chip from the next, which is exactly what happened before.
        return (
          <span
            key={`d-${index}`}
            title={citation.content ?? undefined}
            className="inline-flex max-w-[20rem] items-center gap-1 rounded border border-zinc-800/80 bg-zinc-900/60 px-2 py-0.5 font-mono text-[12px] text-zinc-400"
          >
            <Icon name="document" className="h-3 w-3 shrink-0 text-zinc-500" strokeWidth={2} />
            <span className="truncate">{citation.filename}</span>
            {citation.page_number != null && (
              <span className="shrink-0 whitespace-nowrap text-zinc-500">
                · page {citation.page_number}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
