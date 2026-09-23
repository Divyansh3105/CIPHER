"""scripts/claim_dev_data.py: moving the dev user's data to a real account."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.db import Conversation, Document, DocumentChunk, Memory, User
from scripts.claim_dev_data import move_user_data


def _doc(user_id, content_hash):
    return Document(
        user_id=user_id, filename=f"{content_hash}.txt", content_type="text/plain",
        size_bytes=1, content_hash=content_hash, status="ready",
    )


async def test_moves_everything_except_what_the_account_already_has(db_session):
    dev, me = uuid4(), uuid4()
    db_session.add_all([
        User(id=dev, email="dev@cipher.local", preferences={}),
        User(id=me, email="me@example.com", preferences={}),
    ])
    await db_session.flush()

    unique_doc, dup_doc, my_doc = _doc(dev, "a"), _doc(dev, "b"), _doc(me, "b")
    db_session.add_all([
        Conversation(user_id=dev),
        Memory(user_id=dev, content="likes tea", content_hash="tea"),
        Memory(user_id=dev, content="lives in Delhi", content_hash="delhi"),
        Memory(user_id=me, content="lives in Delhi", content_hash="delhi"),
        unique_doc, dup_doc, my_doc,
    ])
    await db_session.flush()
    db_session.add_all([
        DocumentChunk(document_id=unique_doc.id, user_id=dev, content="x", chunk_index=0),
        DocumentChunk(document_id=dup_doc.id, user_id=dev, content="y", chunk_index=0),
    ])
    await db_session.flush()

    counts = await move_user_data(db_session, dev, me)

    assert counts == {
        "conversations": 1, "agent_runs": 0, "memories": 1, "documents": 1, "document_chunks": 1,
    }
    # The duplicate document's chunk stays with the document it belongs to.
    left = (await db_session.execute(select(DocumentChunk.document_id).where(DocumentChunk.user_id == dev))).scalars()
    assert list(left) == [dup_doc.id]


async def test_refuses_to_move_a_user_onto_itself(db_session):
    same = uuid4()
    with pytest.raises(ValueError):
        await move_user_data(db_session, same, same)
