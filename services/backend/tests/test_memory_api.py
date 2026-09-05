"""Integration tests for GET/POST/PATCH/DELETE /memory."""
from uuid import uuid4

from tests.conftest import FailingEmbedder


async def test_create_and_list_memory(client):
    create = await client.post("/memory", json={"content": "I use Neovim, not VS Code."})
    assert create.status_code == 201
    body = create.json()
    assert body["deduplicated"] is False
    assert body["memory"]["content"] == "I use Neovim, not VS Code."
    assert body["memory"]["embedding_pending"] is False

    listing = await client.get("/memory")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


async def test_duplicate_content_returns_deduplicated_true(client):
    first = await client.post("/memory", json={"content": "My birthday is in June."})
    second = await client.post("/memory", json={"content": "My birthday is in June."})

    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert second.json()["memory"]["id"] == first.json()["memory"]["id"]

    listing = await client.get("/memory")
    assert len(listing.json()) == 1


async def test_update_memory_changes_content(client):
    create = await client.post("/memory", json={"content": "I use Neovim."})
    memory_id = create.json()["memory"]["id"]

    updated = await client.patch(f"/memory/{memory_id}", json={"content": "I use Helix now."})
    assert updated.status_code == 200
    assert updated.json()["content"] == "I use Helix now."


async def test_delete_memory(client):
    create = await client.post("/memory", json={"content": "Delete me."})
    memory_id = create.json()["memory"]["id"]

    deleted = await client.delete(f"/memory/{memory_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": 1}

    listing = await client.get("/memory")
    assert listing.json() == []


async def test_delete_unknown_memory_returns_404(client):
    response = await client.delete(f"/memory/{uuid4()}")
    assert response.status_code == 404


async def test_update_unknown_memory_returns_404(client):
    response = await client.patch(f"/memory/{uuid4()}", json={"content": "x"})
    assert response.status_code == 404


async def test_delete_all_requires_confirm_query_param(client):
    await client.post("/memory", json={"content": "Keep or forget me."})

    without_confirm = await client.delete("/memory/all")
    assert without_confirm.status_code == 400

    with_confirm = await client.delete("/memory/all?confirm=true")
    assert with_confirm.status_code == 200
    assert with_confirm.json()["deleted"] == 1

    listing = await client.get("/memory")
    assert listing.json() == []


async def test_delete_all_route_is_not_shadowed_by_id_route(client):
    """Regression test: DELETE /memory/all must not be parsed as
    DELETE /memory/{memory_id} with memory_id="all" (which would 422).
    """
    response = await client.delete("/memory/all?confirm=true")
    assert response.status_code != 422


async def test_a_users_memory_is_invisible_and_inaccessible_to_another_user(client):
    """Even with the dev-user stub, /memory must be correctly scoped -- this
    is the whole point of keeping auth deferred but scoping everything by
    user_id (see the Phase 3 plan's decision #3).
    """
    create = await client.post("/memory", json={"content": "My secret project is Phoenix."})
    memory_id = create.json()["memory"]["id"]

    from app.api.deps import get_current_user_id
    from app.main import app

    app.dependency_overrides[get_current_user_id] = lambda: uuid4()

    listing = await client.get("/memory")
    assert listing.json() == []

    patch_response = await client.patch(f"/memory/{memory_id}", json={"content": "hacked"})
    assert patch_response.status_code == 404

    delete_response = await client.delete(f"/memory/{memory_id}")
    assert delete_response.status_code == 404


async def test_embedding_failure_marks_memory_pending(client):
    """A memory the user explicitly typed must still be saved even if
    embedding fails -- see app/memory/embedder.py's failure-policy table.
    """
    from app.main import app
    from app.memory.embedder import get_embedder

    app.dependency_overrides[get_embedder] = lambda: FailingEmbedder()

    response = await client.post("/memory", json={"content": "This will fail to embed."})
    assert response.status_code == 201
    assert response.json()["memory"]["embedding_pending"] is True

    listing = await client.get("/memory")
    assert listing.json()[0]["embedding_pending"] is True


async def test_list_memories_supports_substring_search(client):
    await client.post("/memory", json={"content": "I use Neovim, not VS Code."})
    await client.post("/memory", json={"content": "My sister's name is Anya."})

    results = await client.get("/memory", params={"q": "neovim"})
    assert len(results.json()) == 1
    assert "Neovim" in results.json()[0]["content"]
