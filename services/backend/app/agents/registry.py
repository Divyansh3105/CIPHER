"""The agent lookup table (Phase 6).

Adding an agent is one class plus one line here, matching personas, models
and tools. The orchestrator is constructed from this, so registration and
routing cannot disagree about what exists.
"""
from functools import lru_cache

from app.agents.base import Agent
from app.agents.coding import CodingAgent
from app.agents.memory import MemoryAgent
from app.agents.orchestrator import Orchestrator
from app.agents.research import ResearchAgent
from app.llm.router import get_llm_router
from app.memory.embedder import get_embedder
from app.memory.store import get_memory_store
from app.tools.registry import get_tool_registry


def build_orchestrator() -> Orchestrator:
    llm_router = get_llm_router()
    agents: list[Agent] = [
        ResearchAgent(registry=get_tool_registry(), llm_router=llm_router),
        CodingAgent(llm_router=llm_router),
        MemoryAgent(embedder=get_embedder(), store=get_memory_store()),
    ]
    return Orchestrator(agents=agents, llm_router=llm_router)


@lru_cache
def get_orchestrator() -> Orchestrator:
    """Process-wide singleton, matching get_llm_router / get_tool_registry.

    Overridable as a FastAPI dependency so tests can substitute fakes.
    """
    return build_orchestrator()
