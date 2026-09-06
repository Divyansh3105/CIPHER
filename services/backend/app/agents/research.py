"""The research agent: web search, documents, fact-finding (Phase 6).

This agent owns the Phase 5 tools. The planner that used to sit in
app/api/chat.py has moved here rather than being duplicated: choosing
between web search and document search is exactly this agent's job, and
leaving a second router in the endpoint would mean two places deciding the
same thing and drifting apart.

The orchestrator picks the agent; the agent picks the tool. Two levels, each
with one responsibility.
"""
import logging

from app.agents.base import Agent, AgentContext, AgentError, AgentResult
from app.llm.router import LLMRouter
from app.tools.base import ToolError
from app.tools.planner import ToolPlanner
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ResearchAgent(Agent):
    name = "research"
    description = (
        "Looks things up: the public web for current events, facts, documentation and "
        "definitions, and the user's own uploaded documents for anything about their files. "
        "Use it whenever answering well requires information the assistant does not already "
        "have. Do NOT use it for opinions, arithmetic, writing tasks, or questions the "
        "conversation has already answered."
    )
    # Web search plus an LLM planning call; the default 45s is generous but a
    # slow provider should not hold a reply forever.
    timeout_seconds = 40.0

    def __init__(self, registry: ToolRegistry, llm_router: LLMRouter) -> None:
        self._registry = registry
        self._router = llm_router

    async def available(self, context: AgentContext) -> tuple[bool, str]:
        usable = await self._registry.available_for(context.session, user_id=context.user_id)
        if not usable:
            return False, "no search tools are currently usable"
        return True, ""

    async def run(self, context: AgentContext) -> AgentResult:
        tools = await self._registry.available_for(context.session, user_id=context.user_id)
        if not tools:
            raise AgentError("No search tools are available.")

        plan = await ToolPlanner(self._router).plan(context.message, tools)
        if not plan.tool_name:
            # The orchestrator routed here but the agent found nothing worth
            # looking up. Reported as a real outcome rather than an error:
            # "I decided not to search" is information, and a run recorded
            # as failed would make the dashboard lie.
            return AgentResult(
                summary=f"decided not to search ({plan.reason})",
                output=f"no tool used: {plan.reason}",
            )

        tool = self._registry.get(plan.tool_name)
        if tool is None:  # pragma: no cover -- planner validates against this registry
            raise AgentError(f"Planner named an unknown tool {plan.tool_name!r}.")

        try:
            result = await tool.run(plan.query, session=context.session, user_id=context.user_id)
        except ToolError as exc:
            raise AgentError(str(exc)) from exc

        return AgentResult(
            context=result.context,
            citations=result.citations,
            summary=result.summary,
            output=result.context,
        )
