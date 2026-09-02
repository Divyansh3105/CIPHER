"""Shared FastAPI dependencies."""
from uuid import UUID

from app.core.config import get_settings


def get_current_user_id() -> UUID:
    """Phase 1 has no auth: every request is attributed to the seeded dev user.

    Swap this for a real JWT-derived user id when auth is added (pre-Phase 3).
    """
    return get_settings().dev_user_id
