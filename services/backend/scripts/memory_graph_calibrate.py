"""Opt-in, LIVE calibration for the memory graph's similarity floor.

MEMORY_GRAPH_MIN_SIMILARITY (app/memory/store.py) cannot be picked offline.
The test suite's FakeEmbedder is bag-of-words, which produces near-zero
similarity between memories that share no vocabulary -- real
gemini-embedding-001 output is nothing like that. Two unrelated facts about
the same person still score well above zero, because they are both short
first-person English sentences about one life. So a threshold that looks
generous against the fake can be so permissive against the real model that
every node links to every other and the layout carries no information.

This reads your actual memories, computes the real pairwise similarity
distribution in Postgres, and shows what each candidate threshold would do
to the graph: how many links survive, how many memories end up isolated, and
how big the largest connected component gets. Read it the way
scripts/memory_golden_set.py is read -- the numbers pick the threshold, not
taste.

What to look for: a threshold with few isolated nodes AND a largest
component well under 100% of the graph. A giant component containing
everything means the threshold is too low and the picture is a hairball; a
sea of isolated nodes means it is too high and the picture is dust.

Lives in scripts/, not tests/, because it needs a live database with real
embeddings in it. It only ever reads.

A live store of three or four memories cannot settle this on its own, so
--corpus runs the same analysis over the labelled 8-memory corpus in
scripts/memory_golden_set.py instead. That corpus is deliberately a set of
unrelated personal facts with exactly one genuinely related pair in it (the
two editor preferences), which makes it a usable separation test: a good
floor links that pair and nothing else.

Usage (from services/backend):
    python -m scripts.memory_graph_calibrate
    python -m scripts.memory_graph_calibrate --corpus
    python -m scripts.memory_graph_calibrate --neighbours 3
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.memory.embedder import build_embedder  # noqa: E402
from app.memory.store import (  # noqa: E402
    MEMORY_GRAPH_MIN_SIMILARITY,
    MEMORY_GRAPH_NEIGHBOURS,
)
from scripts.memory_golden_set import MEMORIES as CORPUS  # noqa: E402

# The one pair in CORPUS that a reader would call genuinely related: both are
# editor preferences. Everything else in that corpus is a different subject
# (a sibling, an allergy, a flight, a hobby), so a floor that links only this
# pair is separating signal from proximity.
CORPUS_RELATED_PAIRS = {frozenset((0, 4))}

CANDIDATES = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round(fraction * (len(values) - 1))))
    return sorted(values)[index]


def _components(node_ids: list, edges: list[tuple]) -> list[int]:
    """Connected-component sizes, via union-find."""
    parent = {node_id: node_id for node_id in node_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    sizes: dict = {}
    for node_id in node_ids:
        root = find(node_id)
        sizes[root] = sizes.get(root, 0) + 1
    return sorted(sizes.values(), reverse=True)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _report(labels: list[str], pairs: list[tuple], neighbours: int, related: set | None = None) -> None:
    """Print the distribution and the per-floor graph shape.

    `pairs` is (source_index, target_index, similarity, rank) over ordered
    pairs, matching what the SQL window function produces.
    """
    total = len(labels)
    all_scores = [p[2] for p in pairs]

    print("Pairwise similarity across the whole store (all ordered pairs):")
    for label, fraction in (("min", 0.0), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p95", 0.95), ("max", 1.0)):
        print(f"  {label:>6}  {_percentile(all_scores, fraction):.3f}")

    knn_scores = [p[2] for p in pairs if p[3] <= neighbours]
    print(f"\nSimilarity of each memory's top-{neighbours} nearest neighbours only:")
    for label, fraction in (("min", 0.0), ("p25", 0.25), ("median", 0.5), ("max", 1.0)):
        print(f"  {label:>6}  {_percentile(knn_scores, fraction):.3f}")

    print(f"\nGraph shape at each candidate floor (neighbours={neighbours}, {total} nodes):\n")
    header = f"  {'floor':>6}  {'links':>6}  {'isolated':>13}  {'largest component':>18}"
    if related is not None:
        header += f"  {'true':>5}  {'false':>6}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for floor in CANDIDATES:
        undirected = {
            (min(a, b), max(a, b))
            for a, b, similarity, rank in pairs
            if rank <= neighbours and similarity >= floor
        }
        connected = {node for pair in undirected for node in pair}
        isolated = total - len(connected)
        sizes = _components(list(range(total)) if related is not None else labels, list(undirected))
        largest = sizes[0] if sizes else 0

        row = (
            f"  {floor:>6.2f}  {len(undirected):>6}  "
            f"{isolated:>4} ({isolated / total:>5.0%})  {largest:>6} ({largest / total:>5.0%})"
        )
        if related is not None:
            drawn = {frozenset(pair) for pair in undirected}
            true_links = len(drawn & related)
            false_links = len(drawn - related)
            row += f"  {true_links:>3}/{len(related)}  {false_links:>6}"
        if abs(floor - MEMORY_GRAPH_MIN_SIMILARITY) < 1e-9:
            row += "  <- current"
        print(row)


async def corpus_mode(neighbours: int) -> int:
    """Same analysis over the labelled golden-set corpus, using real embeddings.

    Nothing is written: the corpus is embedded in memory and thrown away.
    """
    embedder = build_embedder(get_settings())
    print(f"Embedding {len(CORPUS)} corpus memories with {embedder.model}...\n")
    vectors = await embedder.aembed(list(CORPUS), task="document")

    pairs = []
    for i, source in enumerate(vectors):
        scored = sorted(
            ((_cosine(source, other), j) for j, other in enumerate(vectors) if j != i),
            reverse=True,
        )
        for rank, (similarity, j) in enumerate(scored, start=1):
            pairs.append((i, j, similarity, rank))

    _report(list(CORPUS), pairs, neighbours, related=CORPUS_RELATED_PAIRS)

    print("\n  true  = of the genuinely-related pairs, how many are drawn")
    print("  false = links drawn between memories a reader would call unrelated\n")
    print("Strongest pairs in the corpus:")
    top = sorted({(min(a, b), max(a, b)): sim for a, b, sim, _ in pairs}.items(), key=lambda kv: -kv[1])
    for (a, b), similarity in top[:6]:
        mark = "RELATED" if frozenset((a, b)) in CORPUS_RELATED_PAIRS else "       "
        print(f"  {similarity:.3f}  {mark}  {CORPUS[a][:38]:<40} <-> {CORPUS[b][:38]}")
    return 0


async def main(neighbours: int) -> int:
    settings = get_settings()
    async with async_session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, content
                FROM memories
                WHERE user_id = :user_id
                  AND embedding IS NOT NULL
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY created_at DESC
                """
            ),
            {"user_id": settings.dev_user_id},
        )
        memories = list(rows)

        if len(memories) < 3:
            print(f"Only {len(memories)} embedded memories in the live store.")
            print("Calibration needs more than that to say anything. Use the app for a")
            print("while (or add memories in the dashboard), then run this again.")
            return 1

        node_ids = [row.id for row in memories]
        print(f"{len(memories)} embedded memories for the dev user.\n")

        # Every pair, once, computed by pgvector.
        pair_rows = await session.execute(
            text(
                """
                WITH g AS (
                    SELECT id, embedding
                    FROM memories
                    WHERE user_id = :user_id
                      AND embedding IS NOT NULL
                      AND (expires_at IS NULL OR expires_at > now())
                )
                SELECT a.id AS source_id, b.id AS target_id,
                       1 - (a.embedding <=> b.embedding) AS similarity,
                       row_number() OVER (
                           PARTITION BY a.id ORDER BY a.embedding <=> b.embedding
                       ) AS rank
                FROM g a JOIN g b ON a.id <> b.id
                """
            ),
            {"user_id": settings.dev_user_id},
        )
        pairs = [(r.source_id, r.target_id, float(r.similarity), int(r.rank)) for r in pair_rows]

    _report(
        [str(row.id) for row in memories],
        [(str(a), str(b), sim, rank) for a, b, sim, rank in pairs],
        neighbours,
    )

    print(
        "\nPick the highest floor that still leaves most memories connected without\n"
        "one component swallowing the whole graph, then set\n"
        "MEMORY_GRAPH_MIN_SIMILARITY in app/memory/store.py to it."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neighbours", type=int, default=MEMORY_GRAPH_NEIGHBOURS)
    parser.add_argument(
        "--corpus",
        action="store_true",
        help="Analyse the labelled golden-set corpus with real embeddings instead of the live store.",
    )
    args = parser.parse_args()
    runner = corpus_mode(args.neighbours) if args.corpus else main(args.neighbours)
    raise SystemExit(asyncio.run(runner))
