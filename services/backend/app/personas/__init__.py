"""Public surface of the persona system -- the only module app/api/chat.py
and app/api/personas.py should import from.
"""
from collections.abc import Sequence

from app.personas.base import (
    DEFAULT_PERSONA,
    SHARED_CAPABILITY_NOTE,
    Persona,
    PersonaConfig,
    build_system_prompt,
)
from app.personas.registry import PERSONAS, get_persona
from app.personas.safety import FilterResult

__all__ = [
    "DEFAULT_PERSONA",
    "SHARED_CAPABILITY_NOTE",
    "Persona",
    "PersonaConfig",
    "PERSONAS",
    "FilterResult",
    "build_system_prompt",
    "get_persona",
    "system_prompt_for",
]


def system_prompt_for(
    persona: str | Persona | None,
    *,
    mixed_history: bool = False,
    recalled_memories: Sequence[str] = (),
) -> str:
    """Convenience wrapper: resolve a persona id and assemble its system prompt in one call."""
    return build_system_prompt(
        get_persona(persona), mixed_history=mixed_history, recalled_memories=recalled_memories
    )
