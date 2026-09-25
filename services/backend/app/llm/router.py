"""Routes chat requests to the primary provider, falling back on failure.

Default routing tries the primary (Gemini) first and uses the fallback
(Groq) only if it raises -- for the reply the user reads. Internal calls
(`internal=True`: agent routing, tool choice, memory extraction) go the
other way round, Groq first. Gemini's free tier allows each model about 20
requests a day, and one chat turn made up to three internal calls on top of
the reply, so the quota ran out after a handful of messages. Groq's limits
are far higher, and those calls produce a word or a JSON list nobody reads.

A provider that reports it is out of quota (LLMProviderError.retry_after)
rests: default routing skips it until then, instead of paying a failed call
on every message. If every provider is resting they are all tried anyway --
a stale guess about quota must never be the reason nothing answers.

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
import time
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
        #: Provider name -> time.monotonic() before which default routing skips it.
        self._resting_until: dict[str, float] = {}

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

    # --- default routing ----------------------------------------------

    def _order(self, internal: bool) -> tuple[list[LLMProvider], LLMProvider]:
        """(providers to try in order, the one that counts as not falling back)."""
        preferred = [self._fallback, self._primary] if internal else [self._primary, self._fallback]
        now = time.monotonic()
        awake = [p for p in preferred if self._resting_until.get(p.name, 0.0) <= now]
        return (awake or preferred), preferred[0]

    def _failed(self, provider: LLMProvider, exc: LLMProviderError, remaining: int) -> None:
        if exc.retry_after:
            self._resting_until[provider.name] = time.monotonic() + exc.retry_after
            logger.warning("%s is out of quota; skipping it for %.0fs", provider.name, exc.retry_after)
        if remaining:
            logger.warning("LLM provider %s failed, falling back: %s", provider.name, exc)

    # --- generation -----------------------------------------------------

    async def generate(
        self, messages: list[LLMMessage], *, user_id: UUID | None, internal: bool = False
    ) -> tuple[LLMResponse, bool]:
        """Returns (response, fell_back). `fell_back` is always False when a
        model is pinned, because a pinned model does not fall back at all.

        `user_id` is required, not defaulted: a call site that forgot it would
        quietly answer a pinned user on the default model, which is the exact
        failure rule 1 exists to prevent. Pass None only where there is no
        user at all (the golden-set scripts).

        `internal` marks a call whose output nobody reads directly; see the
        module docstring. A pin still wins over it: a user who named a model
        gets that model everywhere, exactly as before.
        """
        pinned = self.pinned_for(user_id)
        if pinned is not None:
            provider = self._providers[pinned.provider]
            try:
                return await provider.agenerate(messages, model=pinned.id), False
            except LLMProviderError as exc:
                raise _pinned_failed(pinned, exc) from exc

        order, preferred = self._order(internal)
        errors = []
        for i, provider in enumerate(order):
            try:
                return await provider.agenerate(messages), provider is not preferred
            except LLMProviderError as exc:
                self._failed(provider, exc, remaining=len(order) - i - 1)
                errors.append(f"{provider.name}={exc}")
        raise LLMProviderError("All providers failed. " + " ".join(errors))

    async def stream(
        self, messages: list[LLMMessage], *, user_id: UUID | None
    ) -> AsyncIterator[tuple[LLMResponse, bool]]:
        """generate(), in pieces: yields (piece, fell_back) per chunk of text.

        Same rules as generate, plus one that streaming forces: a fallback
        only takes over if the provider before it fails BEFORE its first
        piece. Once text has reached the user it cannot be taken back, and
        starting again on another model would splice two answers together --
        so a failure after that point is raised as a failure.
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

        order, preferred = self._order(internal=False)
        errors = []
        for i, provider in enumerate(order):
            started = False
            try:
                async for piece in provider.astream(messages):
                    started = True
                    yield piece, provider is not preferred
                return
            except LLMProviderError as exc:
                if started:
                    raise
                self._failed(provider, exc, remaining=len(order) - i - 1)
                errors.append(f"{provider.name}={exc}")
        raise LLMProviderError("All providers failed. " + " ".join(errors))


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
