"""Integration test: POST /chat/message persists both turns and returns them.

Uses an in-memory SQLite DB (see app/models/db.py for why the ORM types are
dialect-portable) and fake LLM providers, so this needs no network or real
Postgres/Gemini/Groq access.
"""
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user_id
from app.core.database import Base, get_session
from app.llm.base import LLMMessage, LLMProvider, LLMResponse
from app.llm.router import LLMRouter, get_llm_router
from app.main import app
from app.models.db import User


class FakeProvider(LLMProvider):
    name = "fake-primary"

    async def agenerate(self, messages: list[LLMMessage]) -> LLMResponse:
        return LLMResponse(content="Acknowledged.", model="fake-model", provider=self.name)


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()

    async with session_factory() as session:
        session.add(User(id=user_id, email="test@example.com", name="Test User", preferences={}))
        await session.commit()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_llm_router] = lambda: LLMRouter(primary=FakeProvider(), fallback=FakeProvider())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


async def test_send_message_creates_conversation_and_replies(client):
    response = await client.post("/chat/message", json={"content": "Hello JARVIS"})

    assert response.status_code == 200
    body = response.json()
    assert body["message"]["content"] == "Acknowledged."
    assert body["message"]["role"] == "assistant"
    assert body["fell_back"] is False
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
