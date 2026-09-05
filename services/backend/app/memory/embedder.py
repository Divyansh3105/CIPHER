"""Provider-agnostic embedding interface for long-term memory (Phase 3).

Deliberately a separate ABC from `LLMProvider` (app/llm/base.py), not a new
abstract method on it: `LLMProvider` is defined entirely by `agenerate`, and
Groq has no embeddings API -- adding `aembed` there would force `GroqProvider`
to implement something it fundamentally can't. The fact that Gemini happens to
serve both chat and embeddings from the same `genai.Client` is an
implementation detail of `GeminiEmbedder`, not a reason to merge the ABCs.

Model pinned to gemini-embedding-001 at output_dimensionality=768: verified
live against this account's API key via client.aio.models.list() (filtered to
embedContent support) and a real embed_content call before pinning -- see
scripts/verify_embedding_model.py. Do the same verification again if this ever
404s, the way app/llm/gemini.py's DEFAULT_MODEL had to be re-pinned once
already.
"""
import math
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Literal

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import Settings, get_settings

EMBEDDING_DIM = 768  # must match app.models.db.MEMORY_EMBEDDING_DIM
DEFAULT_EMBEDDING_MODEL = "models/gemini-embedding-001"

Task = Literal["document", "query"]

_TASK_TYPE = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}


class EmbeddingError(Exception):
    """Raised when an embedder fails to produce vectors (network, auth, rate limit, ...)."""


class Embedder(ABC):
    name: str
    dim: int
    model: str

    @abstractmethod
    async def aembed(self, texts: list[str], *, task: Task) -> list[list[float]]:
        """Return one L2-normalised vector per input text, in input order.

        Batch-first on purpose: extraction may yield up to 3 facts in one
        turn, and batching them is one API call instead of three.
        """
        raise NotImplementedError


def _normalise(vector: list[float]) -> list[float]:
    """L2-normalise so cosine similarity is correct regardless of whether the
    underlying model already returns unit vectors.

    gemini-embedding-001 only returns pre-normalised vectors at its native
    3072 dimensions -- truncated (Matryoshka) outputs like our 768 are NOT
    pre-normalised (verified live: norm was ~0.58, not 1.0). Normalising here
    unconditionally also means a future embedding-model swap or a switch from
    `<=>` to `<#>` (inner product) costs no data migration.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


class GeminiEmbedder(Embedder):
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_EMBEDDING_MODEL, dim: int = EMBEDDING_DIM) -> None:
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.dim = dim

    async def aembed(self, texts: list[str], *, task: Task) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.aio.models.embed_content(
                model=self.model,
                contents=texts,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.dim,
                    task_type=_TASK_TYPE[task],
                ),
            )
        except APIError as exc:
            raise EmbeddingError(f"Gemini embedding request failed: {exc}") from exc

        if not response.embeddings or len(response.embeddings) != len(texts):
            raise EmbeddingError("Gemini returned an unexpected number of embeddings")

        return [_normalise(e.values) for e in response.embeddings]


def build_embedder(settings: Settings) -> Embedder:
    return GeminiEmbedder(api_key=settings.gemini_api_key)


@lru_cache
def get_embedder() -> Embedder:
    """Process-wide singleton so the genai client isn't rebuilt per-request.

    Mirrors app.llm.router.get_llm_router's split between a plain builder
    (for scripts that want a fresh, uncached instance) and this cached
    FastAPI dependency.
    """
    return build_embedder(get_settings())
