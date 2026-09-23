"""Move everything the seeded dev user owns to a real, signed-in account.

Before auth existed every request was the dev user, so that is where all your
conversations, memories and documents live. Signing in gives you a new, empty
account; this hands the old data to it.

Usage (from services/backend), after signing in once so the account exists:
    ../../.venv/Scripts/python.exe -m scripts.claim_dev_data --email you@example.com
    ../../.venv/Scripts/python.exe -m scripts.claim_dev_data --email you@example.com --apply

Without --apply it runs the same statements and rolls them back, so the counts
it prints are exactly what --apply would do.

Deliberately NOT moved:
  * activity_logs -- the audit log records who did what. Rewriting its user
    ids would make it say something that did not happen.
  * permissions -- automation grants are trust given to a session. Carrying
    them to a new account would grant it trust nobody gave it; re-grant.

A memory or document the account already has (same content hash) stays with
the dev user: the unique index forbids a second copy, and the account already
has the content.
"""
import argparse
import asyncio
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.db import PgUuid, User

# Order matters only for document_chunks, which follow their documents.
STATEMENTS = {
    "conversations": "UPDATE conversations SET user_id = :to WHERE user_id = :src",
    "agent_runs": "UPDATE agent_runs SET user_id = :to WHERE user_id = :src",
    "memories": """
        UPDATE memories SET user_id = :to
        WHERE user_id = :src AND NOT EXISTS (
            SELECT 1 FROM memories m WHERE m.user_id = :to AND m.content_hash = memories.content_hash
        )""",
    "documents": """
        UPDATE documents SET user_id = :to
        WHERE user_id = :src AND NOT EXISTS (
            SELECT 1 FROM documents d WHERE d.user_id = :to AND d.content_hash = documents.content_hash
        )""",
    "document_chunks": """
        UPDATE document_chunks SET user_id = :to
        WHERE user_id = :src AND document_id IN (SELECT id FROM documents WHERE user_id = :to)""",
}


async def move_user_data(session: AsyncSession, src: UUID, to: UUID) -> dict[str, int]:
    """Reassign src's rows to `to` inside the caller's transaction. Returns row counts."""
    if src == to:
        raise ValueError("Source and target are the same user.")
    counts = {}
    for table, sql in STATEMENTS.items():
        # Typed binds: Postgres takes a UUID as-is, SQLite (tests) stores it as a string.
        statement = text(sql).bindparams(bindparam("src", type_=PgUuid), bindparam("to", type_=PgUuid))
        result = await session.execute(statement, {"src": src, "to": to})
        counts[table] = result.rowcount
    return counts


async def main(email: str, apply: bool) -> None:
    dev_id = get_settings().dev_user_id
    async with async_session_factory() as session:
        target = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if target is None:
            raise SystemExit(
                f"No CIPHER user with email {email}. Sign in to the app once first -- "
                "the account row is created on the first signed-in request."
            )

        counts = await move_user_data(session, dev_id, target.id)
        for table, n in counts.items():
            print(f"  {table:<16} {n}")

        if apply:
            await session.commit()
            print(f"Moved to {email} ({target.id}).")
        else:
            await session.rollback()
            print("Dry run -- nothing changed. Re-run with --apply to move it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the account you signed in with")
    parser.add_argument("--apply", action="store_true", help="commit (default is a dry run)")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.apply))
