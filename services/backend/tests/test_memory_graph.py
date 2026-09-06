"""Integration tests for GET /memory/graph.

The graph is a view onto data the dashboard already exposes, so the tests
that matter are about the graph's own invariants rather than the memories
themselves: no dangling edges, no duplicated mutual pairs, and no memory
silently missing from the picture.
"""
import pytest

from app.memory.store import MEMORY_GRAPH_MIN_SIMILARITY
from tests.conftest import FailingEmbedder
from app.memory.embedder import get_embedder
from app.main import app


# The fake embedder is bag-of-words over distinctive terms (see conftest), so
# memories sharing vocabulary land close together and memories that share
# nothing land far apart. Two deliberately separate clusters:
COFFEE = [
    "I drink espresso every morning without fail",
    "espresso beans from the roaster down the road every morning",
    "my espresso machine needs descaling every morning",
]
TRAVEL = [
    "my passport expires next December before the Lisbon trip",
    "the Lisbon trip is booked for next December",
]


async def _seed(client, contents):
    ids = []
    for content in contents:
        response = await client.post("/memory", json={"content": content})
        assert response.status_code == 201
        ids.append(response.json()["memory"]["id"])
    return ids


async def test_graph_route_is_not_shadowed_by_the_id_route(client):
    """Regression guard, the same one DELETE /memory/all needed: GET
    /memory/graph must not be parsed as GET /memory/{memory_id} with
    memory_id="graph", which would 422 on the UUID conversion.
    """
    response = await client.get("/memory/graph")
    assert response.status_code == 200


async def test_empty_store_returns_an_empty_graph(client):
    body = (await client.get("/memory/graph")).json()

    assert body["nodes"] == []
    assert body["links"] == []
    assert body["total"] == 0
    assert body["truncated"] is False


async def test_a_single_memory_is_a_node_with_no_links(client):
    await _seed(client, COFFEE[:1])

    body = (await client.get("/memory/graph")).json()

    assert len(body["nodes"]) == 1
    assert body["links"] == []


async def test_every_link_endpoint_exists_in_the_node_list(client):
    """A dangling edge does not merely omit a line -- force-directed layouts
    index nodes by id, so an edge pointing at a missing node breaks the whole
    render. This is the invariant the frontend actually depends on.
    """
    await _seed(client, COFFEE + TRAVEL)

    body = (await client.get("/memory/graph")).json()
    node_ids = {n["id"] for n in body["nodes"]}

    assert body["links"], "expected the related memories to link"
    for link in body["links"]:
        assert link["source"] in node_ids
        assert link["target"] in node_ids


async def test_a_mutual_pair_produces_exactly_one_link(client):
    """KNN is asymmetric, so the same pair can be emitted twice (once from
    each end). Undirected rendering would then draw two overlapping lines and
    double that pair's pull on the layout.
    """
    await _seed(client, COFFEE + TRAVEL)

    links = (await client.get("/memory/graph")).json()["links"]

    unordered = [frozenset((link["source"], link["target"])) for link in links]
    assert len(unordered) == len(set(unordered))
    # And no self-links.
    assert all(len(pair) == 2 for pair in unordered)


async def test_related_memories_link_and_unrelated_ones_do_not(client):
    coffee_ids, travel_ids = await _seed(client, COFFEE), await _seed(client, TRAVEL)

    links = (await client.get("/memory/graph")).json()["links"]
    pairs = [frozenset((link["source"], link["target"])) for link in links]

    def crosses_clusters(pair):
        return any(i in pair for i in coffee_ids) and any(i in pair for i in travel_ids)

    assert pairs, "expected within-cluster links"
    assert not any(crosses_clusters(p) for p in pairs)


async def test_similarity_floor_is_applied(client):
    await _seed(client, COFFEE + TRAVEL)

    permissive = (await client.get("/memory/graph?min_similarity=0.0")).json()
    strict = (await client.get("/memory/graph?min_similarity=0.99")).json()

    assert len(strict["links"]) < len(permissive["links"])
    assert all(link["similarity"] >= 0.99 for link in strict["links"])


