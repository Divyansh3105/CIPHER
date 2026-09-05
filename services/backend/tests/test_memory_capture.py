"""Tests for hybrid memory capture: the deterministic "remember that..."
detector (unit tests, no DB) and the background writer wired into
POST /chat/message (integration tests, real MemoryWriter against fakes).

Background tasks run to completion before `client.post(...)` returns (see
app/memory/capture.py's module docstring and tests/conftest.py's
RecordingWriter docstring), so a GET /memory right after a chat call already
reflects whatever the writer did.
"""
import pytest

from app.llm.base import LLMMessage, LLMProvider, LLMResponse
from app.llm.router import LLMRouter
from app.memory.capture import MemoryWriter, detect_explicit


# --- Unit tests: the deterministic detector --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "remember that I use Neovim, not VS Code.",
        "Remember: my sister's name is Anya.",
        "don't forget that my flight is on the 5th.",
        "Keep in mind that I'm allergic to peanuts.",
        "Make a note that the client meeting moved to Friday.",
        "For future reference, I prefer tabs over spaces.",
        "note that I work best in the mornings.",
    ],
)
def test_detect_explicit_positive_cases(text):
    assert detect_explicit(text) is not None
    assert len(detect_explicit(text)) > 0


@pytest.mark.parametrize(
    "text",
    [
        "do you remember my birthday?",
        "Do you remember what I told you yesterday?",
        "What do you remember about me?",
        "Can you remember this for later?",
        "Would you remember to check the logs?",
        "Hello, how are you?",
        "",
        "   ",
    ],
)
def test_detect_explicit_negative_cases(text):
    assert detect_explicit(text) is None


def test_detect_explicit_strips_the_trigger_phrase():
    fact = detect_explicit("remember that I use Neovim, not VS Code.")
    assert fact == "I use Neovim, not VS Code."


# --- Integration: real MemoryWriter wired into POST /chat/message ----------


class ScriptedProvider(LLMProvider):
    """Returns a different canned reply depending on whether it was called
    for the chat turn (messages start with a system prompt) or for
    background fact extraction (a single user-role message) -- see
    app/memory/capture.py's `_extract_facts`, which never includes a system
    message.
    """

    name = "scripted"

    def __init__(self, chat_reply: str = "Acknowledged.", extraction_reply: str = "[]") -> None:
        self.chat_reply = chat_reply
        self.extraction_reply = extraction_reply
        self.calls: list[list[LLMMessage]] = []

    async def agenerate(self, messages: list[LLMMessage]) -> LLMResponse:
        self.calls.append(messages)
        is_extraction_call = not (messages and messages[0].role == "system")
        content = self.extraction_reply if is_extraction_call else self.chat_reply
        return LLMResponse(content=content, model="fake-model", provider=self.name)


@pytest.fixture
def scripted_provider():
    return ScriptedProvider()


@pytest.fixture
def memory_writer(scripted_provider, embedder, memory_store):
    """Overrides tests/conftest.py's no-op RecordingWriter for this module
    only, with a real MemoryWriter wired to fakes -- see that fixture's
    docstring for why every *other* test module keeps the no-op default.
    """
    router = LLMRouter(primary=scripted_provider, fallback=scripted_provider)
    return MemoryWriter(embedder=embedder, store=memory_store, llm_router=router)


async def test_explicit_remember_creates_a_memory_via_chat(client):
    response = await client.post(
        "/chat/message", json={"content": "remember that I use Neovim, not VS Code."}
    )
    assert response.status_code == 200

    listing = await client.get("/memory")
    contents = [m["content"] for m in listing.json()]
    assert "I use Neovim, not VS Code." in contents
    explicit_entry = next(m for m in listing.json() if m["content"] == "I use Neovim, not VS Code.")
    assert explicit_entry["source"] == "explicit"
    assert explicit_entry["memory_type"] == "long_term"


async def test_question_about_memory_does_not_create_a_memory(client):
    response = await client.post("/chat/message", json={"content": "do you remember my birthday?"})
    assert response.status_code == 200

    listing = await client.get("/memory")
    assert listing.json() == []


async def test_llm_extraction_creates_a_semantic_memory(client, scripted_provider):
    scripted_provider.extraction_reply = '["User prefers a quiet home office setup."]'

    response = await client.post(
        "/chat/message", json={"content": "I've been setting up my home office this week."}
    )
    assert response.status_code == 200

    listing = await client.get("/memory")
    contents = [m["content"] for m in listing.json()]
    assert "User prefers a quiet home office setup." in contents
    extracted_entry = next(m for m in listing.json() if m["content"] == "User prefers a quiet home office setup.")
    assert extracted_entry["source"] == "extracted"
    assert extracted_entry["memory_type"] == "semantic"


async def test_short_message_skips_extraction_entirely(client, scripted_provider):
    scripted_provider.extraction_reply = '["This should never be stored."]'

    response = await client.post("/chat/message", json={"content": "thanks a lot"})
    assert response.status_code == 200

    listing = await client.get("/memory")
    assert listing.json() == []


async def test_malformed_extraction_json_does_not_crash_the_request(client, scripted_provider):
    scripted_provider.extraction_reply = "this is not json at all"

    response = await client.post(
        "/chat/message", json={"content": "I've been setting up my home office this week."}
    )
    assert response.status_code == 200

    listing = await client.get("/memory")
    assert listing.json() == []


async def test_sending_the_same_explicit_fact_twice_does_not_duplicate(client):
    await client.post("/chat/message", json={"content": "remember that I use Neovim, not VS Code."})
    await client.post("/chat/message", json={"content": "remember that I use Neovim, not VS Code."})

    listing = await client.get("/memory")
    matches = [m for m in listing.json() if m["content"] == "I use Neovim, not VS Code."]
    assert len(matches) == 1
