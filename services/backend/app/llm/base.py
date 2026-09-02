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
    async def agenerate(self, messages: list[LLMMessage]) -> LLMResponse:
        """Generate a reply for the given message history (system prompt included)."""
        raise NotImplementedError
