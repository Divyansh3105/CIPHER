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
import Icon from "@/components/Icon";
import { MEMORY_TYPE_CLASS, MEMORY_TYPE_LABEL } from "@/components/MemoryCard";
import type { MemoryGraph, MemoryGraphNode } from "@/lib/api";

// three.js touches `window` at module scope, so this can only load in the
// browser. Without ssr:false the page 500s during server rendering.
//
// The wrapper exists because `next/dynamic` does NOT forward refs: writing
// `<ForceGraph3D ref={graphRef} />` against the bare dynamic import leaves
// graphRef.current undefined forever, and every call through it
// (`graphRef.current?.zoomToFit(...)`) silently does nothing -- no error, no
// warning, just a camera that never moves. That took the initial framing,
// "Fit view" and the deep-link flight from a Recalled chip with it. Passing
// the ref as an ordinary prop and attaching it inside the loaded module is
// what makes it actually arrive.
const ForceGraph3D = dynamic(
  async () => {
    const { default: Inner } = await import("react-force-graph-3d");
    type InnerProps = React.ComponentProps<typeof Inner>;
    function WithGraphRef({ graphRef, ...props }: InnerProps & { graphRef?: unknown }) {
      // The library types its ref against its own node/link generics, which
      // do not line up with the narrowed `ForceGraphMethods` handle this
      // component keeps (only .zoomToFit and .cameraPosition are ever
      // called). The cast is confined to this one line rather than loosening
      // the handle's type everywhere it is used.
      return <Inner ref={graphRef as InnerProps["ref"]} {...props} />;
    }
    return WithGraphRef;
  },
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center text-xs text-zinc-500">
        Loading the galaxy…
      </div>
    ),
  }
);

// Shared with the list view's type badges (MEMORY_TYPE_CLASS in
// MemoryCard). A memory has to be the same colour in the graph as it is in
// the list, or the legend below is describing a different picture.
const TYPE_COLOURS: Record<string, string> = {
  long_term: "#6366f1",
  semantic: "#34d399",
  episodic: "#fbbf24",
  short_term: "#38bdf8",
};
const PENDING_COLOUR = "#52525b";

