"""GET/POST/DELETE /models, and the router's pinning behaviour.

The behaviour under test that is easy to get wrong: a *pinned* model must
never fall back. Default routing falling back to Groq is a feature; a pinned
model falling back is a lie about which model answered.
"""
import pytest

from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.registry import ModelUnavailableError, resolve_model
from app.llm.router import LLMRouter, get_llm_router
from app.main import app


class NamedProvider(LLMProvider):
    """Fake provider that answers to a real provider name so registry specs
    can be pinned onto it.
    """

    def __init__(self, name: str, *, fails: bool = False) -> None:
        self.name = name
        self._fails = fails
        self.models: list[str | None] = []

    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        self.models.append(model)
        if self._fails:
            raise LLMProviderError(f"{self.name} is down")
        return LLMResponse(content="ok", model=model or f"{self.name}-default", provider=self.name)


@pytest.fixture
def gemini():
    return NamedProvider("gemini")


@pytest.fixture
def groq():
    return NamedProvider("groq")


@pytest.fixture
def swap_router(gemini, groq):
    """Overrides the app's router with one whose providers are named
    "gemini"/"groq", which is what registry specs point at. conftest's
    default client fixture uses a single fake provider under one name, which
    no ModelSpec can be pinned onto.
    """
    router = LLMRouter(primary=gemini, fallback=groq)
    app.dependency_overrides[get_llm_router] = lambda: router
    yield router
    app.dependency_overrides.pop(get_llm_router, None)


# --- router-level -------------------------------------------------------


@pytest.mark.asyncio
async def test_pinning_sends_the_pinned_model_id_to_the_right_provider(gemini, groq):
    router = LLMRouter(primary=gemini, fallback=groq)
    router.pin(resolve_model("qwen"))

    response, fell_back = await router.generate([LLMMessage(role="user", content="hi")])

    # Qwen lives on Groq, so the *fallback* provider must have taken it --
    # a pin selects a provider, it is not "primary with a different name".
    assert groq.models == ["qwen/qwen3.8-27b"]
    assert gemini.models == []
    assert response.model == "qwen/qwen3.8-27b"
    assert fell_back is False


@pytest.mark.asyncio
async def test_pinned_model_does_not_fall_back(groq):
    """The one that matters. A pinned model failing must surface, not get
    quietly answered by the other provider.
    """
    failing_gemini = NamedProvider("gemini", fails=True)
    router = LLMRouter(primary=failing_gemini, fallback=groq)
    router.pin(resolve_model("gemini 3.8 flash"))

    with pytest.raises(ModelUnavailableError) as excinfo:
        await router.generate([LLMMessage(role="user", content="hi")])

    assert groq.models == []  # never consulted
    assert "Gemini 3.8 Flash" in str(excinfo.value)


@pytest.mark.asyncio
async def test_unpinning_restores_fallback(gemini, groq):
    failing_gemini = NamedProvider("gemini", fails=True)
    router = LLMRouter(primary=failing_gemini, fallback=groq)
    router.pin(resolve_model("gemini 3.8 flash"))
    router.unpin()

    response, fell_back = await router.generate([LLMMessage(role="user", content="hi")])

    assert fell_back is True
    assert response.provider == "groq"


@pytest.mark.asyncio
async def test_a_fresh_router_is_never_pinned(gemini, groq):
    """Stands in for a restart: the pin is process state only, so a new
    router (a new process) always starts on the configured default.
    """
    pinned = LLMRouter(primary=gemini, fallback=groq)
    pinned.pin(resolve_model("qwen"))
    assert pinned.pinned is not None

    assert LLMRouter(primary=gemini, fallback=groq).pinned is None


def test_pinning_a_spec_with_no_registered_provider_raises(gemini, groq):
    from app.llm.registry import ModelSpec

    router = LLMRouter(primary=gemini, fallback=groq)
    with pytest.raises(ValueError):
        router.pin(ModelSpec(id="x", provider="nonexistent", display_name="X", aliases=("x",)))


# --- HTTP ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_models_lists_available_and_default_active(client, swap_router):
    response = await client.get("/models")
    assert response.status_code == 200

    body = response.json()
    assert body["active"]["pinned"] is False
    assert body["active"]["id"] == "gemini-3.6-flash"
    ids = [m["id"] for m in body["available"]]
    assert "gemini-3.8-flash" in ids
    assert "qwen/qwen3.8-27b" in ids


@pytest.mark.asyncio
async def test_post_active_pins_a_spoken_name(client, swap_router):
    response = await client.post("/models/active", json={"spoken": "switch to gemini 3.8 flash"})

    assert response.status_code == 200
    body = response.json()
    assert body["pinned"] is True
    assert body["id"] == "gemini-3.8-flash"
    assert body["display_name"] == "Gemini 3.8 Flash"
    assert swap_router.pinned.id == "gemini-3.8-flash"


@pytest.mark.asyncio
async def test_post_active_refuses_an_unknown_model_and_changes_nothing(client, swap_router):
    response = await client.post("/models/active", json={"spoken": "switch to gemini 4 flash"})

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "Gemini 3.8 Flash" in detail  # tells you what does exist
    # The refusal must not have half-applied.
    assert swap_router.pinned is None


@pytest.mark.asyncio
async def test_refusal_does_not_downgrade_an_existing_pin(client, swap_router):
    await client.post("/models/active", json={"spoken": "qwen"})
    await client.post("/models/active", json={"spoken": "gemini 4 flash"})

    assert swap_router.pinned.id == "qwen/qwen3.8-27b"


@pytest.mark.asyncio
async def test_post_active_with_a_reset_phrase_unpins(client, swap_router):
    await client.post("/models/active", json={"spoken": "qwen"})

    response = await client.post("/models/active", json={"spoken": "go back to your normal brain"})

    assert response.status_code == 200
    assert response.json()["pinned"] is False
    assert swap_router.pinned is None


@pytest.mark.asyncio
async def test_delete_active_unpins(client, swap_router):
    await client.post("/models/active", json={"spoken": "qwen"})

    response = await client.delete("/models/active")

    assert response.status_code == 200
    assert response.json()["pinned"] is False
    assert swap_router.pinned is None


@pytest.mark.asyncio
async def test_chat_uses_the_pinned_model(client, swap_router, gemini):
    await client.post("/models/active", json={"spoken": "gemini 3.8 flash"})

    response = await client.post("/chat/message", json={"content": "hello"})

    assert response.status_code == 200
    assert response.json()["model_used"] == "gemini-3.8-flash"
    assert gemini.models == ["gemini-3.8-flash"]


@pytest.mark.asyncio
async def test_chat_reports_409_when_the_pinned_model_fails(client, groq):
    """Not a 502. The stack is fine; the model the user chose is not, and
    saying so is the whole point of pinning one.
    """
    router = LLMRouter(primary=NamedProvider("gemini", fails=True), fallback=groq)
    router.pin(resolve_model("gemini 3.8 flash"))
    app.dependency_overrides[get_llm_router] = lambda: router
    try:
        response = await client.post("/chat/message", json={"content": "hello"})
    finally:
        app.dependency_overrides.pop(get_llm_router, None)

    assert response.status_code == 409
    assert "Gemini 3.8 Flash" in response.json()["detail"]
    assert groq.models == []
