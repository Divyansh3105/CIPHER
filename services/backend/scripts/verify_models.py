"""Prove every model in app/llm/registry.py is actually reachable.

Run this before adding a model to the registry, and again whenever a
provider deprecates something:

    .venv/Scripts/python.exe services/backend/scripts/verify_models.py
    .venv/Scripts/python.exe services/backend/scripts/verify_models.py --discover

Why a script and not a unit test: it makes real, billable calls with the
real keys in .env, so it must never run in CI or in `pytest`.

The reason it exists at all is that a provider's own model list is not
evidence. On this project's Gemini key, `client.models.list()` advertises
`gemini-2.5-pro` and `gemini-2.5-flash-lite`; a real generate call to either
returns 404 "no longer available to new users". A registry built from the
list endpoint would have shipped two models that cannot answer, and the
runtime model swap would have failed in a way that looks like our bug.

`--discover` prints everything the keys can see, marking which registry
entries are missing, so you can find candidates. Only add what this script
reports OK.
"""
import argparse
import asyncio
import sys
from pathlib import Path

# services/backend/scripts/verify_models.py -> services/backend on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.llm.base import LLMMessage, LLMProviderError  # noqa: E402
from app.llm.gemini import GeminiProvider  # noqa: E402
from app.llm.groq import GroqProvider  # noqa: E402
from app.llm.registry import MODEL_REGISTRY  # noqa: E402

PROBE = [LLMMessage(role="user", content="Reply with the single word: ok")]


async def verify() -> int:
    settings = get_settings()
    providers = {
        "gemini": GeminiProvider(api_key=settings.gemini_api_key),
        "groq": GroqProvider(api_key=settings.groq_api_key),
    }

    failures = 0
    print(f"Verifying {len(MODEL_REGISTRY)} registry models with real calls\n")
    for spec in MODEL_REGISTRY:
        provider = providers[spec.provider]
        try:
            response = await provider.agenerate(PROBE, model=spec.id)
        except LLMProviderError as exc:
            failures += 1
            print(f"  FAIL  {spec.id:26} {spec.provider:7} {str(exc)[:110]}")
        else:
            reply = response.content.strip().replace("\n", " ")[:30]
            print(f"  ok    {spec.id:26} {spec.provider:7} {reply!r}")

    print()
    if failures:
        print(f"{failures} of {len(MODEL_REGISTRY)} registry models are NOT reachable.")
        print("Remove them from app/llm/registry.py -- a model the user can name but cannot")
        print("reach is worse than one that was never offered.")
    else:
        print(f"All {len(MODEL_REGISTRY)} registry models answered.")
    return 1 if failures else 0


async def discover() -> int:
    """List what the keys can see, and flag what the registry already has.

    Listed-but-unverified is the default state here on purpose: seeing a
    model in this output is not permission to add it, only a reason to run
    the verify pass again afterwards.
    """
    settings = get_settings()
    registry_ids = {spec.id for spec in MODEL_REGISTRY}

    from google import genai
    from groq import Groq

    print("=== Gemini: models supporting generateContent ===")
    client = genai.Client(api_key=settings.gemini_api_key)
    for model in sorted(client.models.list(), key=lambda m: m.name):
        if "generateContent" not in (model.supported_actions or []):
            continue
        model_id = model.name.replace("models/", "")
        print(f"  {'[in registry]' if model_id in registry_ids else '             '} {model_id}")

    print("\n=== Groq ===")
    for model in sorted(Groq(api_key=settings.groq_api_key).models.list().data, key=lambda m: m.id):
        print(f"  {'[in registry]' if model.id in registry_ids else '             '} {model.id}")

    print("\nListed is not reachable. Run this script without --discover to prove it.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--discover",
        action="store_true",
        help="List every model the keys can see instead of verifying the registry.",
    )
    args = parser.parse_args()
    return asyncio.run(discover() if args.discover else verify())


if __name__ == "__main__":
    raise SystemExit(main())
