"""Seed the single Phase 1 dev user (auth is deferred -- see app/api/deps.py).

Usage (from services/backend):
    ../../.venv/Scripts/python.exe -m scripts.seed_dev_user
"""
import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.db import User

DEV_EMAIL = "dev@cipher.local"
DEV_NAME = "Dev User"


async def main() -> None:
    settings = get_settings()
    async with async_session_factory() as session:
        existing = await session.get(User, settings.dev_user_id)
        if existing is not None:
            print(f"Dev user already exists: {existing.id} <{existing.email}>")
            return

        by_email = await session.execute(select(User).where(User.email == DEV_EMAIL))
        if by_email.scalar_one_or_none() is not None:
            print(f"A user with email {DEV_EMAIL} already exists under a different id; not creating a duplicate.")
            return

        user = User(id=settings.dev_user_id, email=DEV_EMAIL, name=DEV_NAME, preferences={})
        session.add(user)
        await session.commit()
        print(f"Created dev user: {user.id} <{user.email}>")


if __name__ == "__main__":
    asyncio.run(main())
