"use client";

import type { Persona, PersonaInfo } from "@/lib/personas";

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
    <div className="flex gap-1 rounded-xl border border-zinc-300 p-1 dark:border-zinc-700">
      {personas.map((persona) => {
        const selected = persona.id === value;
        return (
          <button
            key={persona.id}
            type="button"
            title={persona.tagline}
            disabled={disabled}
            onClick={() => onChange(persona.id)}
            className={`rounded-lg px-3 py-1 text-xs font-semibold uppercase tracking-wide transition-colors disabled:opacity-40 ${
              selected
                ? "bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900"
                : "text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
            }`}
          >
            {persona.display_name}
          </button>
        );
      })}
    </div>
  );
}
