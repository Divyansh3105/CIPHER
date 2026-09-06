"""Gemini provider -- primary model per docs/architecture.md Section 4.

Model pinned to gemini-3.6-flash: gemini-2.5-flash (the model named in the
original architecture doc) returned 404 "no longer available to new users"
as of 2026-09-02 -- verified live against the account's actual API key via
client.models.list() and a real generateContent call before pinning this.
"""
from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse

DEFAULT_MODEL = "gemini-3.6-flash"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        model = model or self._model
        system_parts = [m.content for m in messages if m.role == "system"]
        system_instruction = "\n\n".join(system_parts) or None

        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part.from_text(text=m.content)],
            )
            for m in messages
            if m.role != "system"
        ]

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
        except APIError as exc:
            raise LLMProviderError(f"Gemini request failed ({model}): {exc}") from exc

        text = response.text
        if not text:
            raise LLMProviderError(f"Gemini returned an empty response ({model})")

        return LLMResponse(content=text, model=model, provider=self.name)
