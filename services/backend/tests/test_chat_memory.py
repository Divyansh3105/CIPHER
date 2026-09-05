"""Integration tests for memory retrieval wired into POST /chat/message.

Uses the FakeEmbedder from tests/conftest.py -- a bag-of-words fake, not a
real semantic model, so tests that need retrieval to actually fire lower
MEMORY_MIN_SIMILARITY via monkeypatch rather than relying on the fake to hit
the real, Gemini-tuned 0.62 threshold on generic English sentences.
"""
from app.api import chat as chat_module


async def test_recalled_memory_appears_in_system_prompt(client, provider, monkeypatch):
    monkeypatch.setattr(chat_module, "MEMORY_MIN_SIMILARITY", 0.1)

    await client.post("/memory", json={"content": "I use Neovim, not VS Code."})

    response = await client.post(
        "/chat/message", json={"content": "What editor do I use? Remind me about Neovim."}
    )
    assert response.status_code == 200

    system_message = provider.last_messages[0]
    assert system_message.role == "system"
    assert "what you remember about this user" in system_message.content.lower()
    assert "Neovim" in system_message.content


async def test_unrelated_memory_is_not_recalled(client, provider):
    """Default MEMORY_MIN_SIMILARITY (untouched) should exclude a memory that
    shares essentially no distinctive words with the query.
    """
    await client.post("/memory", json={"content": "My sister's name is Anya."})

    response = await client.post(
        "/chat/message", json={"content": "What's a good recipe for banana bread?"}
    )
    assert response.status_code == 200

    system_message = provider.last_messages[0]
    assert "what you remember about this user" not in system_message.content.lower()


async def test_recalled_memories_persist_and_survive_reload(client, monkeypatch):
    monkeypatch.setattr(chat_module, "MEMORY_MIN_SIMILARITY", 0.1)

    await client.post("/memory", json={"content": "I use Neovim, not VS Code."})
    response = await client.post(
        "/chat/message", json={"content": "What editor do I use? Remind me about Neovim."}
    )
    body = response.json()
    assert len(body["message"]["recalled_memories"]) >= 1
    assert body["message"]["recalled_memories"][0]["content"] == "I use Neovim, not VS Code."

    conversation_id = body["conversation_id"]
    detail = await client.get(f"/chat/conversations/{conversation_id}")
    stored_message = detail.json()["messages"][-1]
    assert stored_message["recalled_memories"] == body["message"]["recalled_memories"]


async def test_embedding_failure_during_chat_does_not_break_the_reply(client):
    from app.main import app
    from app.memory.embedder import get_embedder
    from tests.conftest import FailingEmbedder

    app.dependency_overrides[get_embedder] = lambda: FailingEmbedder()

    response = await client.post("/chat/message", json={"content": "Hello there"})
    assert response.status_code == 200
    assert response.json()["message"]["recalled_memories"] == []


async def test_persona_framing_line_present_when_memories_recalled(client, provider, monkeypatch):
    monkeypatch.setattr(chat_module, "MEMORY_MIN_SIMILARITY", 0.1)
    from app.personas import PERSONAS, Persona

    await client.post("/memory", json={"content": "I use Neovim, not VS Code."})
    response = await client.post(
        "/chat/message",
        json={"content": "What editor do I use? Remind me about Neovim.", "persona": "jarvis"},
    )
    assert response.status_code == 200

    system_message = provider.last_messages[0]
    assert PERSONAS[Persona.JARVIS].memory_framing in system_message.content
