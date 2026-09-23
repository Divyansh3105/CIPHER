"""Shared FastAPI dependencies."""
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.models.db import User

_bearer = HTTPBearer(auto_error=False)

#: Users already known to have a row in public.users, so the lookup below
#: runs once per user per process rather than once per request.
_provisioned: set[UUID] = set()


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    """Supabase's public signing keys, fetched once and cached by PyJWT.

    Asymmetric (ES256) verification means the backend holds no secret that
    can mint tokens -- it can only check them.
    """
    settings = get_settings()
    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL is not set, so no token can be verified.")
    return jwt.PyJWKClient(
        f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
        headers={"apikey": settings.supabase_key or ""},
    )


def _verify(token: str) -> dict:
    """Blocking (the first call fetches the key set), so it runs in a thread."""
    key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=["ES256", "RS256"], audience="authenticated")


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> UUID:
    """The Supabase user this request is from.

    A request with no token is refused unless AUTH_DISABLED is set, in which
    case it is the seeded dev user -- that is for local tools like preflight,
    and it never applies to a token that fails to verify. A bad token is a
    401 in every mode, not a quiet fall back to somebody else's data.
    """
    if credentials is None:
        if get_settings().auth_disabled:
            return get_settings().dev_user_id
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required.")

    try:
        claims = await run_in_threadpool(_verify, credentials.credentials)
        user_id = UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid session: {exc}") from exc

    if user_id not in _provisioned:
        await _ensure_user_row(session, user_id, claims.get("email"))
        _provisioned.add(user_id)
    return user_id


async def _ensure_user_row(session: AsyncSession, user_id: UUID, email: str | None) -> None:
    """Every table's user_id is a foreign key to public.users, not auth.users.

    The first request after a sign-up creates the row. A page load sends
    several requests at once, so two of them can race to insert it; the loser
    sees an IntegrityError and finds the winner's row. If the row is still
    missing, the conflict was the email belonging to a different user id.
    """
    if await session.get(User, user_id) is not None:
        return
    session.add(User(id=user_id, email=email or f"{user_id}@users.cipher", preferences={}))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if await session.get(User, user_id) is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{email} is already registered to a different CIPHER user.",
            )
