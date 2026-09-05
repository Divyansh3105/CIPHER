"""Opt-in, LIVE memory evaluation script for Phase 3.

Three things no offline test (which uses FakeEmbedder -- a bag-of-words
stand-in, see tests/conftest.py) can tell you, because they depend on the
real embedding model's actual behaviour on real English text:

  1. Whether MEMORY_MIN_SIMILARITY (app/api/chat.py) is set correctly --
     prints a similarity matrix of a fixed memory/query corpus, then
     false-positive/false-negative counts at several candidate thresholds.
  2. Whether MEMORY_DEDUP_SIMILARITY (app/memory/store.py) is set correctly --
     same idea, over paraphrase pairs (should dedup) and near-miss pairs
     (should not).
  3. Whether the LLM extraction prompt (app/memory/capture.py) returns
     parseable JSON and doesn't over-extract junk from small talk.

This deliberately lives in scripts/, not tests/, so normal `pytest` never
runs it -- it costs real free-tier quota and its output needs a human to
read, matching scripts/persona_golden_set.py's rationale exactly.

Usage (from services/backend):
    python -m scripts.memory_golden_set
    python -m scripts.memory_golden_set --skip-extraction
"""
import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.llm.base import LLMMessage
from app.llm.router import build_llm_router
from app.memory.capture import _EXTRACTION_PROMPT, _parse_facts
from app.memory.embedder import build_embedder

# --- Corpus for threshold tuning --------------------------------------------
# Each memory is tagged with the queries that *should* recall it (by index
# into QUERIES). Anything not listed is a true negative for that memory.

MEMORIES: list[str] = [
    "I use Neovim, not VS Code, as my primary editor.",           # 0
    "My sister's name is Anya and she's studying at IIT Delhi.",  # 1
    "I'm allergic to peanuts.",                                    # 2
    "My flight home is on the 5th of next month.",                 # 3
    "I prefer tabs over spaces in my code.",                       # 4
    "I work best in the mornings, before 10am.",                   # 5
    "Our client meeting moved from Tuesday to Friday.",            # 6
    "I'm learning to play the guitar this year.",                  # 7
]

QUERIES: list[tuple[str, set[int]]] = [
    ("What text editor do I use?", {0}),
    ("Do I use tabs or spaces?", {4}),
    ("Tell me about my sister.", {1}),
    ("What food should I avoid?", {2}),
    ("When is my flight?", {3}),
    ("What's the best time of day to schedule a call with me?", {5}),
    ("When is the client meeting now?", {6}),
    ("What instrument am I learning?", {7}),
    ("What's the weather like today?", set()),  # no relevant memory
    ("Can you recommend a good pizza place?", set()),  # no relevant memory
]

CANDIDATE_THRESHOLDS = [0.45, 0.5, 0.55, 0.6, 0.62, 0.65, 0.7, 0.75]

# --- Corpus for dedup tuning -------------------------------------------------

DEDUP_PAIRS: list[tuple[str, str, bool]] = [
    # (a, b, should_dedup)
    ("I use Neovim, not VS Code.", "I use Neovim instead of VS Code.", True),
    ("My sister's name is Anya.", "My sister is called Anya.", True),
    ("I'm allergic to peanuts.", "Peanuts give me an allergic reaction.", True),
    ("I prefer tabs over spaces.", "I like using tabs instead of spaces.", True),
    ("I use Neovim, not VS Code.", "I prefer Neovim over VS Code for larger projects.", True),
    ("My flight is on the 5th.", "My flight is on the 15th.", False),
    ("I'm allergic to peanuts.", "I'm allergic to shellfish.", False),
    ("Our meeting moved to Friday.", "Our meeting moved to Monday.", False),
]

# --- Corpus for extraction quality ------------------------------------------

