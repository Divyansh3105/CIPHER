"""POST/GET/DELETE /documents, and GET /tools."""
from uuid import uuid4

import pytest

from app.api.deps import get_current_user_id
from app.main import app


def _upload(client, name="notes.txt", body=b"CIPHER runs on free tiers only."):
    return client.post("/documents", files={"file": (name, body, "text/plain")})


async def test_upload_returns_pending_and_queues_ingestion(client, document_ingestor):
    """Upload must not block on embedding.

    A 40-page PDF takes tens of seconds to embed; a request that waits reads
    as a hang. The cost is that the document exists before it is searchable,
    which is why `status` is in the response at all.
    """
    response = await _upload(client)

    assert response.status_code == 201
    body = response.json()
    assert body["deduplicated"] is False
    assert body["document"]["status"] == "pending"
    assert body["document"]["filename"] == "notes.txt"
    # The work was handed off, not done inline.
    assert len(document_ingestor.calls) == 1


async def test_unreadable_file_is_refused_at_upload_not_in_the_background(client, document_ingestor):
    """A password-protected PDF accepted now and failed silently later is a
    much worse experience than a refusal at the moment of upload.
    """
    response = await client.post(
        "/documents", files={"file": ("empty.txt", b"", "text/plain")}
    )

    assert response.status_code == 400
    assert document_ingestor.calls == []


async def test_unsupported_type_is_refused_with_the_supported_list(client):
    response = await client.post("/documents", files={"file": ("photo.png", b"\x89PNG", "image/png")})

    assert response.status_code == 415
    assert ".pdf" in response.json()["detail"]


async def test_the_same_file_twice_is_deduplicated(client, document_ingestor):
    """Two copies of every passage would compete for the same retrieval
    slots and produce answers that cite the same thing twice.
    """
    first = await _upload(client)
    second = await _upload(client, name="renamed.txt")

    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert len((await client.get("/documents")).json()) == 1
    # And no second ingestion was queued.
    assert len(document_ingestor.calls) == 1


async def test_whitespace_only_differences_still_deduplicate(client):
    """The same document re-saved by another writer has different bytes and
    identical content -- hashing the extracted text, not the upload, is what
    makes that a no-op.
    """
    await _upload(client, body=b"CIPHER runs on free tiers only.")
    second = await _upload(client, name="copy.txt", body=b"CIPHER  runs\non   free tiers only.")

    assert second.json()["deduplicated"] is True


async def test_list_documents_is_ordered_and_stable(client):
    """Newest first, and the same order every time.

    The stability half is the part with a bug behind it: created_at has
    one-second resolution, so two files uploaded in the same tick tie, and
    without an explicit tiebreaker their order flipped between requests for
    no reason a user could see.
    """
    await _upload(client, name="one.txt", body=b"first document about alpha")
    await _upload(client, name="two.txt", body=b"second document about beta")

    first = [d["filename"] for d in (await client.get("/documents")).json()]
    second = [d["filename"] for d in (await client.get("/documents")).json()]

    assert sorted(first) == ["one.txt", "two.txt"]
    assert first == second


async def test_delete_removes_the_document(client):
    created = await _upload(client)
    document_id = created.json()["document"]["id"]

    response = await client.delete(f"/documents/{document_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert (await client.get("/documents")).json() == []


async def test_deleting_an_unknown_document_is_404(client):
    assert (await client.delete(f"/documents/{uuid4()}")).status_code == 404


async def test_another_users_document_is_invisible_and_undeletable(client):
    created = await _upload(client)
    document_id = created.json()["document"]["id"]

    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    try:
        listing = await client.get("/documents")
        delete = await client.delete(f"/documents/{document_id}")
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)

    assert listing.json() == []
    # 404 rather than 403: don't reveal that it exists.
    assert delete.status_code == 404


# --- GET /tools ---------------------------------------------------------


async def test_tools_endpoint_reports_availability_not_just_existence(client, tool_registry):
    """A tool that is listed but always fails is worse than an absent one:
    the model keeps choosing it and every answer becomes an apology.
    """
    from app.tools.registry import ToolRegistry
    from app.tools.documents import DocumentSearchTool
    from tests.conftest import InMemoryDocumentStore, FakeEmbedder

    registry = ToolRegistry(
        [DocumentSearchTool(embedder=FakeEmbedder(), store=InMemoryDocumentStore())]
    )
    from app.tools.registry import get_tool_registry

    app.dependency_overrides[get_tool_registry] = lambda: registry
    try:
        with_no_documents = (await client.get("/tools")).json()
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)

    entry = next(t for t in with_no_documents if t["name"] == "document_search")
    assert entry["available"] is False
    assert "no documents" in entry["reason"]
