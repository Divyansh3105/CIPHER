"""POST /voice/transcribe -- the speech backend that works outside Chrome."""
import pytest

from app.api.voice import MIN_AUDIO_BYTES


async def test_a_clip_too_short_to_contain_speech_returns_empty_not_an_error(client):
    """A recorder that opened and shut on a stray click is a non-event.

    Surfacing it as a failure would put an error banner on screen for doing
    nothing, and the mic stays on, so it would happen repeatedly.
    """
    response = await client.post(
        "/voice/transcribe",
        files={"audio": ("utterance.webm", b"\x00" * 32, "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == ""


async def test_an_oversized_clip_is_refused(client):
    response = await client.post(
        "/voice/transcribe",
        files={"audio": ("utterance.webm", b"\x00" * (9 * 1024 * 1024), "audio/webm")},
    )

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


async def test_transcription_is_rate_limited(client, monkeypatch):
    """A stuck recorder must not be able to spend the whole quota, and must
    not eat the chat allowance either -- it has its own key.
    """
    from app.core.ratelimit import CHAT_RULE, get_rate_limiter

    from app.api.deps import get_current_user_id
    from app.main import app

    # The client fixture overrides the current user with a random id, so the
    # limiter key has to come from the override rather than from settings.
    user_id = app.dependency_overrides[get_current_user_id]()

    limiter = get_rate_limiter()
    # Fill this endpoint's bucket directly; the payload below is under the
    # minimum, so no real transcription is attempted.
    for _ in range(CHAT_RULE.limit):
        limiter.check(f"transcribe:{user_id}", CHAT_RULE)

    response = await client.post(
        "/voice/transcribe",
        files={"audio": ("utterance.webm", b"\x00" * 32, "audio/webm")},
    )

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    # Chat is on a separate key and is unaffected.
    assert (await client.post("/chat/message", json={"content": "still works"})).status_code == 200


def test_the_minimum_is_small_enough_for_a_real_utterance():
    """Guard against raising MIN_AUDIO_BYTES to something that silently
    swallows short answers like "yes" or "stop".
    """
    assert MIN_AUDIO_BYTES < 4000
