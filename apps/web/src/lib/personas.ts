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

// --- Phase 4: voice ----------------------------------------------------

/**
 * How each persona should sound when spoken aloud.
 *
 * `voiceHints` are matched as case-insensitive substrings against
 * `speechSynthesis.getVoices()`, in order, first hit wins. They are hints,
 * not requirements: the installed voice list differs per OS, browser and
 * even per user, so every field degrades to the system default rather than
 * failing. `lang` is the fallback filter when no hint matches.
 */
export interface VoiceProfile {
  voiceHints: string[];
  lang: string;
  rate: number;
  pitch: number;
}

export const VOICE_PROFILES: Record<Persona, VoiceProfile> = {
  // Formal and clipped: a shade faster than default, flat pitch.
  jarvis: {
    voiceHints: ["Google UK English Male", "Daniel", "Arthur", "Ryan", "UK English Male"],
    lang: "en-GB",
    rate: 1.05,
    pitch: 0.95,
  },
  // Warm and conversational: default speed, slightly brighter.
  friday: {
    voiceHints: ["Google UK English Female", "Moira", "Samantha", "Sonia", "UK English Female"],
    lang: "en-IE",
    rate: 1.0,
    pitch: 1.1,
  },
  // Measured and low. Deliberately the slowest of the three -- ULTRON's
  // lines land worse when rushed.
  ultron: {
    voiceHints: ["Google US English", "Alex", "Guy", "Microsoft David"],
    lang: "en-US",
    rate: 0.92,
    pitch: 0.8,
  },
};

export function pickVoice(
  voices: SpeechSynthesisVoice[],
  profile: VoiceProfile
): SpeechSynthesisVoice | null {
  for (const hint of profile.voiceHints) {
    const match = voices.find((v) => v.name.toLowerCase().includes(hint.toLowerCase()));
    if (match) return match;
  }
  const exactLang = voices.find((v) => v.lang.replace("_", "-") === profile.lang);
  if (exactLang) return exactLang;
  const sameLanguage = voices.find((v) => v.lang.toLowerCase().startsWith(profile.lang.slice(0, 2)));
  return sameLanguage ?? null;
}
