"""Supabase JWT auth (app/api/deps.py).

Signs real ES256 tokens with a throwaway key and swaps only the key lookup,
so the decode path under test is the one that runs in production.
"""
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.api import deps
from app.core.config import get_settings
from app.main import app
from app.models.db import User

KEY = ec.generate_private_key(ec.SECP256R1())


def _token(sub, *, key=KEY, aud="authenticated", exp_in=3600, email="someone@example.com"):
    claims = {"sub": str(sub), "aud": aud, "exp": int(time.time()) + exp_in, "email": email}
    return jwt.encode(claims, key, algorithm="ES256")


@pytest.fixture
def real_auth(client, monkeypatch):
    """The `client` fixture, minus its fixed-user override."""
    app.dependency_overrides.pop(deps.get_current_user_id, None)
    monkeypatch.setattr(
        deps, "_jwks_client",
        lambda: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=KEY.public_key())),
    )
    monkeypatch.setattr(get_settings(), "auth_disabled", False)
    deps._provisioned.clear()
    return client


async def _get(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.get("/chat/conversations", headers=headers)


async def test_no_token_is_refused(real_auth):
    assert (await _get(real_auth)).status_code == 401


async def test_every_router_is_guarded_not_just_the_ones_that_read_the_user(real_auth):
    # /models/active never reads a user id, and it changes the model for everyone.
    assert (await real_auth.post("/models/active", json={"spoken": "gemini"})).status_code == 401


async def test_health_stays_public(real_auth):
    assert (await real_auth.get("/health")).status_code == 200


@pytest.mark.parametrize(
    "bad",
    [
        lambda: _token(uuid4(), key=ec.generate_private_key(ec.SECP256R1())),  # forged
        lambda: _token(uuid4(), exp_in=-60),  # expired
        lambda: _token(uuid4(), aud="anon"),  # not a user session
        lambda: _token("not-a-uuid"),
    ],
    ids=["forged", "expired", "wrong-audience", "bad-subject"],
)
async def test_a_bad_token_is_refused(real_auth, bad):
    assert (await _get(real_auth, bad())).status_code == 401


async def test_a_bad_token_is_refused_even_with_auth_disabled(real_auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_disabled", True)
    forged = _token(uuid4(), key=ec.generate_private_key(ec.SECP256R1()))
    assert (await _get(real_auth, forged)).status_code == 401


async def test_auth_disabled_only_covers_a_missing_token(real_auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_disabled", True)
    assert (await _get(real_auth)).status_code != 401


async def test_first_sign_in_creates_the_user_row(real_auth):
    from app.core.database import get_session

    user_id = uuid4()
    response = await _get(real_auth, _token(user_id, email="new@example.com"))
    assert response.status_code == 200

    async for session in app.dependency_overrides[get_session]():
        row = await session.get(User, user_id)
    assert row is not None and row.email == "new@example.com"


async def test_users_cannot_see_each_others_conversations(real_auth):
    alice, bob = uuid4(), uuid4()
    await _get(real_auth, _token(alice, email="alice@example.com"))
    await _get(real_auth, _token(bob, email="bob@example.com"))

    created = await real_auth.post(
        "/chat/message",
        json={"content": "hello"},
        headers={"Authorization": f"Bearer {_token(alice, email='alice@example.com')}"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]

    bobs = await _get(real_auth, _token(bob, email="bob@example.com"))
    assert conversation_id not in [c["id"] for c in bobs.json()]
