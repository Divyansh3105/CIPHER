"""Provider-agnostic LLM interface.

Swapping Gemini -> Claude -> OpenAI later should be a matter of adding a new
class here, not rewriting the chat endpoint (docs/architecture.md, Section 4).
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ImagePart:
    """One image attached to a message (Phase 7).

    Raw bytes plus a media type, rather than a path or a URL. The provider
    is handed exactly what it will send, so nothing downstream can be talked
    into fetching a different file than the one that was inspected -- and
    the media type is carried explicitly because guessing it from the bytes
    is how an endpoint ends up rejecting a perfectly good image with an error
    that looks like the whole feature is broken.
    """

    data: bytes
    media_type: str = "image/jpeg"


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str
    #: Images accompanying this message. Providers that cannot accept them
    #: must raise rather than silently answering from the text alone -- an
    #: answer about an image the model never saw is worse than an error.
    images: tuple[ImagePart, ...] = ()


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    provider: str


class LLMProviderError(Exception):
    """Raised when a provider fails to produce a response (network, auth, rate limit, ...).

    `retry_after` is set when the provider said it is out of quota and for
    how long, in seconds. The router uses it to stop sending default-routed
    calls there until then, rather than paying a failed call on each one.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMProvider(ABC):
    name: str
    #: Whether this provider can accept LLMMessage.images. Checked by the
    #: router before a vision request is sent, so the failure is "this model
    #: cannot see" rather than a provider-specific 400.
    supports_vision: bool = False

    @abstractmethod
    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        """Generate a reply for the given message history (system prompt included).

        `model` overrides the provider's configured default for this one call
        (Phase 4's runtime brain swap, app/llm/registry.py). It is per-call
        rather than mutable provider state so a swap can never leak into
        another request through the shared provider singleton.
        """
        raise NotImplementedError

    async def astream(self, messages: list[LLMMessage], model: str | None = None) -> AsyncIterator[LLMResponse]:
        """The reply in pieces: each LLMResponse's `content` is only the new text.

        The default yields the whole reply as one piece, so a provider (or a
        test fake) with no streaming API still works everywhere a stream is
        expected. Raises LLMProviderError like agenerate, including partway
        through -- callers must not assume a failure means nothing was sent.
        """
        yield await self.agenerate(messages, model=model)
