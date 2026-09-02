"""Unit tests for the primary->fallback LLM routing logic."""
import pytest

from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.router import LLMRouter


class FakeProvider(LLMProvider):
    def __init__(self, name: str, *, fails: bool = False, reply: str = "hi") -> None:
        self.name = name
        self._fails = fails
        self._reply = reply
        self.calls = 0

    async def agenerate(self, messages: list[LLMMessage]) -> LLMResponse:
        self.calls += 1
        if self._fails:
            raise LLMProviderError(f"{self.name} is down")
        return LLMResponse(content=self._reply, model=f"{self.name}-model", provider=self.name)


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback():
    primary = FakeProvider("primary", reply="from primary")
    fallback = FakeProvider("fallback")
    router = LLMRouter(primary=primary, fallback=fallback)

    response, fell_back = await router.generate([LLMMessage(role="user", content="hello")])

    assert response.content == "from primary"
    assert response.provider == "primary"
    assert fell_back is False
    assert primary.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_primary_failure_falls_back():
    primary = FakeProvider("primary", fails=True)
    fallback = FakeProvider("fallback", reply="from fallback")
    router = LLMRouter(primary=primary, fallback=fallback)

    response, fell_back = await router.generate([LLMMessage(role="user", content="hello")])

    assert response.content == "from fallback"
    assert response.provider == "fallback"
    assert fell_back is True
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_both_providers_failing_raises():
    primary = FakeProvider("primary", fails=True)
    fallback = FakeProvider("fallback", fails=True)
    router = LLMRouter(primary=primary, fallback=fallback)

    with pytest.raises(LLMProviderError):
        await router.generate([LLMMessage(role="user", content="hello")])
