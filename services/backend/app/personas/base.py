"""Persona definitions and system-prompt assembly.

A persona is a *style*, never a permission tier (docs/architecture.md,
Section 3). Every persona gets identical capabilities and tool access; ULTRON
additionally carries an application-layer output filter (app/personas/safety.py),
which adds checking on top of the shared capability set -- it never subtracts
from it.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.personas.safety import FilterResult


class Persona(StrEnum):
    JARVIS = "jarvis"
    FRIDAY = "friday"
    ULTRON = "ultron"


DEFAULT_PERSONA = Persona.JARVIS

# Factored out so it is written exactly once instead of once per persona file.
# Update this paragraph when memory/tools land in later phases.
SHARED_CAPABILITY_NOTE = """\
You are currently running in an early build of this assistant: you have no
long-term memory yet and can only see the current conversation. Do not claim
to remember anything from before it. You have no tools yet (web search,
documents, calendar) -- if a request needs one, say so plainly rather than
guessing at an answer you can't verify."""

# Appended to the system prompt only when the conversation actually contains
# turns from a *different* persona (see app/api/chat.py). Without this, after
# a mid-conversation switch the model can drift toward continuing the prior
# persona's voice, since transcript turns look like its own past replies.
_MIXED_HISTORY_NOTE = """\


Some earlier turns in this conversation are prefixed with a bracketed persona
tag, e.g. "[JARVIS]". Those were written by a different persona, not by you --
they are shown so you have the full context, but they are not your own past
words and you should not imitate their tone. Answer every message from here
on in your own voice, as described above."""


@dataclass(frozen=True)
class PersonaConfig:
    id: Persona
    display_name: str
    tagline: str
    style_prompt: str
    extra_boundaries: str = ""
    refusal_message: str = (
        "I can't give you that one -- it crossed a line my output filter enforces. Ask me something else."
    )
    output_filter: Callable[[str], FilterResult] | None = field(default=None)


def build_system_prompt(config: PersonaConfig, *, mixed_history: bool = False) -> str:
    """Assemble the single system message sent for this persona.

    Deliberately ONE message, not several: the Gemini provider joins every
    system-role message it's given, but Groq's OpenAI-style API only reliably
    honours system messages at the front of the list -- concatenating into one
    message is safe on both providers (app/llm/gemini.py, app/llm/groq.py).
    """
    parts = [config.style_prompt]
    if config.extra_boundaries:
        parts.append(config.extra_boundaries)
    parts.append(SHARED_CAPABILITY_NOTE)
    prompt = "\n\n".join(parts)
    if mixed_history:
        prompt += _MIXED_HISTORY_NOTE
    return prompt
