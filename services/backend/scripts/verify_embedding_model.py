"""Opt-in, LIVE spike script for Phase 3 (long-term memory).

Answers two questions that MUST be verified against the real account before
anything downstream (the `Embedding` column, the migration, the embedder) is
written, because model IDs from documentation cannot be trusted here --
gemini-2.5-flash (the model named in docs/architecture.md) already returned
404 "no longer available to new users" on this account (see the docstring in
app/llm/gemini.py). The same staleness risk applies to embedding model names.

This deliberately lives in scripts/, not tests/, so normal `pytest` never
calls it -- it costs real free-tier quota and needs a human to read its
output, same rationale as scripts/persona_golden_set.py.

Usage (from services/backend):
    python -m scripts.verify_embedding_model
"""
import asyncio
import sys

from app.core.config import get_settings


async def _list_embedding_models(client) -> list[str]:
    models = []
    async for model in await client.aio.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if "embedContent" in actions:
            models.append(model.name)
    return models


async def run() -> None:
    from google import genai

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)

    print("=" * 70)
    print("Models supporting embedContent on this API key:")
    print("=" * 70)
    embedding_models = await _list_embedding_models(client)
    for name in embedding_models:
        print(f"  - {name}")
    if not embedding_models:
        print("  (none found -- check API key / SDK version)")
        return

    # Prefer the newest-looking candidate but let the human eyeball the list
    # above; this just picks *something* to test dimension truncation with.
    candidate = next(
        (m for m in embedding_models if "embedding-001" in m or "text-embedding" in m),
        embedding_models[0],
    )
    print()
    print(f"Testing candidate model: {candidate}")
    print("=" * 70)

    for dim in (768, None):
        label = f"output_dimensionality={dim}" if dim else "default dimensionality"
        try:
            kwargs = {}
            if dim is not None:
                from google.genai import types

                kwargs["config"] = types.EmbedContentConfig(output_dimensionality=dim)
            response = await client.aio.models.embed_content(
                model=candidate,
                contents=["I use Neovim, not VS Code."],
                **kwargs,
            )
            vector = response.embeddings[0].values
            print(f"[{label}] OK -- len(vector) = {len(vector)}")
        except Exception as exc:  # noqa: BLE001 - this is a diagnostic spike
            print(f"[{label}] FAILED -- {type(exc).__name__}: {exc}")

    print()
    print("Next: pick the model name + dimension printed above with a clean")
    print("768-length result, and pin both in app/memory/embedder.py.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run())


if __name__ == "__main__":
    main()
