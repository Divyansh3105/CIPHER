"""Per-user request limiting (Phase 8).

docs/architecture.md Section 14 asks for this to protect the free-tier API
quotas "from being exhausted by a bug or misuse". That framing matters: the
threat here is not an attacker, it is a retry loop in the frontend or a
runaway agent burning a day's Gemini allowance in a minute. This session
itself hit a 429 from exactly that kind of accumulation.

In-memory, single-process, and that is a real limitation rather than an
oversight. Section 14 offers "simple in-memory or Redis-based"; Redis is a
service to run, pay for and monitor, which contradicts the project's
zero-cost constraint for a benefit that only appears at multi-worker scale.
When there is more than one worker this limits per worker, which is stated
here and in the README rather than left to be discovered from a bill.

The window is a sliding one over recorded timestamps rather than a fixed
bucket. A fixed bucket lets a caller spend the whole allowance at 11:59:59
and the whole next allowance at 12:00:00, which is precisely the burst the
limit exists to prevent.
"""
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitRule:
    #: Requests allowed within the window.
    limit: int
    #: Window length in seconds.
    window_seconds: int


#: Chat is the expensive path: every non-trivial message costs a routing call
#: plus a reply, and possibly a tool call and an embedding on top. 20/minute
#: is far above human typing speed and far below what a retry loop achieves.
CHAT_RULE = RateLimitRule(limit=20, window_seconds=60)

#: Uploads embed a whole document. Deliberately tighter.
UPLOAD_RULE = RateLimitRule(limit=10, window_seconds=60)

#: Transcription used to share CHAT_RULE, and that was wrong twice over. It
#: is far cheaper -- one small Whisper call, against a chat turn's routing
#: call plus reply plus possibly a tool and an embedding -- and it is far
#: more frequent, because one spoken message is several utterances. Sharing
#: the chat number meant the cheap path exhausted the expensive path's
#: allowance. 40/minute is well above a person talking continuously and
#: still low enough that a stuck recorder is caught within seconds.
TRANSCRIBE_RULE = RateLimitRule(limit=40, window_seconds=60)


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, rule: RateLimitRule, *, now: float | None = None) -> tuple[bool, int]:
        """Record a request and say whether it is allowed.

        Returns (allowed, retry_after_seconds). A rejected request is NOT
        recorded -- counting rejections would extend the penalty every time
        a client retried, turning a brief overage into a lockout.
        """
        now = time.monotonic() if now is None else now
        window_start = now - rule.window_seconds
        hits = self._hits[key]

        while hits and hits[0] <= window_start:
            hits.popleft()

        if len(hits) >= rule.limit:
            retry_after = max(1, int(hits[0] + rule.window_seconds - now) + 1)
            return False, retry_after

        hits.append(now)
        return True, 0

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)


#: Process-wide, for the same reason the kill switch is: a per-request
#: limiter would count to one and never further.
_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency, overridable in tests."""
    return _limiter
