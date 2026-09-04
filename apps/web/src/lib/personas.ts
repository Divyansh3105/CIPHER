// Persona types and label helpers shared by the switcher, bubbles, and sidebar.

export type Persona = "jarvis" | "friday" | "ultron";

export interface PersonaInfo {
  id: Persona;
  display_name: string;
  tagline: string;
}

export const DEFAULT_PERSONA: Persona = "jarvis";

// Used before GET /personas resolves, and as a fallback if it fails -- the
// UI should never be stuck showing a raw id like "jarvis" as a label.
export const FALLBACK_PERSONAS: PersonaInfo[] = [
  { id: "jarvis", display_name: "JARVIS", tagline: "Formal and precise." },
  { id: "friday", display_name: "FRIDAY", tagline: "Warm and conversational." },
  { id: "ultron", display_name: "ULTRON", tagline: "Blunt and analytical." },
];

export function personaLabel(personas: PersonaInfo[], id: string | null | undefined): string {
  if (!id) return "Unknown";
  const found = personas.find((p) => p.id === id) ?? FALLBACK_PERSONAS.find((p) => p.id === id);
  return found?.display_name ?? id.toUpperCase();
}
