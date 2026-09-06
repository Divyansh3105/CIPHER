"""The memory agent: answering questions *about* what is remembered (Phase 6).

Not to be confused with the memory system itself. Every reply already gets
relevant memories injected automatically (app/api/chat.py, Phase 3) -- this
agent exists for the narrower case where the memory store is the *subject*
of the question rather than background for it: "what do you know about me",
"what have I told you about the project", "do you remember anything about my
editor".

Those questions are badly served by ordinary recall, which retrieves the top
few memories above a similarity threshold tuned for relevance to a topic.
"What do you know about me" has no topic, so it matches almost nothing and
the assistant answers "I don't remember anything" while holding forty facts.
This agent widens the search and drops the threshold instead.
"""
import logging

from app.agents.base import Agent, AgentContext, AgentError, AgentResult
from app.memory.embedder import Embedder, EmbeddingError
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)

#: Far wider than chat recall's 5. A question about the memory store itself
#: wants breadth, not the single best match.
MEMORY_AGENT_TOP_K = 25

#: And a much lower floor, for the same reason -- "what do you know about me"
#: is topically close to nothing in particular.
MEMORY_AGENT_MIN_SIMILARITY = 0.30

MEMORY_AGENT_MAX_CHARS = 4000


class MemoryAgent(Agent):
    name = "memory"
    description = (
        "Answers questions about what the assistant remembers: what it knows about the user, "
        "what they have told it before, or whether it remembers something specific. "
        "Use it when the memory store itself is the subject of the question. "
        "Do NOT use it for ordinary questions that merely benefit from context -- those "
        "already get relevant memories automatically."
    )
    timeout_seconds = 30.0

    def __init__(self, embedder: Embedder, store: MemoryStore) -> None:
        self._embedder = embedder
        self._store = store

    async def run(self, context: AgentContext) -> AgentResult:
        try:
            vector = (await self._embedder.aembed([context.message], task="query"))[0]
        except EmbeddingError as exc:
            raise AgentError(f"Could not search memory: {exc}") from exc

        hits = await self._store.search(
            context.session,
            user_id=context.user_id,
            embedding=vector,
            limit=MEMORY_AGENT_TOP_K,
            min_similarity=MEMORY_AGENT_MIN_SIMILARITY,
        )

        if not hits:
            # An empty store and a store with nothing relevant are different
            # answers, and saying the wrong one is how the assistant ends up
            # denying it remembers things it does.
            return AgentResult(
                context=(
                    "A search of the long-term memory store returned nothing at all. "
                    "Say plainly that you do not have anything stored about this, and do not "
                    "invent a recollection."
                ),
                summary="searched memory — nothing stored",
                output="no memories matched",
            )

        lines = ["Everything currently stored that relates to this question:", ""]
        budget = MEMORY_AGENT_MAX_CHARS
        used = 0
        for hit in hits:
            if budget <= 0:
                break
            content = hit.content[:budget]
            budget -= len(content)
            used += 1
            lines.append(f"- {content}")

        lines.append("")
        lines.append(
            "Answer from this list only. If it does not cover what was asked, say so rather "
            "than filling the gap. Do not recite the list verbatim unless asked to."
        )

        await self._store.mark_recalled(context.session, [h.id for h in hits[:used]])

        return AgentResult(
            context="\n".join(lines),
            summary=f"searched memory — {used} stored fact" + ("s" if used != 1 else ""),
            output="\n".join(h.content for h in hits[:used]),
        )
