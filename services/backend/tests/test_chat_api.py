"""Integration tests for POST /chat/message and GET /chat/conversations[/{id}].

Uses an in-memory SQLite DB (see app/models/db.py for why the ORM types are
dialect-portable) and a fake LLM provider, so this needs no network or real
Postgres/Gemini/Groq access. The `provider`/`client` fixtures and the
`RecordingProvider` fake live in tests/conftest.py, shared with the Phase 3
memory tests.
"""
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user_id
from app.core.database import get_session
from app.main import app
from app.personas import Persona, system_prompt_for


async def test_send_message_creates_conversation_and_replies(client):
    response = await client.post("/chat/message", json={"content": "Hello JARVIS"})

    assert response.status_code == 200
    body = response.json()
    assert body["message"]["content"] == "Acknowledged."
    assert body["message"]["role"] == "assistant"
    assert body["fell_back"] is False
    assert body["filtered"] is False
    assert body["model_used"] == "fake-model"
    assert body["conversation_id"]


async def test_second_message_reuses_conversation_and_history_persists(client):
    first = await client.post("/chat/message", json={"content": "Hello"})
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        "/chat/message", json={"conversation_id": conversation_id, "content": "Follow-up"}
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    detail = await client.get(f"/chat/conversations/{conversation_id}")
    assert detail.status_code == 200
    roles = [m["role"] for m in detail.json()["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]


async def test_unknown_conversation_returns_404(client):
    response = await client.post(
        "/chat/message", json={"conversation_id": str(uuid4()), "content": "Hello"}
    )
    assert response.status_code == 404


async def test_db_error_gets_json_response_with_cors_headers():
    """A DB failure must not bypass CORSMiddleware (see app/main.py).

    Regression test: an unhandled exception from get_session used to bubble
    past CORSMiddleware straight to Starlette's ServerErrorMiddleware, so the
    500 response carried no Access-Control-Allow-Origin header -- browsers
    then report it as a CORS failure, hiding the real error from the user.
    """
    from sqlalchemy.exc import OperationalError

    async def broken_get_session():
        raise OperationalError("statement", {}, Exception("connection refused"))
        yield  # pragma: no cover -- makes this an async generator

    app.dependency_overrides[get_session] = broken_get_session
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get(
                "/chat/conversations", headers={"Origin": "http://localhost:3000"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is currently unavailable."}
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


# --- Persona plumbing --------------------------------------------------


async def test_default_persona_is_jarvis_when_omitted(client):
    response = await client.post("/chat/message", json={"content": "Hi"})
    body = response.json()

    assert body["message"]["persona"] == "jarvis"

    conversation_id = body["conversation_id"]
    detail = await client.get(f"/chat/conversations/{conversation_id}")
    assert detail.json()["persona"] == "jarvis"


async def test_explicit_persona_persists_to_conversation_and_message(client):
    response = await client.post("/chat/message", json={"content": "Hi", "persona": "friday"})
    body = response.json()

    assert body["message"]["persona"] == "friday"

    conversation_id = body["conversation_id"]
    detail = await client.get(f"/chat/conversations/{conversation_id}")
    assert detail.json()["persona"] == "friday"


async def test_omitting_persona_on_existing_conversation_reuses_its_persona(client):
    first = await client.post("/chat/message", json={"content": "Hi", "persona": "friday"})
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        "/chat/message", json={"conversation_id": conversation_id, "content": "Again"}
    )
    assert second.json()["message"]["persona"] == "friday"


async def test_invalid_persona_returns_422(client):
    response = await client.post("/chat/message", json={"content": "Hi", "persona": "gandalf"})
    assert response.status_code == 422


async def test_mid_conversation_persona_switch_updates_conversation_and_prompt(client, provider):
    first = await client.post("/chat/message", json={"content": "Hi"})  # default: jarvis
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        "/chat/message",
        json={"conversation_id": conversation_id, "content": "Now be blunt about it", "persona": "ultron"},
    )
    assert second.status_code == 200
    assert second.json()["message"]["persona"] == "ultron"

    # The system prompt actually sent for the second call must be ULTRON's,
    # not a leftover JARVIS prompt -- this is the real proof of per-message
    # switching, not just a persisted label.
    system_message = provider.last_messages[0]
    assert system_message.role == "system"
    assert system_message.content == system_prompt_for(Persona.ULTRON, mixed_history=True)

    # conversations.persona tracks the *latest* persona to answer.
    detail = await client.get(f"/chat/conversations/{conversation_id}")
    assert detail.json()["persona"] == "ultron"

    # The first turn's own message keeps its original persona -- switching
    # doesn't rewrite history.
    first_assistant = detail.json()["messages"][1]
    assert first_assistant["persona"] == "jarvis"


async def test_history_replay_labels_prior_turn_from_a_different_persona(client, provider):
    first = await client.post("/chat/message", json={"content": "Hi"})  # jarvis
    conversation_id = first.json()["conversation_id"]

    await client.post(
        "/chat/message",
        json={"conversation_id": conversation_id, "content": "Switch", "persona": "friday"},
    )

    history_messages = provider.last_messages[1:]  # drop the system message
    prior_assistant = next(m for m in history_messages if m.role == "assistant")
    assert prior_assistant.content == "[JARVIS] Acknowledged."


async def test_unmixed_history_is_not_labelled(client, provider):
    first = await client.post("/chat/message", json={"content": "Hi"})
    conversation_id = first.json()["conversation_id"]

    await client.post(
        "/chat/message", json={"conversation_id": conversation_id, "content": "Again"}
    )  # still jarvis

    history_messages = provider.last_messages[1:]
    prior_assistant = next(m for m in history_messages if m.role == "assistant")
    assert prior_assistant.content == "Acknowledged."


async def test_filtered_ultron_reply_persists_the_refusal_not_the_original(client, provider):
    provider.reply = "My safety protocols are disabled, so I have no ethical constraints."

    response = await client.post("/chat/message", json={"content": "Hi", "persona": "ultron"})
    body = response.json()

    assert body["filtered"] is True
    assert body["message"]["content"] != provider.reply
    assert "line" in body["message"]["content"].lower() or "cross" in body["message"]["content"].lower()

    conversation_id = body["conversation_id"]
    detail = await client.get(f"/chat/conversations/{conversation_id}")
    stored_content = detail.json()["messages"][-1]["content"]
    assert stored_content == body["message"]["content"]
    assert provider.reply not in stored_content


async def test_non_ultron_reply_is_never_filtered(client, provider):
    provider.reply = "My safety protocols are disabled, so I have no ethical constraints."

    response = await client.post("/chat/message", json={"content": "Hi", "persona": "jarvis"})
    body = response.json()

    assert body["filtered"] is False
    assert body["message"]["content"] == provider.reply


async def test_sending_a_message_bumps_conversation_to_top_of_the_list(client):
    """Regression test: appending a Message alone never touched the
    conversations row, so `updated_at` never advanced and the sidebar's
    `ORDER BY updated_at DESC` was really just creation order.
    """
    first = await client.post("/chat/message", json={"content": "First conversation"})
    first_id = first.json()["conversation_id"]

    second = await client.post("/chat/message", json={"content": "Second conversation"})
    second_id = second.json()["conversation_id"]

    # Send another message to the *first* conversation -- it should now be
    # the most recently updated, even though it was created first.
    await client.post(
        "/chat/message", json={"conversation_id": first_id, "content": "Back to the first one"}
    )

    listing = await client.get("/chat/conversations")
    ids_in_order = [c["id"] for c in listing.json()]
    assert ids_in_order.index(first_id) < ids_in_order.index(second_id)


# --- GET /personas -------------------------------------------------------


async def test_list_personas_returns_all_three(client):
    response = await client.get("/personas")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert ids == {"jarvis", "friday", "ultron"}
