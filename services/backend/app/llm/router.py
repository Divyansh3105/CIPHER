"""Routes chat requests to the primary provider, falling back on failure.

Default routing is deliberately simple: always try the primary (Gemini)
first, and only use the fallback (Groq) if the primary raises. Section 4's
"quick message -> fast model" routing is a later refinement once we have
signals (message length/intent) worth routing on.

Phase 4 adds a runtime pin (app/llm/registry.py). Two rules make it safe:

1. A pinned model NEVER falls back. Silent fallback is the right behaviour
   for default routing -- an answer beats an outage -- but it is exactly the
   wrong behaviour once the user has named a model, because the entire point
   of naming one is knowing which one answered. A pinned model that fails
   raises ModelUnavailableError and says so.
2. The pin is process-lifetime only, never persisted. A restart always
   returns to the configured defaults, so it is impossible to strand
   yourself on a model you meant to try for one message.
"""
import logging
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.gemini import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from app.llm.gemini import GeminiProvider
from app.llm.groq import DEFAULT_MODEL as GROQ_DEFAULT_MODEL
from app.llm.groq import GroqProvider
from app.llm.registry import ModelSpec, ModelUnavailableError

logger = logging.getLogger(__name__)


class LLMRouter:
    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self._primary = primary
        self._fallback = fallback
        self._providers = {primary.name: primary, fallback.name: fallback}
        self._pinned: ModelSpec | None = None

    # --- runtime pin ----------------------------------------------------

    def pin(self, spec: ModelSpec) -> None:
        if spec.provider not in self._providers:
            # Unreachable via resolve_model() -- every registry entry names a
            # registered provider -- but a registry edit could break it, and
            # the failure should surface here rather than at request time.
            raise ValueError(f"No provider named {spec.provider!r} is registered on this router")
        logger.info("Pinning model to %s (%s)", spec.id, spec.provider)
        self._pinned = spec

    def unpin(self) -> None:
        logger.info("Unpinning model; back to default routing")
        self._pinned = None

    @property
    def pinned(self) -> ModelSpec | None:
        return self._pinned

    def default_model_id(self) -> str:
        return GEMINI_DEFAULT_MODEL if self._primary.name == "gemini" else GROQ_DEFAULT_MODEL

    # --- generation -----------------------------------------------------

    async def generate(self, messages: list[LLMMessage]) -> tuple[LLMResponse, bool]:
        """Returns (response, fell_back). `fell_back` is always False when a
        model is pinned, because a pinned model does not fall back at all.
        """
        if self._pinned is not None:
            provider = self._providers[self._pinned.provider]
            try:
                return await provider.agenerate(messages, model=self._pinned.id), False
            except LLMProviderError as exc:
                raise ModelUnavailableError(
                    f"{self._pinned.display_name} is pinned and failed: {exc}. "
                    f"Nothing was answered on a different model -- switch back to the default to continue."
                ) from exc

        try:
            return await self._primary.agenerate(messages), False
        except LLMProviderError as primary_error:
            logger.warning("Primary LLM provider (%s) failed, falling back: %s", self._primary.name, primary_error)
            try:
                return await self._fallback.agenerate(messages), True
            except LLMProviderError as fallback_error:
                raise LLMProviderError(
                    f"Both providers failed. primary={primary_error} fallback={fallback_error}"
                ) from fallback_error


def build_llm_router(settings: Settings) -> LLMRouter:
    return LLMRouter(
        primary=GeminiProvider(api_key=settings.gemini_api_key),
        fallback=GroqProvider(api_key=settings.groq_api_key),
    )


@lru_cache
def get_llm_router() -> LLMRouter:
    """Process-wide singleton so provider clients aren't rebuilt per-request.

    Also what makes the runtime pin behave: it lives on this one instance,
    so it survives across requests and dies with the process.
    """
    return build_llm_router(get_settings())
