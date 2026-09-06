"""The set of models we know exist, and the spoken names that reach them.

Phase 4 lets the user change which model is thinking, mid-conversation and
by voice. That turns a speech transcript into a model id, which is the exact
place a "helpful" system quietly does the wrong thing: you say "gemini four
flash", a loose matcher sees "gemini" and "flash", picks the nearest thing it
has, and cheerfully answers on a model you did not ask for. You then spend an
hour comparing two models that were the same model.

So resolution here is exact-alias-only. There is no fuzzy fallback, no
nearest-match, no "did you mean". An unknown name raises UnknownModelError
carrying the list of names that do work. A loud refusal costs one retry; a
silent wrong answer costs an afternoon.

WHY THIS LIST IS SHORT
Every entry was verified with a real generate call against this project's
own API keys on 2026-09-06, not taken from a provider's model list. That
distinction is not pedantry: `client.models.list()` on the Gemini key
advertises `gemini-2.5-pro` and `gemini-2.5-flash-lite`, and a real call to
either returns 404 "no longer available". `gemini-3.1-pro-preview` exists and
answers 429 on the free tier, so it is left out too -- a model you cannot
actually reach does not belong in a list whose entire job is being true.

Re-verify with scripts/verify_models.py before adding to this list.
"""
from dataclasses import dataclass

from app.llm.base import LLMProviderError


@dataclass(frozen=True)
class ModelSpec:
    id: str
    #: Must match the `name` of an LLMProvider registered on the router.
    provider: str
    display_name: str
    #: Spoken/typed forms that resolve to this model. Compared after
    #: normalisation (lowercase, punctuation stripped, whitespace collapsed),
    #: so "Gemini 3.8 Flash" and "gemini 3.8 flash" are the same alias.
    aliases: tuple[str, ...]
    note: str = ""


# Ordered best-guess-first; GET /models returns them in this order.
MODEL_REGISTRY: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="gemini-3.8-flash",
        provider="gemini",
        display_name="Gemini 3.8 Flash",
        aliases=("gemini 3.8 flash", "gemini 3.8", "3.8 flash", "gemini three point eight flash"),
        note="Newest Flash on this key.",
    ),
    ModelSpec(
        id="gemini-3.7-flash",
        provider="gemini",
        display_name="Gemini 3.7 Flash",
        aliases=("gemini 3.7 flash", "gemini 3.7", "3.7 flash"),
    ),
    ModelSpec(
        id="gemini-3.6-flash",
        provider="gemini",
        display_name="Gemini 3.6 Flash",
        aliases=("gemini 3.6 flash", "gemini 3.6", "3.6 flash"),
        note="The default primary (app/llm/gemini.py).",
    ),
    ModelSpec(
        id="gemini-3.5-flash",
        provider="gemini",
        display_name="Gemini 3.5 Flash",
        aliases=("gemini 3.5 flash", "gemini 3.5", "3.5 flash"),
    ),
    ModelSpec(
        id="openai/gpt-oss-120b",
        provider="groq",
        display_name="GPT-OSS 120B (Groq)",
        aliases=("gpt oss 120b", "gpt oss", "oss 120b", "120b", "groq 120b"),
        note="The default fallback (app/llm/groq.py).",
    ),
    ModelSpec(
        id="openai/gpt-oss-20b",
        provider="groq",
        display_name="GPT-OSS 20B (Groq)",
        aliases=("gpt oss 20b", "oss 20b", "20b", "groq 20b"),
    ),
    ModelSpec(
        id="qwen/qwen3.8-27b",
        provider="groq",
        display_name="Qwen 3.8 27B (Groq)",
        aliases=("qwen", "qwen 3.8", "qwen 3.8 27b", "qwen 27b"),
    ),
)

#: Phrases that mean "stop pinning, go back to normal routing".
RESET_ALIASES = frozenset(
    {
        "default",
        "normal",
        "normal brain",
        "your normal brain",
        "the default",
        "default brain",
        "back to normal",
        "auto",
        "automatic",
    }
)

