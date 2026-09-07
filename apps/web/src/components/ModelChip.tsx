"use client";

// Phase 4: which brain is thinking, always on screen.
//
// The chip exists because the swap is invisible otherwise. You say "switch
// to Qwen", you get an answer, and nothing on screen tells you whether the
// swap happened -- which is how you end up comparing two models that were
// the same model. Pinned state is styled differently from default routing
// on purpose: they behave differently (a pinned model never falls back), and
// the pinned chip carries its own unpin control so getting back to Auto is
// one click rather than a trip through the menu.

import { useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon";
import type { ActiveModel, ModelInfo } from "@/lib/api";

export default function ModelChip({
  active,
  available,
  busy,
  onSelect,
  onReset,
}: {
  active: ActiveModel | null;
  available: ModelInfo[];
  busy: boolean;
  onSelect: (id: string) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!active) return null;

  return (
    <div className="relative" ref={containerRef}>
      {/* The unpin control sits beside the menu trigger rather than inside
          it: a button inside a button is invalid, and clicking "unpin" must
          not also open the list you were trying to leave. */}
      <div
        className={[
          "flex items-center gap-1.5 rounded border py-1 pl-2 font-mono text-[12px] transition-colors",
          active.pinned ? "pr-1" : "pr-2",
          "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700",
        ].join(" ")}
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          disabled={busy}
          aria-haspopup="listbox"
          aria-expanded={open}
          title={
            active.pinned
              ? `Pinned to ${active.display_name}. It will not fall back to another model.`
              : `Default routing: ${active.default_id}, falling back automatically if it fails.`
          }
          className="flex items-center gap-1.5 disabled:opacity-40"
        >
          <span className="text-[10px] uppercase tracking-tight text-zinc-500">Model</span>
          <span className={active.pinned ? "font-medium text-zinc-100" : "text-zinc-400"}>
            {busy ? "…" : active.pinned ? active.display_name : "Auto"}
          </span>
        </button>

        {active.pinned && (
          <button
            type="button"
            onClick={onReset}
            disabled={busy}
            title="Unpin model (switch back to Auto)"
            aria-label="Unpin model"
            className="ml-0.5 rounded p-0.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40"
          >
            <Icon name="close" className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        )}
      </div>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-30 mt-1 w-72 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-925 shadow-2xl"
        >
          <button
            type="button"
            role="option"
            aria-selected={!active.pinned}
            onClick={() => {
              onReset();
              setOpen(false);
            }}
            className={[
              "block w-full px-3 py-2 text-left text-xs transition-colors hover:bg-zinc-850",
              !active.pinned ? "font-semibold text-zinc-100" : "text-zinc-300",
            ].join(" ")}
          >
            Auto
            <span className="mt-0.5 block font-mono text-[10px] font-normal text-zinc-500">
              {active.default_id}, falls back automatically
            </span>
          </button>

          <div className="border-t border-zinc-800" />

          <div className="max-h-72 overflow-y-auto">
            {available.map((model) => {
              const selected = active.pinned && active.id === model.id;
              return (
                <button
                  key={model.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => {
                    onSelect(model.id);
                    setOpen(false);
                  }}
                  className={[
                    "block w-full px-3 py-2 text-left text-xs transition-colors hover:bg-zinc-850",
                    selected ? "font-semibold text-zinc-100" : "text-zinc-300",
                  ].join(" ")}
                >
                  {model.display_name}
                  <span className="mt-0.5 block font-mono text-[10px] font-normal text-zinc-500">
                    {model.note || model.id}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="border-t border-zinc-800 bg-zinc-950/60 px-3 py-2 text-[10px] leading-relaxed text-zinc-500">
            A pinned model never falls back — if it fails, you get an error, not a
            different model&rsquo;s answer.
          </div>
        </div>
      )}
    </div>
  );
}
