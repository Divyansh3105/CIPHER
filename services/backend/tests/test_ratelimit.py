"""Per-user rate limiting.

The threat this defends against is not an attacker -- it is a retry loop or a
runaway agent burning a day's free-tier allowance in a minute. This session
hit a real 429 from exactly that kind of accumulation, which is what makes
the limiter worth having rather than theoretical.
"""
import pytest

from app.core.ratelimit import CHAT_RULE, RateLimiter, RateLimitRule, get_rate_limiter

RULE = RateLimitRule(limit=3, window_seconds=60)


def test_requests_under_the_limit_are_allowed():
    limiter = RateLimiter()

    for _ in range(RULE.limit):
        allowed, _ = limiter.check("user", RULE)
        assert allowed is True


def test_the_limit_is_enforced():
    limiter = RateLimiter()
    for _ in range(RULE.limit):
        limiter.check("user", RULE)

    allowed, retry_after = limiter.check("user", RULE)

    assert allowed is False
    assert retry_after > 0


def test_a_rejected_request_is_not_counted():
    """Counting rejections would extend the penalty every time a client
    retried, turning a brief overage into a lockout.
    """
    limiter = RateLimiter()
    for _ in range(RULE.limit):
        limiter.check("user", RULE, now=1000.0)

    # Hammer it while blocked, then step just past the window.
    for _ in range(50):
        limiter.check("user", RULE, now=1001.0)

    allowed, _ = limiter.check("user", RULE, now=1000.0 + RULE.window_seconds + 1)
    assert allowed is True


def test_the_window_slides_rather_than_resetting():
    """A fixed bucket lets a caller spend the whole allowance at the end of
    one window and the whole next allowance immediately after -- exactly the
    burst the limit exists to prevent.
    """
    limiter = RateLimiter()
    for offset in range(RULE.limit):
        assert limiter.check("user", RULE, now=1000.0 + offset)[0] is True

    # One second after the first hit expires, exactly one slot frees up.
    just_after_first_expires = 1000.0 + RULE.window_seconds + 0.1
    assert limiter.check("user", RULE, now=just_after_first_expires)[0] is True
    assert limiter.check("user", RULE, now=just_after_first_expires)[0] is False


def test_limits_are_per_key():
    limiter = RateLimiter()
    for _ in range(RULE.limit):
        limiter.check("alice", RULE)

    assert limiter.check("alice", RULE)[0] is False
    assert limiter.check("bob", RULE)[0] is True


async def test_chat_returns_429_with_retry_after(client):
    """The header matters: a client that knows when to retry does not spin."""
    limiter = get_rate_limiter()
    for _ in range(CHAT_RULE.limit):
        limiter.check(f"chat:{(await client.get('/personas')).status_code}", CHAT_RULE)

    # Fill the real key by sending messages.
    for _ in range(CHAT_RULE.limit):
        await client.post("/chat/message", json={"content": "hello"})

    response = await client.post("/chat/message", json={"content": "one too many"})

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert "Try again in" in response.json()["detail"]


async def test_the_limiter_runs_before_any_work(client, provider):
    """A limiter that runs after the expensive part protects nothing."""
    for _ in range(CHAT_RULE.limit):
        await client.post("/chat/message", json={"content": "hello"})
    calls_before = len(provider.calls)

    await client.post("/chat/message", json={"content": "one too many"})

    assert len(provider.calls) == calls_before
