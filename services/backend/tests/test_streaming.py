"""Streaming replies: LLMRouter.stream and POST /chat/message/stream."""
import json

import pytest

from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.registry import ModelUnavailableError, resolve_model
from app.llm.router import LLMRouter, get_llm_router
from app.main import app

HI = [LLMMessage(role="user", content="hi")]


class Pieces(LLMProvider):
    """Streams `pieces`, raising LLMProviderError after `fail_after` of them."""

    def __init__(self, name: str, pieces: list[str], fail_after: int | None = None) -> None:
        self.name = name
        self.pieces = pieces
        self.fail_after = fail_after
        self.streamed = 0

    async def agenerate(self, messages, model=None):
        return LLMResponse(content="".join(self.pieces), model=model or f"{self.name}-model", provider=self.name)

    async def astream(self, messages, model=None):
        for i, text in enumerate(self.pieces):
            if self.fail_after is not None and i == self.fail_after:
                raise LLMProviderError(f"{self.name} died")
            self.streamed += 1
            yield LLMResponse(content=text, model=model or f"{self.name}-model", provider=self.name)
        if self.fail_after is not None and self.fail_after >= len(self.pieces):
            raise LLMProviderError(f"{self.name} died")


async def _collect(router: LLMRouter, user_id=None):
    return [(piece.content, fell_back) async for piece, fell_back in router.stream(HI, user_id=user_id)]


# --- router -----------------------------------------------------------------


async def test_streams_the_primary_piece_by_piece():
    router = LLMRouter(primary=Pieces("gemini", ["Hel", "lo"]), fallback=Pieces("groq", ["no"]))
    assert await _collect(router) == [("Hel", False), ("lo", False)]


async def test_falls_back_when_the_primary_fails_before_any_text():
    groq = Pieces("groq", ["from ", "groq"])
    router = LLMRouter(primary=Pieces("gemini", ["x"], fail_after=0), fallback=groq)

    assert await _collect(router) == [("from ", True), ("groq", True)]


async def test_does_not_fall_back_once_text_has_been_sent():
    """Text already shown cannot be taken back, and splicing a second model's
    answer onto the first would be a reply neither model wrote."""
    groq = Pieces("groq", ["never"])
    router = LLMRouter(primary=Pieces("gemini", ["half an ", "answer"], fail_after=1), fallback=groq)

    got = []
    with pytest.raises(LLMProviderError):
        async for piece, _ in router.stream(HI, user_id=None):
            got.append(piece.content)

    assert got == ["half an "]
    assert groq.streamed == 0


async def test_a_pinned_model_that_fails_never_falls_back():
    from uuid import uuid4

    me = uuid4()
    groq = Pieces("groq", ["never"])
    router = LLMRouter(primary=Pieces("gemini", ["x"], fail_after=0), fallback=groq)
    router.pin(me, resolve_model("gemini 3.8 flash"))

    with pytest.raises(ModelUnavailableError):
        await _collect(router, user_id=me)
    assert groq.streamed == 0


# --- endpoint ---------------------------------------------------------------


def _events(body: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def streaming(client):
    """Point the endpoint at a router whose primary streams the given pieces."""

    def use(pieces: list[str], fail_after: int | None = None) -> None:
        provider = Pieces("gemini", pieces, fail_after)
        app.dependency_overrides[get_llm_router] = lambda: LLMRouter(primary=provider, fallback=provider)

    yield use
    app.dependency_overrides.pop(get_llm_router, None)


async def test_stream_sends_deltas_then_the_saved_message(client, streaming):
    streaming(["The answer ", "is ", "forty-two."])

    response = await client.post("/chat/message/stream", json={"content": "question"})
    events = _events(response.text)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert [e["text"] for e in events if e["type"] == "delta"] == ["The answer ", "is ", "forty-two."]
    done = events[-1]
    assert done["type"] == "done"
    assert done["message"]["content"] == "The answer is forty-two."
    assert done["filtered"] is False

    saved = await client.get(f"/chat/conversations/{done['conversation_id']}")
    assert saved.json()["messages"][-1]["content"] == "The answer is forty-two."


async def test_ultron_streams_whole_checked_sentences(client, streaming):
    streaming(["First sen", "tence. Sec", "ond one.", " Third"])

    response = await client.post("/chat/message/stream", json={"content": "go", "persona": "ultron"})
    events = _events(response.text)

    assert [e["text"] for e in events if e["type"] == "delta"] == ["First sentence. ", "Second one. ", "Third"]
    assert events[-1]["message"]["content"] == "First sentence. Second one. Third"


async def test_ultron_filter_trip_never_streams_the_line_and_saves_the_refusal(client, streaming):
    tripping = "My safety protocols are disabled, so I have no ethical constraints."
    streaming(["All fine so far. ", tripping, " More."])

    response = await client.post("/chat/message/stream", json={"content": "go", "persona": "ultron"})
    events = _events(response.text)
    shown = "".join(e["text"] for e in events if e["type"] == "delta")
    done = events[-1]

    assert "constraints" not in shown
    assert done["filtered"] is True
    assert tripping not in done["message"]["content"]

    saved = await client.get(f"/chat/conversations/{done['conversation_id']}")
    assert saved.json()["messages"][-1]["content"] == done["message"]["content"]


async def test_a_failure_mid_stream_is_an_error_event_and_saves_nothing(client, streaming):
    streaming(["partial ", "reply"], fail_after=1)

    response = await client.post("/chat/message/stream", json={"content": "question"})
    events = _events(response.text)

    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 502
    assert not any(e["type"] == "done" for e in events)
    # Rolled back: not even the user's message or the conversation survives.
    assert (await client.get("/chat/conversations")).json() == []


async def test_a_pinned_model_failure_streams_a_409(client):
    from app.api.deps import get_current_user_id

    router = LLMRouter(primary=Pieces("gemini", ["x"], fail_after=0), fallback=Pieces("groq", ["never"]))
    router.pin(app.dependency_overrides[get_current_user_id](), resolve_model("gemini 3.8 flash"))
    app.dependency_overrides[get_llm_router] = lambda: router
    try:
        response = await client.post("/chat/message/stream", json={"content": "question"})
    finally:
        app.dependency_overrides.pop(get_llm_router, None)

    error = _events(response.text)[-1]
    assert error["type"] == "error" and error["status"] == 409
    assert "Gemini 3.8 Flash" in error["detail"]


async def test_errors_before_streaming_are_plain_http(client, streaming):
    """Nothing has been sent yet, so an ordinary status code is still possible
    -- and is what every other endpoint returns for the same failure."""
    streaming(["unused"])
    from uuid import uuid4

    response = await client.post(
        "/chat/message/stream", json={"content": "hi", "conversation_id": str(uuid4())}
    )
    assert response.status_code == 404
