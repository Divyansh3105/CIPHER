"use client";

// Phase 4: the memory store as a 3D force-directed graph.
//
// This exists because the recall data was already there and invisible. Every
// assistant reply persists a snapshot of the memories it used
// (messages.recalled_memories), and until now that surfaced as a small text
// chip. Same data, drawn.
//
// The honest framing matters here and is repeated in the UI copy: a link
// means two memories are among each other's NEAREST by embedding similarity,
// not that a person would call them related. Measured against real
// gemini-embedding-001 output, unrelated personal facts score 0.77 while a
// genuinely related pair scores 0.81 -- the absolute numbers barely separate,
// so only the ranking carries signal. Labelling these "related memories"
// would be a claim the data does not support.

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ForceGraphMethods } from "react-force-graph-3d";
import type { MemoryGraph, MemoryGraphNode } from "@/lib/api";

// three.js touches `window` at module scope, so this can only load in the
// browser. Without ssr:false the page 500s during server rendering.
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-xs text-zinc-500">
      Loading the galaxy…
    </div>
  ),
});

const TYPE_COLOURS: Record<string, string> = {
  long_term: "#38bdf8",
  semantic: "#a78bfa",
  episodic: "#fbbf24",
  short_term: "#34d399",
};
const PENDING_COLOUR = "#71717a";

function baseColour(node: MemoryGraphNode): string {
  return node.embedding_pending ? PENDING_COLOUR : TYPE_COLOURS[node.memory_type] ?? "#94a3b8";
}

type GraphNode = MemoryGraphNode & {
  degree: number;
  /**
   * Render colour, stored ON the node rather than returned by a `nodeColor`
   * closure.
   *
   * The closure form is the obvious way to write this and it does not work:
   * the library builds each node's sphere material once, so a closure that
   * starts returning a different colour later is never consulted again.
   * A field name is read per node and stays correct.
   */
  colour: string;
  // react-force-graph writes simulation coordinates onto the node objects.
  x?: number;
  y?: number;
  z?: number;
};

