"""Resolution and refusal rules for the runtime model swap.

The tests that matter here are the negative ones. Anyone can make
"gemini 3.8 flash" resolve; the value of app/llm/registry.py is that
"gemini 4 flash" does NOT quietly become gemini 3.8.
"""
import pytest

from app.llm.registry import (
    ALIAS_INDEX,
    MODEL_REGISTRY,
    UnknownModelError,
    is_reset_phrase,
    known_names,
    normalise,
    resolve_model,
)


# --- resolution ---------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "expected_id"),
    [
        ("gemini 3.8 flash", "gemini-3.8-flash"),
        ("Gemini 3.8 Flash", "gemini-3.8-flash"),
        ("gemini-3.8-flash", "gemini-3.8-flash"),
        # Punctuation speech recognition tends to add.
        ("Gemini 3.8 Flash.", "gemini-3.8-flash"),
        ("gemini 3.6", "gemini-3.6-flash"),
        ("qwen", "qwen/qwen3.8-27b"),
        ("gpt oss 20b", "openai/gpt-oss-20b"),
    ],
)
def test_exact_aliases_resolve(spoken, expected_id):
    assert resolve_model(spoken).id == expected_id


@pytest.mark.parametrize(
    "spoken",
    [
        "switch to gemini 3.8 flash",
        "change to gemini 3.8 flash",
        "try on qwen",
        "go back to gemini 3.6",
        "use gpt oss 20b",
        "switch to gemini 3.8 flash please",
        "swap to qwen now",
    ],
)
def test_command_words_are_stripped(spoken):
    assert resolve_model(spoken).id in {s.id for s in MODEL_REGISTRY}


def test_every_registry_id_resolves_to_itself():
    for spec in MODEL_REGISTRY:
        assert resolve_model(spec.id) is spec


# --- refusal ------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken",
    [
        # The exact trap this module exists for: a real family, a version
        # that does not exist. A loose matcher would drop the "4" and load
        # 3.8, then announce success.
        "gemini 4 flash",
        "gemini 4",
        "switch to gemini 9.9 flash",
        # Real family, wrong version, other provider.
        "qwen 4",
        "gpt oss 400b",
        # A whole family we do not carry.
        "opus 5",
        "claude",
        "switch to gpt 5",
        "llama 3.3 70b",
        # Bare family names are ambiguous, so they must not resolve either.
        "gemini",
        "flash",
        "",
        "   ",
        "switch to",
    ],
)
def test_near_misses_and_unknowns_are_refused(spoken):
    with pytest.raises(UnknownModelError):
        resolve_model(spoken)


def test_refusal_names_what_is_actually_available():
    """An honest error beats a helpful guess -- but a bare "no" makes the
    user guess again, so the refusal has to carry the real options.
    """
    with pytest.raises(UnknownModelError) as excinfo:
        resolve_model("gemini 4 flash")

    error = excinfo.value
    assert error.known == known_names()
    assert "Gemini 3.8 Flash" in str(error)
    # And it must not pretend it did something.
    assert "gemini 4 flash" in str(error)


def test_refusing_does_not_leak_a_nearest_match():
    """Guards against someone later "improving" this with difflib."""
    with pytest.raises(UnknownModelError) as excinfo:
        resolve_model("gemini 3.9 flash")
    # 3.8 is one character away and must not be offered as *the* answer.
    message = str(excinfo.value)
    assert "did you mean" not in message.lower()


# --- reset --------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken",
    ["default", "normal brain", "go back to your normal brain", "back to normal", "auto"],
)
def test_reset_phrases_are_recognised(spoken):
    assert is_reset_phrase(spoken) is True


@pytest.mark.parametrize("spoken", ["gemini 3.8 flash", "qwen", "something else"])
def test_model_names_are_not_reset_phrases(spoken):
    assert is_reset_phrase(spoken) is False


# --- registry integrity -------------------------------------------------


def test_no_alias_maps_to_two_models():
    """_build_alias_index raises at import on a collision; this asserts the
    index actually got built and is not silently empty.
    """
    assert len(ALIAS_INDEX) >= len(MODEL_REGISTRY)
    assert all(spec in MODEL_REGISTRY for spec in ALIAS_INDEX.values())


def test_every_model_declares_a_registered_provider():
    assert {spec.provider for spec in MODEL_REGISTRY} <= {"gemini", "groq"}


def test_normalise_keeps_version_numbers():
    # Dropping the decimal point would silently merge 3.8 and 38.
    assert normalise("Gemini 3.8 Flash!") == "gemini 3.8 flash"
    assert normalise("openai/gpt-oss-120b") == "openai gpt oss 120b"