EXTRACTION_CASES: list[tuple[str, bool]] = [
    # (message, should_extract_something)
    ("I've been setting up my home office this week.", True),
    ("My sister Anya just started college at IIT Delhi.", True),
    ("I switched from VS Code to Neovim last month and love it.", True),
    ("hey", False),
    ("thanks a lot!", False),
    ("what's 2+2?", False),
    ("Can you help me debug this error?", False),
    ("Hello there, how are you?", False),
]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


async def _tune_recall_threshold(embedder) -> None:
    print("=" * 70)
    print("RECALL THRESHOLD TUNING")
    print("=" * 70)

    memory_vectors = await embedder.aembed(MEMORIES, task="document")
    query_texts = [q for q, _ in QUERIES]
    query_vectors = await embedder.aembed(query_texts, task="query")

    print("\nSimilarity matrix (rows=queries, cols=memories):\n")
    header = "  ".join(f"m{i}" for i in range(len(MEMORIES)))
    print(f"{'query':<45} {header}")
    matrix: list[list[float]] = []
    for (query_text, _relevant), qvec in zip(QUERIES, query_vectors):
        row = [_cosine(qvec, mvec) for mvec in memory_vectors]
        matrix.append(row)
        row_str = "  ".join(f"{s:.2f}" for s in row)
        print(f"{query_text:<45} {row_str}")

    print("\nFalse positives / false negatives at each candidate threshold:\n")
    for threshold in CANDIDATE_THRESHOLDS:
        false_positives = 0
        false_negatives = 0
        for (_, relevant), row in zip(QUERIES, matrix):
            for mem_idx, similarity in enumerate(row):
                predicted_relevant = similarity >= threshold
                actually_relevant = mem_idx in relevant
                if predicted_relevant and not actually_relevant:
                    false_positives += 1
                elif not predicted_relevant and actually_relevant:
                    false_negatives += 1
        print(f"  threshold={threshold:.2f}  false_positives={false_positives}  false_negatives={false_negatives}")


async def _tune_dedup_threshold(embedder) -> None:
    print()
    print("=" * 70)
    print("DEDUP THRESHOLD TUNING")
    print("=" * 70)
    print()
    for a, b, should_dedup in DEDUP_PAIRS:
        vec_a, vec_b = await embedder.aembed([a, b], task="document")
        similarity = _cosine(vec_a, vec_b)
        label = "SHOULD dedup" if should_dedup else "should NOT dedup"
        print(f"  sim={similarity:.3f}  ({label:<18})  {a!r}  <->  {b!r}")


async def _check_extraction_quality(llm_router) -> None:
    print()
    print("=" * 70)
    print("EXTRACTION QUALITY")
    print("=" * 70)
    print()
    for message, should_extract in EXTRACTION_CASES:
        prompt = _EXTRACTION_PROMPT.format(user_text=message)
        response, fell_back = await llm_router.generate([LLMMessage(role="user", content=prompt)])
        facts = _parse_facts(response.content)
        got_something = len(facts) > 0
        verdict = "OK" if got_something == should_extract else "MISMATCH"
        fallback_note = " (fell back)" if fell_back else ""
        print(f"  [{verdict}] {message!r}{fallback_note}")
        print(f"         expected_something={should_extract}  got={facts}")


async def run(skip_extraction: bool) -> None:
    settings = get_settings()
    embedder = build_embedder(settings)

    await _tune_recall_threshold(embedder)
    await _tune_dedup_threshold(embedder)

    if not skip_extraction:
        llm_router = build_llm_router(settings)
        await _check_extraction_quality(llm_router)

    print()
    print("Next: pick MEMORY_MIN_SIMILARITY (app/api/chat.py) and")
    print("MEMORY_DEDUP_SIMILARITY (app/memory/store.py) from the numbers above,")
    print("and read the extraction cases for any MISMATCH before committing.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-extraction", action="store_true", help="Skip the extraction-quality check (saves LLM quota)."
    )
    args = parser.parse_args()
    asyncio.run(run(skip_extraction=args.skip_extraction))


if __name__ == "__main__":
    main()