const LEGEND: { type: string; label: string }[] = [
  { type: "long_term", label: "Long term" },
  { type: "semantic", label: "Semantic" },
  { type: "episodic", label: "Episodic" },
  { type: "short_term", label: "Short term" },
];

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
    <div className="relative h-full w-full bg-zinc-950">
      <div ref={containerRef} className="absolute inset-0 cursor-grab active:cursor-grabbing">
        {size.width > 0 && data.nodes.length > 0 && (
          <ForceGraph3D
            graphRef={graphRef}
            width={size.width}
            height={size.height}
            graphData={data}
            backgroundColor="#0c0c0e"
            showNavInfo={false}
            nodeId="id"
            nodeLabel={(node) => {
              const n = node as GraphNode;
              const text = n.content.length > 110 ? `${n.content.slice(0, 110)}…` : n.content;
              return `<div style="max-width:260px;font:12px system-ui;padding:6px 8px;background:#18181b;color:#e4e4e7;border:1px solid #3f3f46;border-radius:6px">${escapeHtml(text)}</div>`;
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
            linkColor={() => "#3f3f46"}
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
        )}
      </div>

      {graph && graph.nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-zinc-500">
          Nothing stored yet. Add a memory, or say &ldquo;remember that…&rdquo; in chat.
        </div>
      )}

      {loading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/40 font-mono text-xs text-zinc-300">
          Rebuilding…
        </div>
      )}

      {/* Graph controls, floating top-left. */}
      <div className="absolute left-4 top-4 z-10 flex w-64 select-none flex-col gap-2.5 rounded-lg border border-zinc-800 bg-zinc-900/85 p-3.5 text-xs shadow-xl backdrop-blur-md">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-indigo-500" />
            <span className="font-mono text-[11px] font-semibold tracking-tight text-zinc-200">
              K-NN VECTOR GRAPH
            </span>
          </div>
          <button
            type="button"
            onClick={() => graphRef.current?.zoomToFit(700, 70)}
            title="Frame every memory"
            className="rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400 transition hover:text-zinc-100"
          >
            Fit view
          </button>
        </div>

        <div className="space-y-2 pt-1 font-mono text-[11px]">
          <div className="flex items-center justify-between text-zinc-400">
            <span>Links per memory</span>
            <span className="tabular-nums text-zinc-300">{neighbours}</span>
          </div>
          <input
            type="range"
            min={1}
            max={6}
            step={1}
            value={neighbours}
            onChange={(e) => applySettings({ neighbours: Number(e.target.value) })}
            className="w-full accent-zinc-400"
            aria-label="Links per memory"
          />

          <div className="flex items-center justify-between text-zinc-400">
            <span>Strictness</span>
            <span className="tabular-nums text-zinc-300">
              {(floorOverride ?? graph?.min_similarity ?? 0).toFixed(2)}
            </span>
          </div>
          <input
            type="range"
            min={0.3}
            max={0.95}
            step={0.01}
            value={floorOverride ?? graph?.min_similarity ?? 0.6}
            onChange={(e) => applySettings({ minSimilarity: Number(e.target.value) })}
            className="w-full accent-zinc-400"
            aria-label="Strictness"
          />
        </div>

        <div className="flex items-center justify-between border-t border-zinc-800/80 pt-2 font-mono text-[10px] text-zinc-500">
          <span>
            {graph
              ? `${graph.nodes.length}${graph.truncated ? ` of ${graph.total}` : ""} nodes · ${graph.links.length} links`
              : "—"}
          </span>
          {floorOverride !== null && (
            <button
              type="button"
              onClick={() => applySettings({ minSimilarity: null })}
              className="rounded border border-zinc-700 px-1.5 py-0.5 text-zinc-400 transition hover:text-zinc-100"
            >
              Back to auto
            </button>
          )}
        </div>
      </div>

      {/* Legend, floating bottom-left. The edge caption says "nearest by
          similarity" rather than "related" on purpose: measured on real
          gemini-embedding-001 output, unrelated personal facts score 0.77
          while a genuinely related pair scores 0.81, so only the ranking
          carries signal and "related" would be a claim the data cannot
          support. */}
      <div className="pointer-events-none absolute bottom-4 left-4 z-10 max-w-sm space-y-1.5 rounded-lg border border-zinc-800 bg-zinc-900/85 p-3 font-mono text-[11px] text-zinc-400 shadow-xl backdrop-blur-md">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {LEGEND.map((entry) => (
            <span key={entry.type} className="flex items-center gap-1.5">
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: TYPE_COLOURS[entry.type] }}
              />
              {entry.label}
            </span>
          ))}
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: PENDING_COLOUR }} />
            No embedding
          </span>
        </div>
        <div className="border-t border-zinc-800 pt-1 text-[10px] text-zinc-500">
          Edges: nearest by similarity
          {graph
            ? graph.adaptive
              ? ` (auto floor ${graph.min_similarity.toFixed(2)}, from this store's own spread)`
              : ` (floor ${graph.min_similarity.toFixed(2)}, overridden)`
            : ""}
          . Not necessarily memories you would call related.
        </div>
        <div className="text-[10px] text-zinc-500">
          Grey nodes have no embedding yet and can never be recalled.
        </div>
      </div>

      {/* Inspector, floating right. */}
      {selected && (
        <aside className="absolute right-4 top-4 z-10 flex max-h-[calc(100%-2rem)] w-[340px] flex-col gap-3 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/90 p-4 text-xs shadow-2xl backdrop-blur-md">
          <div className="flex items-start justify-between gap-2 border-b border-zinc-800 pb-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium ${MEMORY_TYPE_CLASS[selected.memory_type]}`}
                >
                  {MEMORY_TYPE_LABEL[selected.memory_type]}
                </span>
                <span className="font-mono text-[11px] text-zinc-400">
                  MEM-{selected.id.slice(0, 8).toUpperCase()}
                </span>
              </div>
              <p className="mt-1 font-mono text-[11px] text-zinc-500">source: {selected.source}</p>
            </div>
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="rounded p-1 text-zinc-500 transition hover:text-zinc-300"
              aria-label="Close inspector"
            >
              <Icon name="close" className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
          </div>

          <div className="rounded border border-zinc-850 bg-zinc-950/80 p-2.5 font-sans text-[12px] leading-relaxed text-zinc-200">
            {selected.content}
          </div>

          <dl className="grid grid-cols-2 gap-2 rounded border border-zinc-850 bg-zinc-950/40 p-2 font-mono text-[11px]">
            <div>
              <dt className="block text-[10px] text-zinc-500">STORED</dt>
              <dd className="text-zinc-300">{new Date(selected.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt className="block text-[10px] text-zinc-500">LAST RECALLED</dt>
              <dd className="text-zinc-300">
                {selected.last_recalled_at
                  ? new Date(selected.last_recalled_at).toLocaleString()
                  : "never"}
              </dd>
            </div>
            <div className="col-span-2 flex justify-between border-t border-zinc-850 pt-1">
              <dt className="text-zinc-500">NEAREST</dt>
              <dd className="text-zinc-400">
                {selected.degree === 0 ? "nothing close enough" : `${selected.degree} memories`}
              </dd>
            </div>
          </dl>

          {selected.embedding_pending && (
            <p className="rounded border border-amber-800 bg-amber-950/60 px-2 py-1.5 text-[11px] leading-relaxed text-amber-300">
              Stored, but never embedded — so it cannot be searched or linked, and CIPHER will
              not recall it. Edit and re-save it to try again.
            </p>
          )}
        </aside>
      )}
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
