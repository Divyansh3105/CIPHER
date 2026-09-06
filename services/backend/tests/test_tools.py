"""Tool planning: choosing a tool, and refusing to.

The tests that matter here are the ones about *not* using a tool. A planner
that over-triggers makes every message slow and occasionally wrong, while a
planner that under-triggers just answers from the model's own knowledge --
which is what the assistant did for four phases.

Chat-level integration (a tool result reaching the prompt, a tool failure
being reported rather than swallowed) moved to tests/test_agents.py in
Phase 6, when the orchestrator took over routing.
"""
import pytest

from app.llm.base import LLMMessage, LLMProviderError, LLMProvider, LLMResponse
from app.tools.base import ToolResult
from app.tools.planner import ToolPlanner
from app.llm.router import LLMRouter
from tests.conftest import RecordingTool


class ScriptedProvider(LLMProvider):
    """Returns canned replies in order, so the planner's output is fixed."""

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[LLMMessage]] = []

    async def agenerate(self, messages: list[LLMMessage], model: str | None = None) -> LLMResponse:
        self.calls.append(messages)
        reply = self._replies.pop(0) if self._replies else "(no more scripted replies)"
        return LLMResponse(content=reply, model=model or "scripted", provider=self.name)


def _planner(reply: str) -> ToolPlanner:
    provider = ScriptedProvider([reply])
    return ToolPlanner(LLMRouter(primary=provider, fallback=provider))


# --- planning -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_named_tool_is_planned_with_a_standalone_query():
    tool = RecordingTool("web_search")

    plan = await _planner('{"tool": "web_search", "query": "pgvector HNSW index"}').plan(
        "hey what's that pgvector index thing called again", [tool]
    )

    assert plan.tool_name == "web_search"
    # The query is a good standalone search, not the message verbatim.
    assert plan.query == "pgvector HNSW index"


@pytest.mark.asyncio
async def test_null_means_no_tool():
    plan = await _planner('{"tool": null}').plan("what do you think about tabs vs spaces", [RecordingTool()])

    assert plan.tool_name is None


@pytest.mark.asyncio
async def test_a_hallucinated_tool_name_is_refused_not_matched():
    """Same rule as the model registry: an unknown name produces no call,
    never a guess at which real tool was meant.
    """
    tool = RecordingTool("web_search")

    plan = await _planner('{"tool": "search_the_internet", "query": "x"}').plan(
        "look up the weather in Delhi please", [tool]
    )

    assert plan.tool_name is None
    assert "unknown tool" in plan.reason


@pytest.mark.asyncio
async def test_a_tool_chosen_without_a_query_is_refused():
    plan = await _planner('{"tool": "web_search"}').plan(
        "look up the weather in Delhi please", [RecordingTool("web_search")]
    )

    assert plan.tool_name is None


@pytest.mark.asyncio
async def test_json_wrapped_in_a_code_fence_is_still_parsed():
    """Models fence JSON despite being told not to."""
    plan = await _planner('```json\n{"tool": "web_search", "query": "delhi weather"}\n```').plan(
        "look up the weather in Delhi please", [RecordingTool("web_search")]
    )

    assert plan.tool_name == "web_search"


@pytest.mark.asyncio
async def test_unparseable_planner_output_means_no_tool_not_an_error():
    plan = await _planner("I think you should search the web!").plan(
        "look up the weather in Delhi please", [RecordingTool("web_search")]
    )

    assert plan.tool_name is None
    assert "JSON" in plan.reason


@pytest.mark.asyncio
async def test_a_planner_outage_degrades_to_no_tool():
    """An ungrounded answer beats an error page."""

    class Failing(LLMProvider):
        name = "failing"

        async def agenerate(self, messages, model=None):
            raise LLMProviderError("provider is down")

    planner = ToolPlanner(LLMRouter(primary=Failing(), fallback=Failing()))

    plan = await planner.plan("look up the weather in Delhi", [RecordingTool("web_search")])

    assert plan.tool_name is None
    assert "failed" in plan.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["hi", "thanks!", "ok", "good morning", "how are you"])
async def test_small_talk_never_reaches_the_planner(message):
    """The pre-filter is the whole reason tool use does not add a round trip
    to every greeting.
    """
    provider = ScriptedProvider(['{"tool": "web_search", "query": "x"}'])
    planner = ToolPlanner(LLMRouter(primary=provider, fallback=provider))

    plan = await planner.plan(message, [RecordingTool("web_search")])

    assert plan.tool_name is None
    assert provider.calls == []


@pytest.mark.asyncio
async def test_no_tools_available_means_no_planner_call():
    provider = ScriptedProvider(['{"tool": "web_search", "query": "x"}'])
    planner = ToolPlanner(LLMRouter(primary=provider, fallback=provider))

    plan = await planner.plan("please look up the population of Delhi", [])

    assert plan.tool_name is None
    assert provider.calls == []
