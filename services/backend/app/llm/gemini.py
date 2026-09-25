"""Gemini provider -- primary model per docs/architecture.md Section 4.

Model pinned to gemini-3.6-flash: gemini-2.5-flash (the model named in the
original architecture doc) returned 404 "no longer available to new users"
as of 2026-09-02 -- verified live against the account's actual API key via
client.models.list() and a real generateContent call before pinning this.
"""
from collections.abc import AsyncIterator

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse

DEFAULT_MODEL = "gemini-3.6-flash"

# ponytail: a per-day quota resets at midnight Pacific. Resting an hour and
# trying again costs one failed call per hour, which is simpler than
# timezone arithmetic (and python:slim images ship without a tz database).
DAILY_QUOTA_REST_SECONDS = 3600.0
DEFAULT_REST_SECONDS = 60.0


def _failure(model: str, exc: APIError) -> LLMProviderError:
    """An LLMProviderError, carrying how long to stay away if this was a quota 429."""
    retry_after = None
    if exc.code == 429:
        details = (exc.details or {}).get("error", {}).get("details", []) if isinstance(exc.details, dict) else []
        retry_after = DEFAULT_REST_SECONDS
        for detail in details:
            kind = detail.get("@type", "")
            if kind.endswith("QuotaFailure") and any(
                "PerDay" in v.get("quotaId", "") for v in detail.get("violations", [])
            ):
                # The error's own retryDelay (~30-60s) is wrong for a daily
                # quota: retrying then just fails again.
                retry_after = DAILY_QUOTA_REST_SECONDS
                break
            if kind.endswith("RetryInfo"):
                try:
                    retry_after = float(str(detail.get("retryDelay", "")).rstrip("s"))
                except ValueError:
                    pass
    return LLMProviderError(f"Gemini request failed ({model}): {exc}", retry_after=retry_after)


class GeminiProvider(LLMProvider):
    name = "gemini"
    supports_vision = True

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def _request(self, messages: list[LLMMessage]) -> tuple[list[types.Content], types.GenerateContentConfig]:
        """Shared by agenerate and astream, so the two can never send different requests."""
        system_parts = [m.content for m in messages if m.role == "system"]
        system_instruction = "\n\n".join(system_parts) or None

        contents = []
        for m in messages:
            if m.role == "system":
                continue
            parts = [types.Part.from_text(text=m.content)]
            # Images after the text: the question frames what to look for,
            # and putting it first measurably improves grounding.
            parts.extend(
                types.Part.from_bytes(data=image.data, mime_type=image.media_type)
                for image in m.images
            )
            contents.append(
                types.Content(role="model" if m.role == "assistant" else "user", parts=parts)
            )
        return contents, types.GenerateContentConfig(system_instruction=system_instruction)

    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        model = model or self._model
        contents, config = self._request(messages)
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except APIError as exc:
            raise _failure(model, exc) from exc

        text = response.text
        if not text:
            raise LLMProviderError(f"Gemini returned an empty response ({model})")

        return LLMResponse(content=text, model=model, provider=self.name)

    async def astream(self, messages: list[LLMMessage], model: str | None = None) -> AsyncIterator[LLMResponse]:
        model = model or self._model
        contents, config = self._request(messages)
        got_text = False
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=model, contents=contents, config=config
            )
            async for chunk in stream:
                if chunk.text:
                    got_text = True
                    yield LLMResponse(content=chunk.text, model=model, provider=self.name)
        except APIError as exc:
            raise _failure(model, exc) from exc

        if not got_text:
            raise LLMProviderError(f"Gemini returned an empty response ({model})")
