"""Hybrid memory capture: a cheap deterministic "remember that..." detector,
plus a background LLM extraction pass -- both feeding the same dedup'd store.

Both halves run from a FastAPI `BackgroundTasks` callback (see
app/api/chat.py), *after* the chat reply has already been sent, so neither
adds user-visible latency. That has one hard consequence: if this code raises,
the exception surfaces after the HTTP response has already started sending,
and Starlette's attempt to turn that into a 500 blows up with a confusing
RuntimeError instead. `MemoryWriter.capture`'s outer `except Exception` exists
specifically to prevent that -- it is a correctness requirement, not sloppy
error handling. Do not remove it or narrow it without re-reading this comment.
"""
import json
import logging
import re
from functools import lru_cache
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.llm.base import LLMMessage, LLMProviderError
from app.llm.router import LLMRouter, get_llm_router
from app.memory.embedder import Embedder, EmbeddingError, get_embedder
from app.memory.store import MemoryStore, get_memory_store

logger = logging.getLogger(__name__)

# Below this many words in the user's message, skip the LLM extraction pass
# entirely -- "ok", "thanks", "hi" etc. are never going to contain a durable
# fact, and this is the cheapest possible quota-saving gate.
MIN_WORDS_FOR_EXTRACTION = 5

# --- Deterministic "remember that..." detection -----------------------------

_TRIGGER_PATTERN = re.compile(
    r"(?:remember that|remember:|remember,|don'?t forget that|keep in mind that|"
    r"make a note that|for future reference,?|note that i)\s*",
    re.IGNORECASE,
)
_NEGATIVE_PATTERN = re.compile(
    r"^\s*(?:do|can|will|would)\s+you\s+remember|^\s*what do you remember",
    re.IGNORECASE,
)
_MAX_EXPLICIT_LEN = 500


def detect_explicit(user_text: str) -> str | None:
    """Return the fact to remember if `user_text` explicitly asks to
    remember something, else None.

    Deliberately conservative: a question ("do you remember my birthday?")
    must NEVER create a memory, even though it contains the word "remember".
    """
    stripped = user_text.strip()
    if not stripped or stripped.endswith("?"):
        return None
    if _NEGATIVE_PATTERN.search(stripped):
        return None
    match = _TRIGGER_PATTERN.search(stripped)
    if not match:
        return None
    fact = stripped[match.end() :].strip()
    if len(fact) < 3:
        return None
    return fact[:_MAX_EXPLICIT_LEN]


# --- Background LLM extraction ----------------------------------------------

_EXTRACTION_PROMPT = """\
Extract durable facts about the user from the message below. A durable fact \
is something still true next month: preferences, relationships, ongoing \
projects, constraints, decisions. NOT: greetings, questions, one-off \
requests, or anything about you (the assistant).

Message:
{user_text}

Return a JSON array of at most 3 short, complete, standalone sentences. \
Return [] if there is nothing durable. Return ONLY the JSON array -- no \
other text, no markdown fences."""

_MAX_EXTRACTED_FACTS = 3
_MAX_FACT_LEN = 300


def _parse_facts(raw: str) -> list[str]:
    """Defensive JSON-array parsing: strip code fences, reject anything that
    isn't `list[str]`, drop oversized items, drop the whole result on any
    parse failure. Extraction quality is judged with real output in
    scripts/memory_golden_set.py, not guarded against here -- this function's
    only job is to never crash and never return garbage.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("Memory extraction: could not parse JSON from model output")
        return []
    if not isinstance(data, list):
        return []
    facts = [item for item in data if isinstance(item, str) and 0 < len(item) <= _MAX_FACT_LEN]
    if not facts:
        logger.debug("Memory extraction: model returned no durable facts")
    return facts[:_MAX_EXTRACTED_FACTS]


async def _extract_facts(llm_router: LLMRouter, user_text: str) -> list[str]:
    if len(user_text.split()) < MIN_WORDS_FOR_EXTRACTION:
        return []
    prompt = _EXTRACTION_PROMPT.format(user_text=user_text)
    try:
        response, _fell_back = await llm_router.generate([LLMMessage(role="user", content=prompt)])
    except LLMProviderError as exc:
        logger.warning("Memory extraction: LLM call failed: %s", exc)
        return []
    return _parse_facts(response.content)


# --- The writer --------------------------------------------------------------


class MemoryWriter:
    """Runs after a chat reply has been sent (see app/api/chat.py). Never
    raises -- see the module docstring.
    """

    def __init__(self, embedder: Embedder, store: MemoryStore, llm_router: LLMRouter) -> None:
        self._embedder = embedder
        self._store = store
        self._llm_router = llm_router

    async def capture(
        self,
        *,
        session_factory: async_sessionmaker,
        user_id: UUID,
        conversation_id: UUID,
        persona_id: str,
        user_text: str,
    ) -> None:
        try:
            candidates: list[tuple[str, str]] = []  # (content, source)

            explicit = detect_explicit(user_text)
            if explicit:
                candidates.append((explicit, "explicit"))

            for fact in await _extract_facts(self._llm_router, user_text):
                candidates.append((fact, "extracted"))

            if not candidates:
                return

            texts = [content for content, _source in candidates]
            try:
                vectors: list[list[float] | None] = list(
                    await self._embedder.aembed(texts, task="document")
                )
            except EmbeddingError as exc:
                logger.warning("Memory capture: embedding failed, storing without vectors: %s", exc)
                vectors = [None] * len(texts)

            async with session_factory() as session:
                for (content, source), vector in zip(candidates, vectors):
                    await self._store.add_if_new(
                        session,
                        user_id=user_id,
                        content=content,
                        embedding=vector,
                        memory_type="long_term" if source == "explicit" else "semantic",
                        source=source,
                        persona=persona_id,
                        conversation_id=conversation_id,
                    )
                await session.commit()
        except Exception:  # noqa: BLE001 -- mandatory, see module docstring
            logger.exception("Memory capture failed (conversation=%s)", conversation_id)


def build_memory_writer(embedder: Embedder, store: MemoryStore, llm_router: LLMRouter) -> MemoryWriter:
    return MemoryWriter(embedder=embedder, store=store, llm_router=llm_router)


@lru_cache
def get_memory_writer() -> MemoryWriter:
    """Process-wide singleton, matching get_llm_router / get_embedder /
    get_memory_store. Reuses the shared llm_router (and therefore its
    Gemini->Groq fallback) for extraction rather than building a fresh
    provider -- see the module docstring in scripts/memory_golden_set.py for
    the quota tradeoff this implies.
    """
    return build_memory_writer(get_embedder(), get_memory_store(), get_llm_router())
