"use client";

// Phase 4: which brain is thinking, always on screen.
//
// The chip exists because the swap is invisible otherwise. You say "switch
// to Qwen", you get an answer, and nothing on screen tells you whether the
// swap happened -- which is how you end up comparing two models that were
// the same model. Pinned state is styled differently from default routing
// on purpose: they behave differently (a pinned model never falls back).

import { useEffect, useRef, useState } from "react";
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

  const label = active.pinned ? active.display_name : "Auto";

  return (
    <div className="relative" ref={containerRef}>
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
        className={[
          "rounded-full border px-2.5 py-1 text-[11px] font-medium transition disabled:opacity-40",
          active.pinned
            ? "border-sky-500 text-sky-600 dark:text-sky-400"
            : "border-zinc-300 text-zinc-500 dark:border-zinc-700 dark:text-zinc-400",
        ].join(" ")}
      >
        {busy ? "…" : label}
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 z-20 mt-1 w-64 overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
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
              "block w-full px-3 py-2 text-left text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800",
              !active.pinned ? "font-semibold text-zinc-900 dark:text-zinc-100" : "text-zinc-600 dark:text-zinc-300",
            ].join(" ")}
          >
            Auto
            <span className="block text-[10px] font-normal text-zinc-400">
              {active.default_id}, falls back automatically
            </span>
          </button>

          <div className="border-t border-zinc-200 dark:border-zinc-800" />

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
                  "block w-full px-3 py-2 text-left text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800",
                  selected ? "font-semibold text-zinc-900 dark:text-zinc-100" : "text-zinc-600 dark:text-zinc-300",
                ].join(" ")}
              >
                {model.display_name}
                <span className="block text-[10px] font-normal text-zinc-400">
                  {model.note || model.id}
                </span>
              </button>
            );
          })}

          <div className="border-t border-zinc-200 px-3 py-2 text-[10px] text-zinc-400 dark:border-zinc-800">
            A pinned model never falls back — if it fails, you get an error, not a
            different model’s answer.
          </div>
        </div>
      )}
    </div>
  );
}
