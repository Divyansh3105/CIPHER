"""Keeping Gemini's small free-tier quota for the reply.

Two mechanisms, both in app/llm/router.py: internal calls go to Groq first,
and a provider that reports it is out of quota is skipped until it has
rested, instead of costing a failed call on every message.
"""
from uuid import uuid4

import pytest
from google.genai.errors import APIError

from app.llm import router as router_module
from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.gemini import DAILY_QUOTA_REST_SECONDS, _failure
from app.llm.registry import resolve_model
from app.llm.router import LLMRouter

HI = [LLMMessage(role="user", content="hi")]


class Fake(LLMProvider):
    def __init__(self, name: str, *, fails: bool = False, retry_after: float | None = None) -> None:
        self.name = name
        self.fails = fails
        self.retry_after = retry_after
        self.calls = 0

    async def agenerate(self, messages, model=None):
        self.calls += 1
        if self.fails:
            raise LLMProviderError(f"{self.name} is down", retry_after=self.retry_after)
        return LLMResponse(content="ok", model=model or f"{self.name}-model", provider=self.name)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(router_module.time, "monotonic", lambda: now[0])
    return now


# --- internal calls ---------------------------------------------------------


async def test_internal_calls_go_to_groq_first():
    gemini, groq = Fake("gemini"), Fake("groq")
    response, fell_back = await LLMRouter(primary=gemini, fallback=groq).generate(HI, user_id=None, internal=True)

    assert response.provider == "groq"
    assert fell_back is False  # Groq was the first choice here, not a fallback
    assert gemini.calls == 0


async def test_internal_calls_still_fall_back_to_gemini():
    gemini, groq = Fake("gemini"), Fake("groq", fails=True)
    response, fell_back = await LLMRouter(primary=gemini, fallback=groq).generate(HI, user_id=None, internal=True)

    assert response.provider == "gemini"
    assert fell_back is True


async def test_the_reply_still_prefers_gemini():
    gemini, groq = Fake("gemini"), Fake("groq")
    response, _ = await LLMRouter(primary=gemini, fallback=groq).generate(HI, user_id=None)
    assert response.provider == "gemini"


async def test_a_pin_wins_over_internal():
    """Naming a model means that model answers everything, internal calls included."""
    me = uuid4()
    gemini, groq = Fake("gemini"), Fake("groq")
    router = LLMRouter(primary=gemini, fallback=groq)
    router.pin(me, resolve_model("gemini 3.8 flash"))

    response, _ = await router.generate(HI, user_id=me, internal=True)

    assert response.provider == "gemini"
    assert groq.calls == 0


# --- resting after a quota error ------------------------------------------


async def test_a_provider_out_of_quota_is_skipped_until_it_has_rested(clock):
    gemini, groq = Fake("gemini", fails=True, retry_after=3600), Fake("groq")
    router = LLMRouter(primary=gemini, fallback=groq)

    await router.generate(HI, user_id=None)  # pays the one failed call
    response, fell_back = await router.generate(HI, user_id=None)

    assert gemini.calls == 1  # not asked again
    assert (response.provider, fell_back) == ("groq", True)  # still reported honestly

    clock[0] += 3601
    gemini.fails = False
    response, fell_back = await router.generate(HI, user_id=None)
    assert (response.provider, fell_back) == ("gemini", False)


async def test_an_ordinary_failure_does_not_rest_the_provider(clock):
    gemini, groq = Fake("gemini", fails=True), Fake("groq")
    router = LLMRouter(primary=gemini, fallback=groq)

    await router.generate(HI, user_id=None)
    await router.generate(HI, user_id=None)

    assert gemini.calls == 2  # a blip is retried next time, not skipped for an hour


async def test_when_everything_is_resting_everything_is_still_tried(clock):
    gemini = Fake("gemini", fails=True, retry_after=3600)
    groq = Fake("groq", fails=True, retry_after=3600)
    router = LLMRouter(primary=gemini, fallback=groq)
    with pytest.raises(LLMProviderError):
        await router.generate(HI, user_id=None)

    gemini.fails = False
    response, _ = await router.generate(HI, user_id=None)

    assert response.provider == "gemini"


async def test_a_pinned_model_is_tried_even_while_resting(clock):
    """Resting is a default-routing economy. The user named this model; they
    get its real answer or its real error, not a skip."""
    me = uuid4()
    gemini, groq = Fake("gemini", fails=True, retry_after=3600), Fake("groq")
    router = LLMRouter(primary=gemini, fallback=groq)
    await router.generate(HI, user_id=None)
    router.pin(me, resolve_model("gemini 3.8 flash"))

    gemini.fails = False
    response, _ = await router.generate(HI, user_id=me)

    assert response.provider == "gemini"


async def test_streaming_skips_a_resting_provider_too(clock):
    gemini, groq = Fake("gemini", fails=True, retry_after=3600), Fake("groq")
    router = LLMRouter(primary=gemini, fallback=groq)
    await router.generate(HI, user_id=None)

    pieces = [(p.provider, fell_back) async for p, fell_back in router.stream(HI, user_id=None)]

    assert pieces == [("groq", True)]
    assert gemini.calls == 1


# --- reading Gemini's 429 ---------------------------------------------------


def _gemini_429(details: list[dict]) -> APIError:
    return APIError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota", "details": details}})


def test_a_daily_quota_rests_for_an_hour_not_the_suggested_seconds():
    exc = _gemini_429([
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "42s"},
    ])
    assert _failure("gemini-3.6-flash", exc).retry_after == DAILY_QUOTA_REST_SECONDS


def test_a_per_minute_limit_rests_for_the_suggested_delay():
    exc = _gemini_429([
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"},
    ])
    assert _failure("gemini-3.6-flash", exc).retry_after == 17.0


def test_a_non_quota_error_does_not_rest():
    exc = APIError(500, {"error": {"code": 500, "status": "INTERNAL", "message": "boom"}})
    assert _failure("gemini-3.6-flash", exc).retry_after is None
