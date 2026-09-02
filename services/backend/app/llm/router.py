"""Routes chat requests to the primary provider, falling back on failure.

Phase 1 routing is deliberately simple: always try the primary (Gemini)
first, and only use the fallback (Groq) if the primary raises. Section 4's
"quick message -> fast model" routing is a Phase 2+ refinement once we have
signals (message length/intent) worth routing on.
"""
import logging
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.gemini import GeminiProvider
from app.llm.groq import GroqProvider

logger = logging.getLogger(__name__)


class LLMRouter:
    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    async def generate(self, messages: list[LLMMessage]) -> tuple[LLMResponse, bool]:
        """Returns (response, fell_back)."""
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
    """Process-wide singleton so provider clients aren't rebuilt per-request."""
    return build_llm_router(get_settings())
