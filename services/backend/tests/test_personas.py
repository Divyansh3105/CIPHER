"""Unit tests for the persona registry and system-prompt assembly."""
import pytest

from app.personas import (
    DEFAULT_PERSONA,
    PERSONAS,
    SHARED_CAPABILITY_NOTE,
    Persona,
    get_persona,
    system_prompt_for,
)


def test_every_enum_member_is_registered():
    """Catches "added the enum member, forgot the registry entry"."""
    assert set(PERSONAS) == set(Persona)


@pytest.mark.parametrize("persona", list(Persona))
def test_capability_note_appears_exactly_once(persona):
    """Regression test for the shared disclaimer being triplicated across personas."""
    assert system_prompt_for(persona).count(SHARED_CAPABILITY_NOTE) == 1


@pytest.mark.parametrize("persona", list(Persona))
def test_prompt_names_its_own_persona(persona):
    assert PERSONAS[persona].display_name in system_prompt_for(persona)


def test_prompts_are_pairwise_distinct():
    prompts = [system_prompt_for(p) for p in Persona]
    assert len(set(prompts)) == len(prompts)


def test_ultron_prompt_carries_the_hard_boundaries():
    prompt = system_prompt_for(Persona.ULTRON).lower()
    for boundary in ("violence", "illegal", "manipulate", "unauthorized", "permission"):
        assert boundary in prompt


def test_ultron_prompt_explicitly_permits_analytical_edgy_topics():
    """ULTRON must not become a lower permission tier than the other two
    personas -- it should be explicitly told that discussing risk/violence
    as subject matter is in scope, only *facilitating* harm is not.
    """
    prompt = system_prompt_for(Persona.ULTRON).lower()
    assert "analy" in prompt  # "analytically" / "analysing"


def test_only_ultron_has_an_output_filter():
    assert PERSONAS[Persona.ULTRON].output_filter is not None
    assert PERSONAS[Persona.JARVIS].output_filter is None
    assert PERSONAS[Persona.FRIDAY].output_filter is None


def test_get_persona_resolves_known_ids():
    assert get_persona("friday").id is Persona.FRIDAY
    assert get_persona(Persona.ULTRON).id is Persona.ULTRON


def test_get_persona_falls_back_on_unknown_or_missing():
    assert get_persona("gandalf").id is DEFAULT_PERSONA
    assert get_persona(None).id is DEFAULT_PERSONA


def test_mixed_history_note_is_conditional():
    plain = system_prompt_for(Persona.FRIDAY)
    mixed = system_prompt_for(Persona.FRIDAY, mixed_history=True)
    assert "[JARVIS]" not in plain
    assert "[JARVIS]" in mixed
    assert mixed.startswith(plain)


# --- Phase 3: memory ---------------------------------------------------


def test_capability_note_no_longer_denies_memory():
    """Guard against reverting to the pre-Phase-3 wording now that a memory
    store actually exists (app/models/db.py's Memory model).
    """
    lowered = SHARED_CAPABILITY_NOTE.lower()
    assert "no long-term memory" not in lowered
    assert "do not claim to remember" not in lowered


@pytest.mark.parametrize("persona", list(Persona))
def test_recall_block_is_absent_when_nothing_recalled(persona):
    assert "what you remember about this user" not in system_prompt_for(persona).lower()


@pytest.mark.parametrize("persona", list(Persona))
def test_recall_block_appears_with_memories_and_note_still_appears_once(persona):
    prompt = system_prompt_for(persona, recalled_memories=["I use Neovim, not VS Code."])
    assert "what you remember about this user" in prompt.lower()
    assert "I use Neovim, not VS Code." in prompt
    # The recall block must not create a second copy of the shared note.
    assert prompt.count(SHARED_CAPABILITY_NOTE) == 1


def test_each_persona_frames_memory_differently():
    framings = {PERSONAS[p].memory_framing for p in Persona}
    assert len(framings) == len(Persona)
    assert all(framings)  # none blank
