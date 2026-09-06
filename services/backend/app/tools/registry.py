"""The tool lookup table and per-request availability (Phase 5).

Adding a tool is one class plus one line here, matching how personas
(app/personas/registry.py) and models (app/llm/registry.py) work.

`available_for` is the part that carries weight. A tool the planner can name
but that cannot actually run is worse than a missing one: the model keeps
choosing it and every answer becomes an apology. So document search is only
offered when the user actually has a ready document, and web search only when
a provider can be reached. What is withheld and why is reported, so the
reason shows up in preflight and in the logs rather than being invisible.
"""
import logging
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.memory.embedder import get_embedder
from app.models.db import Document
from app.rag.store import get_document_store
from app.tools.base import Tool
from app.tools.documents import DocumentSearchTool
from app.tools.search import build_web_search_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolAvailability:
    tool: Tool
    usable: bool
    #: Empty when usable. Shown in /tools and in preflight, never to the model.
    reason: str = ""


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    async def availability(
        self, session: AsyncSession | None = None, *, user_id: UUID | None = None
    ) -> list[ToolAvailability]:
        """Every tool with whether it can run right now.

        Takes the session because availability is genuinely per-request for
        some tools -- "does this user have any indexed documents" is not
        something a process-wide singleton can answer.
        """
        ready_documents: int | None = None
        if session is not None and user_id is not None:
            ready_documents = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.user_id == user_id, Document.status == "ready")
                )
                or 0
            )

        results: list[ToolAvailability] = []
        for tool in self._tools.values():
            usable, reason = tool.available()
            if usable and tool.name == "document_search":
                if ready_documents is None:
                    usable, reason = False, "no database session to check for documents"
                elif ready_documents == 0:
                    usable, reason = False, "no documents have been uploaded and indexed yet"
            results.append(ToolAvailability(tool=tool, usable=usable, reason=reason))
        return results

    async def available_for(
        self, session: AsyncSession | None = None, *, user_id: UUID | None = None
    ) -> list[Tool]:
        """Just the tools worth offering the planner for this request."""
        return [a.tool for a in await self.availability(session, user_id=user_id) if a.usable]


def build_tool_registry() -> ToolRegistry:
    settings = get_settings()
    return ToolRegistry(
        [
            build_web_search_tool(settings),
            DocumentSearchTool(embedder=get_embedder(), store=get_document_store()),
        ]
    )


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """Process-wide singleton, matching get_llm_router / get_memory_store.

    Overridable as a FastAPI dependency so tests can register fakes.
    """
    return build_tool_registry()
