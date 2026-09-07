"use client";

import type { Persona, PersonaInfo } from "@/lib/personas";

// Segmented control. Each persona keeps its accent on its own dot and on
// its selected/hover text, and nowhere else -- the control's chrome stays
// zinc so the coloured dot reads as "this voice", not as decoration.
const SELECTED_CLASS: Record<Persona, string> = {
  jarvis: "bg-zinc-800 text-indigo-300 border border-zinc-700/60 shadow-sm",
  friday: "bg-zinc-800 text-amber-300 border border-zinc-700/60 shadow-sm",
  ultron: "bg-zinc-800 text-red-300 border border-zinc-700/60 shadow-sm",
};

const IDLE_CLASS: Record<Persona, string> = {
  jarvis: "text-zinc-400 hover:bg-zinc-850/60 hover:text-indigo-400",
  friday: "text-zinc-400 hover:bg-zinc-850/60 hover:text-amber-400",
  ultron: "text-zinc-400 hover:bg-zinc-850/60 hover:text-red-400",
};

const DOT_SELECTED: Record<Persona, string> = {
  jarvis: "bg-indigo-500 ring-2 ring-indigo-500/30",
  friday: "bg-amber-500 ring-2 ring-amber-500/30",
  ultron: "bg-red-500 ring-2 ring-red-500/30",
};

const DOT_IDLE: Record<Persona, string> = {
  jarvis: "bg-indigo-500/70",
  friday: "bg-amber-500/70",
  ultron: "bg-red-500/70",
};

export default function PersonaSwitcher({
  personas,
  value,
  onChange,
  disabled,
}: {
  personas: PersonaInfo[];
  value: Persona;
  onChange: (persona: Persona) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="tablist"
      aria-label="Persona"
      className="inline-flex rounded border border-zinc-800 bg-zinc-900 p-0.5"
    >
      {personas.map((persona) => {
        const selected = persona.id === value;
        return (
          <button
            key={persona.id}
            type="button"
            role="tab"
            aria-selected={selected}
            title={persona.tagline}
            disabled={disabled}
            onClick={() => onChange(persona.id)}
            className={[
              "flex items-center gap-1.5 rounded px-3 py-1 text-[12px] font-medium transition-all disabled:opacity-40",
              selected ? SELECTED_CLASS[persona.id] : IDLE_CLASS[persona.id],
            ].join(" ")}
          >
            <span
              className={[
                "h-1.5 w-1.5 rounded-full",
                selected ? DOT_SELECTED[persona.id] : DOT_IDLE[persona.id],
              ].join(" ")}
            />
            <span>{persona.display_name}</span>
          </button>
        );
      })}
    </div>
  );
}
