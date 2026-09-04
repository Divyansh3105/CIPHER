"""The persona lookup table. Adding a 4th persona = one new PersonaConfig
file + one line here -- no other code changes (docs/architecture.md, Section 3:
"Adding a 4th persona later = adding a new system-prompt config... No
architecture change needed.").
"""
from app.personas.base import DEFAULT_PERSONA, Persona, PersonaConfig
from app.personas.friday import FRIDAY
from app.personas.jarvis import JARVIS
from app.personas.ultron import ULTRON

PERSONAS: dict[Persona, PersonaConfig] = {
    Persona.JARVIS: JARVIS,
    Persona.FRIDAY: FRIDAY,
    Persona.ULTRON: ULTRON,
}


def get_persona(value: str | Persona | None) -> PersonaConfig:
    """Resolve a persona id to its config. Falls back to the default rather
    than raising on an unknown or missing value -- defence in depth behind
    Pydantic's own enum validation at the API boundary, and a safe default
    for old rows / unexpected input.
    """
    if value is None:
        return PERSONAS[DEFAULT_PERSONA]
    try:
        persona = Persona(value)
    except ValueError:
        return PERSONAS[DEFAULT_PERSONA]
    return PERSONAS[persona]
