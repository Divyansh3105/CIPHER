"""Provider-agnostic LLM interface.

Swapping Gemini -> Claude -> OpenAI later should be a matter of adding a new
class here, not rewriting the chat endpoint (docs/architecture.md, Section 4).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    provider: str


class LLMProviderError(Exception):
    """Raised when a provider fails to produce a response (network, auth, rate limit, ...)."""


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        """Generate a reply for the given message history (system prompt included).

        `model` overrides the provider's configured default for this one call
        (Phase 4's runtime brain swap, app/llm/registry.py). It is per-call
        rather than mutable provider state so a swap can never leak into
        another request through the shared provider singleton.
        """
        raise NotImplementedError
