"""Groq provider -- fast/fallback model per docs/architecture.md Section 4.

Model pinned to openai/gpt-oss-120b: llama-3.3-70b-versatile (the model
named in the original architecture doc) returned 404 "does not exist" as
of 2026-09-02 -- Groq's Llama chat models are gone from this account's
client.models.list() entirely. Verified live with a real chat.completions
call before pinning this.
"""
from groq import APIError, AsyncGroq

from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse

DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        model = model or self._model
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except APIError as exc:
            raise LLMProviderError(f"Groq request failed ({model}): {exc}") from exc

        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice and choice.message else None
        if not text:
            raise LLMProviderError(f"Groq returned an empty response ({model})")

        return LLMResponse(content=text, model=model, provider=self.name)