async def test_neighbours_caps_links_per_node(client):
    await _seed(client, COFFEE + TRAVEL)

    body = (await client.get("/memory/graph?neighbours=1&min_similarity=0.0")).json()

    # Each node contributes at most `neighbours` edges, and mutual pairs
    # collapse, so the total can never exceed nodes * neighbours.
    assert len(body["links"]) <= len(body["nodes"]) * 1
    assert body["neighbours"] == 1


async def test_limit_reports_truncation_honestly(client):
    await _seed(client, COFFEE + TRAVEL)

    body = (await client.get("/memory/graph?limit=2")).json()

    assert len(body["nodes"]) == 2
    assert body["total"] == 5
    assert body["truncated"] is True


async def test_untruncated_graph_says_so(client):
    await _seed(client, COFFEE)

    body = (await client.get("/memory/graph")).json()

    assert body["total"] == len(body["nodes"]) == 3
    assert body["truncated"] is False


async def test_an_unembedded_memory_is_an_isolated_node_not_a_missing_one(client):
    """A memory stored while embedding was down is real and the user can see
    it in the dashboard. Dropping it from the graph would make the two
    disagree about how much the assistant remembers; showing it unconnected
    and flagged says the true thing.
    """
    await _seed(client, COFFEE)

    app.dependency_overrides[get_embedder] = lambda: FailingEmbedder()
    try:
        created = await client.post("/memory", json={"content": "a fact stored while embedding was down"})
    finally:
        app.dependency_overrides.pop(get_embedder, None)
    pending_id = created.json()["memory"]["id"]
    assert created.json()["memory"]["embedding_pending"] is True

    body = (await client.get("/memory/graph?min_similarity=0.0")).json()

    pending_node = next(n for n in body["nodes"] if n["id"] == pending_id)
    assert pending_node["embedding_pending"] is True
    assert not any(pending_id in (link["source"], link["target"]) for link in body["links"])


async def test_graph_is_scoped_to_the_current_user(client, monkeypatch):
    from uuid import uuid4

    from app.api.deps import get_current_user_id

    await _seed(client, COFFEE)
    mine = {n["id"] for n in (await client.get("/memory/graph")).json()["nodes"]}
    assert mine

    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    try:
        other = (await client.get("/memory/graph")).json()
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)

    assert other["nodes"] == []
    assert other["total"] == 0


@pytest.mark.parametrize(
    "query",
    ["limit=0", "neighbours=0", "neighbours=99", "min_similarity=-0.1", "min_similarity=1.5"],
)
async def test_out_of_range_parameters_are_rejected(client, query):
    assert (await client.get(f"/memory/graph?{query}")).status_code == 422


async def test_the_default_floor_is_derived_from_the_store_not_hardcoded(client):
    """A fixed floor does not survive real embeddings.

    Measured against real gemini-embedding-001 output, every pair in the
    labelled corpus scored between 0.641 and 0.809, and unrelated facts
    outscored some related ones -- so the graph derives its floor from each
    store's own mean and spread instead. This pins that the default path
    actually does that, and reports honestly that it did.
    """
    await _seed(client, COFFEE + TRAVEL)

    body = (await client.get("/memory/graph")).json()

    assert body["adaptive"] is True
    # Above the absolute guard, and not equal to it -- that constant is a
    # floor of last resort, not the operating value.
    assert body["min_similarity"] > MEMORY_GRAPH_MIN_SIMILARITY
    assert all(link["similarity"] >= body["min_similarity"] for link in body["links"])


async def test_an_explicit_floor_overrides_the_adaptive_one(client):
    await _seed(client, COFFEE + TRAVEL)

    body = (await client.get("/memory/graph?min_similarity=0.42")).json()

    assert body["adaptive"] is False
    assert body["min_similarity"] == 0.42
    assert all(link["similarity"] >= 0.42 for link in body["links"])


async def test_the_adaptive_floor_tracks_the_store_it_is_given(client):
    """The whole point of deriving the floor: two stores with different
    spreads must not be judged by the same number.
    """
    await _seed(client, COFFEE)
    tight = (await client.get("/memory/graph")).json()["min_similarity"]

    await _seed(client, TRAVEL + ["something else entirely about tax paperwork"])
    mixed = (await client.get("/memory/graph")).json()["min_similarity"]

    assert tight != mixed