export default function MemoryGalaxy({
  graph,
  loading,
  onReload,
  focusId,
}: {
  graph: MemoryGraph | null;
  loading: boolean;
  onReload: (options: { neighbours: number; minSimilarity: number | null }) => void;
  /**
   * A memory to fly the camera to once the layout settles -- set when
   * arriving from a "Recalled" chip in chat, so a reply can show you exactly
   * which memory it drew on.
   */
  focusId?: string | null;
}) {
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [neighbours, setNeighbours] = useState(3);
  const [floorOverride, setFloorOverride] = useState<number | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const observerRef = useRef<ResizeObserver | null>(null);
  // The library's own handle type; only .cameraPosition() is used.
  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);
  // Held until the force simulation settles: flying at a node while the
  // layout is still expanding lands the camera where the node used to be.
  const pendingFocusRef = useRef<string | null>(null);

  // The library needs explicit pixel dimensions; it will not fill a flex
  // parent on its own.
  //
  // A callback ref rather than an effect, and it measures the element
  // directly before subscribing. Doing it purely through ResizeObserver was
  // the original implementation and it rendered nothing at all in a browser
  // where the observer exists but never delivers an initial entry: `size`
  // stayed 0x0, the render was gated on it, and the user got an empty black
  // box with no error anywhere. getBoundingClientRect always answers; the
  // observer is now only how later resizes arrive.
  const containerRef = useCallback((node: HTMLDivElement | null) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!node) return;

    const rect = node.getBoundingClientRect();
    setSize({ width: rect.width, height: rect.height });

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(node);
    observerRef.current = observer;
  }, []);

  useEffect(() => () => observerRef.current?.disconnect(), []);

  // Degree drives node size, so the memories the assistant keeps connecting
  // things through read as hubs. Recomputed here rather than server-side
  // because it depends on the links actually drawn at the current settings.
  const data = useMemo(() => {
    if (!graph) return { nodes: [], links: [] };
    const degree = new Map<string, number>();
    for (const link of graph.links) {
      degree.set(link.source, (degree.get(link.source) ?? 0) + 1);
      degree.set(link.target, (degree.get(link.target) ?? 0) + 1);
    }
    return {
      nodes: graph.nodes.map((n) => ({ ...n, degree: degree.get(n.id) ?? 0, colour: baseColour(n) })),
      // Cloned because the force simulation mutates link objects in place,
      // replacing the string endpoints with node references -- which would
      // corrupt the props we were handed.
      links: graph.links.map((l) => ({ ...l })),
    };
  }, [graph]);

  const focusNode = useCallback((node: GraphNode) => {
    setSelected(node);
    const controls = graphRef.current;
    if (!controls || node.x == null || node.y == null || node.z == null) return;
    // Fly to a point offset along the vector from the origin through the
    // node, so the camera ends up looking at it rather than through it.
    const distance = 90;
    const ratio = 1 + distance / Math.hypot(node.x, node.y, node.z || 1);
    controls.cameraPosition(
      { x: node.x * ratio, y: node.y * ratio, z: (node.z || 1) * ratio },
      // An explicit Coords, not the node itself: the node's x/y/z are
      // optional in the library's type even though they are narrowed above.
      { x: node.x, y: node.y, z: node.z },
      1200
    );
  }, []);

  // Record the request; the flight happens in onEngineStop below. Writing a
  // ref here rather than calling focusNode keeps state changes out of the
  // effect body, and waits for coordinates that are actually final.
  useEffect(() => {
    pendingFocusRef.current = focusId ?? null;
  }, [focusId]);

  // Runs once the force simulation has had time to settle, which is the
  // first moment node coordinates are final and therefore the first moment
  // either of these is meaningful.
  const onLayoutSettled = useCallback(() => {
    const wanted = pendingFocusRef.current;
    if (wanted) {
      const node = data.nodes.find((n) => n.id === wanted);
      if (node) {
        pendingFocusRef.current = null;
        focusNode(node as GraphNode);
        return;
      }
      // Only give up once there is actually a graph to have missed it in.
      // The simulation settles almost instantly on the initial empty render,
      // before the fetch returns -- clearing the request there was a real
      // bug: the deep link resolved to nothing every time, because by the
      // time the nodes arrived the request had already been discarded.
      if (data.nodes.length > 0) pendingFocusRef.current = null;
      else return;
    }
    // Otherwise frame the whole store. Without this the default camera sits
    // far enough back that a dozen memories read as a smudge.
    graphRef.current?.zoomToFit(700, 70);
  }, [data.nodes, focusNode]);

  // The library exposes `onEngineStop` for exactly this, and it never fires
  // in this version -- verified live by instrumenting the handler and
  // watching it stay silent while the graph rendered perfectly well. Both
  // the deep-link camera flight and the initial framing were dead code
  // hanging off it. Waiting out the settle explicitly is less elegant and
  // actually works; the state change happens inside the timeout callback,
  // not in the effect body.
  const SETTLE_MS = 1500;
  useEffect(() => {
    if (data.nodes.length === 0) return;
    const timer = setTimeout(onLayoutSettled, SETTLE_MS);
    return () => clearTimeout(timer);
  }, [data.nodes, onLayoutSettled]);

  function applySettings(next: { neighbours?: number; minSimilarity?: number | null }) {
    const n = next.neighbours ?? neighbours;
    const floor = next.minSimilarity === undefined ? floorOverride : next.minSimilarity;
    setNeighbours(n);
    setFloorOverride(floor);
    onReload({ neighbours: n, minSimilarity: floor });
  }


  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-zinc-500 dark:text-zinc-400">
        <label className="flex items-center gap-2">
          Links per memory
          <input
            type="range"
            min={1}
            max={6}
            step={1}
            value={neighbours}
            onChange={(e) => applySettings({ neighbours: Number(e.target.value) })}
            className="accent-sky-500"
          />
          <span className="tabular-nums">{neighbours}</span>
        </label>

        <label className="flex items-center gap-2">
          Strictness
          <input
            type="range"
            min={0.3}
            max={0.95}
            step={0.01}
            value={floorOverride ?? graph?.min_similarity ?? 0.6}
            onChange={(e) => applySettings({ minSimilarity: Number(e.target.value) })}
            className="accent-sky-500"
          />
          <span className="tabular-nums">{(floorOverride ?? graph?.min_similarity ?? 0).toFixed(2)}</span>
        </label>

        {floorOverride !== null && (
          <button
            type="button"
            onClick={() => applySettings({ minSimilarity: null })}
            className="rounded border border-zinc-300 px-2 py-0.5 hover:border-zinc-500 dark:border-zinc-700"
          >
            Back to auto
          </button>
        )}

        <span className="ml-auto">
          {graph
            ? `${graph.nodes.length}${graph.truncated ? ` of ${graph.total}` : ""} memories, ${graph.links.length} links`
            : ""}
        </span>
      </div>

      <div
        ref={containerRef}
        className="relative h-[520px] w-full overflow-hidden rounded-xl border border-zinc-200 bg-[#05060a] dark:border-zinc-800"
      >
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 text-xs text-zinc-300">
            Rebuilding…
          </div>
        )}

        {graph && graph.nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center px-6 text-center text-sm text-zinc-400">
            Nothing stored yet. Teach it something above, or say &ldquo;remember that…&rdquo; in chat.
          </div>
        ) : (
          size.width > 0 && (
            <ForceGraph3D
              ref={graphRef}
              width={size.width}
              height={size.height}
              graphData={data}
              backgroundColor="#05060a"
              showNavInfo={false}
              nodeId="id"
              nodeLabel={(node) => {
                const n = node as GraphNode;
                const text = n.content.length > 110 ? `${n.content.slice(0, 110)}…` : n.content;
                return `<div style="max-width:260px;font:12px system-ui;padding:6px 8px;background:#111;color:#eee;border-radius:6px">${escapeHtml(text)}</div>`;
              }}
              // A field name, not a function -- see GraphNode.colour.
              nodeColor="colour"
              nodeVal={(node) => 1 + (node as GraphNode).degree * 1.4}
              nodeOpacity={0.95}
              nodeResolution={12}
              // Selection is shown by flying the camera to the node and
              // opening the inspector, not by dimming everything else. Dimming
              // needed per-node recolouring on every click, which this library
              // only honours through in-place mutation of the node objects --
              // and those objects are memo-derived, which React 19's
              // immutability rule rightly forbids. Not worth the contortion:
              // losing the whole map to look at one node is worse anyway.
              linkColor={() => "#334155"}
              linkOpacity={0.5}
              linkWidth={(link) => {
                // Rescaled across the narrow real range (roughly 0.6-0.85) so
                // the difference between a strong and a weak link is visible
                // at all; using the raw value makes every link look identical.
                const similarity = (link as unknown as { similarity: number }).similarity;
                return 0.3 + Math.max(0, Math.min(1, (similarity - 0.6) / 0.3)) * 1.6;
              }}
              onNodeClick={(node) => focusNode(node as GraphNode)}
              onBackgroundClick={() => setSelected(null)}
              enableNodeDrag={false}
            />
          )
        )}

        {selected && (
          <aside className="absolute bottom-3 left-3 right-3 max-h-[45%] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-950/95 p-3 text-xs text-zinc-200 sm:right-auto sm:max-w-sm">
            <div className="mb-1 flex items-start justify-between gap-3">
              <span className="font-semibold uppercase tracking-wide text-zinc-400">
                {selected.memory_type.replace("_", " ")} · {selected.source}
              </span>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="text-zinc-500 hover:text-zinc-200"
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <p className="mb-2 leading-relaxed text-zinc-100">{selected.content}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-zinc-400">
              <dt>Stored</dt>
              <dd>{new Date(selected.created_at).toLocaleString()}</dd>
              <dt>Last recalled</dt>
              <dd>{selected.last_recalled_at ? new Date(selected.last_recalled_at).toLocaleString() : "never"}</dd>
              <dt>Nearest</dt>
              <dd>{selected.degree === 0 ? "nothing close enough" : `${selected.degree} memories`}</dd>
            </dl>
            {selected.embedding_pending && (
              <p className="mt-2 rounded border border-amber-800 bg-amber-950/60 px-2 py-1 text-[11px] text-amber-300">
                Stored, but never embedded — so it cannot be searched or linked, and CIPHER
                will not recall it. Edit and re-save it to try again.
              </p>
            )}
          </aside>
        )}
      </div>

      <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
        Lines connect each memory to its <strong>nearest</strong> few by embedding similarity —
        not necessarily to ones you would call related. On real data, unrelated personal facts
        score nearly as high as related ones, so only the ranking is meaningful and the
        strictness above is derived from this store&rsquo;s own spread
        {graph && !graph.adaptive ? " (currently overridden)" : graph ? ` (auto: ${graph.min_similarity.toFixed(2)})` : ""}.
        Grey nodes have no embedding yet and can never be recalled.
      </p>
    </div>
  );
}

function escapeHtml(value: string): string {
  // nodeLabel is injected as raw HTML by the library, and memory content is
  // whatever the user (or an LLM extraction pass) wrote.
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
