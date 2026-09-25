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
3. The pin is per user. It used to be one slot on this process-wide router,
   which was fine while every request was the same dev user and wrong the
   moment there were two accounts: one user's pin changed the model that
   answered everyone else.
"""
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from uuid import UUID

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
        self._pins: dict[UUID, ModelSpec] = {}

    # --- runtime pin ----------------------------------------------------

    def pin(self, user_id: UUID, spec: ModelSpec) -> None:
        if spec.provider not in self._providers:
            # Unreachable via resolve_model() -- every registry entry names a
            # registered provider -- but a registry edit could break it, and
            # the failure should surface here rather than at request time.
            raise ValueError(f"No provider named {spec.provider!r} is registered on this router")
        logger.info("Pinning model to %s (%s) for user %s", spec.id, spec.provider, user_id)
        self._pins[user_id] = spec

    def unpin(self, user_id: UUID) -> None:
        logger.info("Unpinning model for user %s; back to default routing", user_id)
        self._pins.pop(user_id, None)

    def pinned_for(self, user_id: UUID | None) -> ModelSpec | None:
        return self._pins.get(user_id) if user_id is not None else None

    def default_model_id(self) -> str:
        return GEMINI_DEFAULT_MODEL if self._primary.name == "gemini" else GROQ_DEFAULT_MODEL

    # --- generation -----------------------------------------------------

    async def generate(
        self, messages: list[LLMMessage], *, user_id: UUID | None
    ) -> tuple[LLMResponse, bool]:
        """Returns (response, fell_back). `fell_back` is always False when a
        model is pinned, because a pinned model does not fall back at all.

        `user_id` is required, not defaulted: a call site that forgot it would
        quietly answer a pinned user on the default model, which is the exact
        failure rule 1 exists to prevent. Pass None only where there is no
        user at all (the golden-set scripts).
        """
        pinned = self.pinned_for(user_id)
        if pinned is not None:
            provider = self._providers[pinned.provider]
            try:
                return await provider.agenerate(messages, model=pinned.id), False
            except LLMProviderError as exc:
                raise _pinned_failed(pinned, exc) from exc

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

    async def stream(
        self, messages: list[LLMMessage], *, user_id: UUID | None
    ) -> AsyncIterator[tuple[LLMResponse, bool]]:
        """generate(), in pieces: yields (piece, fell_back) per chunk of text.

        Same rules as generate, plus one that streaming forces: the fallback
        only takes over if the primary fails BEFORE its first piece. Once text
        has reached the user it cannot be taken back, and starting again on
        another model would splice two answers together -- so a failure after
        that point is raised as a failure.
        """
        pinned = self.pinned_for(user_id)
        if pinned is not None:
            provider = self._providers[pinned.provider]
            try:
                async for piece in provider.astream(messages, model=pinned.id):
                    yield piece, False
            except LLMProviderError as exc:
                raise _pinned_failed(pinned, exc) from exc
            return

        started = False
        try:
            async for piece in self._primary.astream(messages):
                started = True
                yield piece, False
            return
        except LLMProviderError as primary_error:
            if started:
                raise
            logger.warning("Primary LLM provider (%s) failed, falling back: %s", self._primary.name, primary_error)
            failure = primary_error

        try:
            async for piece in self._fallback.astream(messages):
                yield piece, True
        except LLMProviderError as fallback_error:
            raise LLMProviderError(
                f"Both providers failed. primary={failure} fallback={fallback_error}"
            ) from fallback_error


def _pinned_failed(pinned: ModelSpec, exc: LLMProviderError) -> ModelUnavailableError:
    return ModelUnavailableError(
        f"{pinned.display_name} is pinned and failed: {exc}. "
        f"Nothing was answered on a different model -- switch back to the default to continue."
    )


def build_llm_router(settings: Settings) -> LLMRouter:
    return LLMRouter(
        primary=GeminiProvider(api_key=settings.gemini_api_key),
        fallback=GroqProvider(api_key=settings.groq_api_key),
    )


@lru_cache
def get_llm_router() -> LLMRouter:
    """Process-wide singleton so provider clients aren't rebuilt per-request.

    Also what makes the runtime pins behave: they live on this one instance,
    so they survive across requests and die with the process.
    """
    return build_llm_router(get_settings())
