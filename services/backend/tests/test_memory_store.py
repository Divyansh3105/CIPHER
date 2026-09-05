"""Unit tests for the memory store's similarity search and dedup logic.

Uses the InMemoryVectorStore fake (tests/conftest.py) -- real ORM reads and
writes, real user scoping, real expiry filtering; only the `<=>` operator
itself is faked with Python cosine math. See app/memory/store.py for the
production PgVectorStore this stands in for.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.db import User
from tests.conftest import InMemoryVectorStore, _bag_of_words_vector


async def _seed_user(session, user_id):
    session.add(User(id=user_id, email=f"{user_id}@example.com", preferences={}))
    await session.flush()


async def test_search_returns_most_similar_first(db_session):
    store = InMemoryVectorStore()
    user_id = uuid4()
    await _seed_user(db_session, user_id)

    await store.add_if_new(
        db_session, user_id=user_id, content="I use Neovim as my editor.",
        embedding=_bag_of_words_vector("I use Neovim as my editor"),
        memory_type="long_term", source="explicit",
    )
    await store.add_if_new(
        db_session, user_id=user_id, content="My sister's name is Anya.",
        embedding=_bag_of_words_vector("My sister's name is Anya"),
        memory_type="semantic", source="extracted",
    )
    await db_session.commit()

    hits = await store.search(
        db_session, user_id=user_id, embedding=_bag_of_words_vector("what editor do I use Neovim"), limit=5
    )
    assert len(hits) == 2
    assert hits[0].content == "I use Neovim as my editor."
    assert hits[0].similarity >= hits[1].similarity


async def test_min_similarity_cutoff_excludes_weak_matches(db_session):
    store = InMemoryVectorStore()
    user_id = uuid4()
    await _seed_user(db_session, user_id)

    await store.add_if_new(
        db_session, user_id=user_id, content="Completely unrelated fact about pandas.",
        embedding=_bag_of_words_vector("Completely unrelated fact about pandas"),
        memory_type="semantic", source="extracted",
    )
    await db_session.commit()

    hits = await store.search(
        db_session, user_id=user_id, embedding=_bag_of_words_vector("what editor do I use"),
        limit=5, min_similarity=0.9,
    )
    assert hits == []


async def test_limit_caps_number_of_hits(db_session):
    store = InMemoryVectorStore()
    user_id = uuid4()
    await _seed_user(db_session, user_id)

    for i in range(5):
        await store.add_if_new(
            db_session, user_id=user_id, content=f"fact number {i} about Neovim",
            embedding=_bag_of_words_vector(f"fact number {i} about Neovim"),
            memory_type="semantic", source="extracted",
        )
    await db_session.commit()

    hits = await store.search(db_session, user_id=user_id, embedding=_bag_of_words_vector("Neovim"), limit=2)
    assert len(hits) == 2


async def test_semantic_dedup_skips_near_duplicate_insert(db_session):
    store = InMemoryVectorStore()
    user_id = uuid4()
    await _seed_user(db_session, user_id)

    vector = _bag_of_words_vector("I use Neovim, not VS Code")
    memory, deduplicated = await store.add_if_new(
        db_session, user_id=user_id, content="I use Neovim, not VS Code.",
        embedding=vector, memory_type="long_term", source="explicit",
    )
    assert deduplicated is False
    await db_session.commit()

    # A near-identical vector (simulated by reusing the same vector directly,
    # since the fake embedder is bag-of-words and "I use Neovim" alone won't
    # score >= MEMORY_DEDUP_SIMILARITY against the longer original phrase).
    dup, deduplicated_again = await store.add_if_new(
        db_session, user_id=user_id, content="I use Neovim, not VS Code!!",
        embedding=vector, memory_type="long_term", source="explicit",
    )
    assert deduplicated_again is True
    assert dup.id == memory.id


async def test_content_hash_prevents_exact_duplicate_rows(db_session):
    store = InMemoryVectorStore()
    user_id = uuid4()
    await _seed_user(db_session, user_id)

    vector = _bag_of_words_vector("remember my birthday is in June")
    first, first_dup = await store.add_if_new(
        db_session, user_id=user_id, content="My birthday is in June.",
        embedding=vector, memory_type="long_term", source="explicit",
    )
    await db_session.commit()

    second, second_dup = await store.add_if_new(
        db_session, user_id=user_id, content="  My   birthday   is in June.  ",  # whitespace variant
        embedding=vector, memory_type="long_term", source="explicit",
    )
    assert first_dup is False
    assert second_dup is True
    assert second.id == first.id


async def test_search_never_returns_another_users_memory(db_session):
    store = InMemoryVectorStore()
    user_a = uuid4()
    user_b = uuid4()
    await _seed_user(db_session, user_a)
    await _seed_user(db_session, user_b)

    await store.add_if_new(
        db_session, user_id=user_b, content="User B's secret project is called Phoenix.",
        embedding=_bag_of_words_vector("User B's secret project is called Phoenix"),
        memory_type="semantic", source="extracted",
    )
    await db_session.commit()

    hits = await store.search(
        db_session, user_id=user_a, embedding=_bag_of_words_vector("User B's secret project is called Phoenix"),
        limit=5, min_similarity=0.0,
    )
    assert hits == []


async def test_expired_memories_are_excluded(db_session):
    store = InMemoryVectorStore()
    user_id = uuid4()
    await _seed_user(db_session, user_id)

    memory, _ = await store.add_if_new(
        db_session, user_id=user_id, content="Temporary note about the demo.",
        embedding=_bag_of_words_vector("Temporary note about the demo"),
        memory_type="short_term", source="explicit",
    )
    memory.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    hits = await store.search(
        db_session, user_id=user_id, embedding=_bag_of_words_vector("Temporary note about the demo"),
        limit=5, min_similarity=0.0,
    )
    assert hits == []