#: Command words stripped off the front of a spoken phrase before matching,
#: longest first so "go back to" wins over "go".
_COMMAND_PREFIXES = (
    "switch your brain to",
    "change your brain to",
    "swap your brain to",
    "put your brain on",
    "go back to",
    "switch to",
    "change to",
    "swap to",
    "switch over to",
    "try on",
    "run on",
    "use",
    "try",
    "switch",
    "change",
    "swap",
)

_SUFFIXES = (" please", " now", " model", " brain", " instead")


class UnknownModelError(LookupError):
    """Raised when a spoken name does not exactly match a known model.

    Carries `known` so the caller can tell the user what does exist instead
    of just saying no.
    """

    def __init__(self, spoken: str, known: list[str]) -> None:
        self.spoken = spoken
        self.known = known
        super().__init__(
            f"I don't have a model called {spoken!r}. What I actually have: {', '.join(known)}."
        )


class ModelUnavailableError(LLMProviderError):
    """Raised when a pinned model fails.

    Distinct from a plain LLMProviderError so the chat endpoint can say the
    *pinned* model failed rather than implying the whole stack is down --
    and so nothing is tempted to quietly answer on a different model.
    """


def normalise(spoken: str) -> str:
    """Lowercase, drop punctuation that speech recognition sprinkles in, and
    collapse whitespace. Decimal points are kept: they are the version.
    """
    cleaned = "".join(ch if (ch.isalnum() or ch in " .-/") else " " for ch in spoken.lower())
    cleaned = cleaned.replace("-", " ").replace("/", " ")
    return " ".join(cleaned.split()).strip(" .")


#: Leading determiners dropped after the command prefix, so "go back to your
#: normal brain" reduces to "normal brain".
_DETERMINERS = ("your ", "the ", "my ", "its ", "it is ")


def _strip_prefix(phrase: str) -> str:
    for prefix in _COMMAND_PREFIXES:
        if phrase == prefix:
            return ""
        if phrase.startswith(prefix + " "):
            phrase = phrase[len(prefix) + 1 :]
            break
    for determiner in _DETERMINERS:
        if phrase.startswith(determiner):
            phrase = phrase[len(determiner) :]
            break
    return phrase.strip()


def _strip_command_words(phrase: str) -> str:
    phrase = _strip_prefix(phrase)
    for suffix in _SUFFIXES:
        if phrase.endswith(suffix):
            phrase = phrase[: -len(suffix)]
    return phrase.strip()


def _build_alias_index() -> dict[str, ModelSpec]:
    index: dict[str, ModelSpec] = {}
    for spec in MODEL_REGISTRY:
        for alias in (spec.id, *spec.aliases):
            key = normalise(alias)
            # A duplicate alias would make resolution depend on declaration
            # order, which is precisely the kind of quiet ambiguity this
            # module exists to prevent. Fail at import instead.
            if key in index and index[key] is not spec:
                raise ValueError(f"Alias {key!r} maps to both {index[key].id} and {spec.id}")
            index[key] = spec
    return index


ALIAS_INDEX: dict[str, ModelSpec] = _build_alias_index()


def known_names() -> list[str]:
    return [spec.display_name for spec in MODEL_REGISTRY]


def is_reset_phrase(spoken: str) -> bool:
    """True for "default", "go back to your normal brain", and friends.

    Checks the phrase both before and after suffix stripping: " brain" is a
    suffix worth ignoring on "switch to qwen brain", but it is load-bearing
    in "normal brain", and stripping it first would turn a reset request
    into an unrecognised one.
    """
    phrase = normalise(spoken)
    return (
        phrase in RESET_ALIASES
        or _strip_prefix(phrase) in RESET_ALIASES
        or _strip_command_words(phrase) in RESET_ALIASES
    )


def resolve_model(spoken: str) -> ModelSpec:
    """Map a spoken or typed model name to a ModelSpec, or refuse.

    Exact match on the normalised alias index only. Deliberately no
    similarity scoring: "gemini 4 flash" and "opus 5" must fail loudly rather
    than land on the nearest thing in the list.
    """
    phrase = _strip_command_words(normalise(spoken))
    if not phrase:
        raise UnknownModelError(spoken, known_names())
    spec = ALIAS_INDEX.get(phrase)
    if spec is None:
        raise UnknownModelError(spoken, known_names())
    return spec
