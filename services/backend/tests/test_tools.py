"""Tool planning, tool failure, and document grounding in chat.

The tests that matter here are the ones about *not* using a tool and about
tools failing. A planner that over-triggers makes every message slow and
occasionally wrong; a tool failure that is swallowed makes the assistant
answer as though it had looked something up when it had not.
"""
import pytest

from app.llm.base import LLMMessage, LLMProviderError, LLMProvider, LLMResponse
from app.main import app
from app.tools.base import Tool, ToolError, ToolResult
from app.tools.planner import ToolPlanner
from app.tools.registry import ToolRegistry, get_tool_registry
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


# --- chat integration ---------------------------------------------------


async def test_a_tool_result_is_injected_and_reported(client, provider):
    tool = RecordingTool("web_search", ToolResult(context="RESULT CONTEXT", citations=[{"kind": "web", "title": "T", "url": "u"}], summary="searched"))
    app.dependency_overrides[get_tool_registry] = lambda: _RoutingRegistry([tool], "web_search", "delhi")
    try:
        response = await client.post("/chat/message", json={"content": "look up the weather in Delhi"})
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)

    body = response.json()
    assert body["tool_used"] == "web_search"
    assert body["tool_summary"] == "searched"
    assert body["message"]["citations"][0]["title"] == "T"
    # And the tool's output actually reached the prompt.
    assert "RESULT CONTEXT" in provider.last_messages[0].content


async def test_a_failing_tool_still_answers_and_says_it_failed(client):
    """The one thing that must not happen is answering as if a tool had run."""

    class Broken(Tool):
        name = "web_search"
        description = "broken"

        async def run(self, query: str, **context) -> ToolResult:
            raise ToolError("the search provider is down")

    app.dependency_overrides[get_tool_registry] = lambda: _RoutingRegistry([Broken()], "web_search", "x")
    try:
        response = await client.post("/chat/message", json={"content": "look up the weather in Delhi"})
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)

    assert response.status_code == 200
    body = response.json()
    assert body["tool_used"] is None
    assert "failed" in body["tool_summary"]
    assert body["message"]["citations"] == []


async def test_a_tool_raising_an_unexpected_error_does_not_take_the_reply_down(client):
    """A tool talks to the network and to third-party JSON -- it is the least
    trustworthy code in the request path.
    """

    class Exploding(Tool):
        name = "web_search"
        description = "explodes"

        async def run(self, query: str, **context) -> ToolResult:
            raise ValueError("unexpected")

    app.dependency_overrides[get_tool_registry] = lambda: _RoutingRegistry([Exploding()], "web_search", "x")
    try:
        response = await client.post("/chat/message", json={"content": "look up the weather in Delhi"})
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)

    assert response.status_code == 200
    assert response.json()["tool_used"] is None
    assert "ValueError" in response.json()["tool_summary"]


async def test_no_tools_registered_means_chat_is_unchanged(client, provider):
    """The default path for four phases must keep working."""
    response = await client.post("/chat/message", json={"content": "what is the capital of France"})

    assert response.status_code == 200
    assert response.json()["tool_used"] is None
    # The block header, not just "What you looked up" -- that phrase also
    # appears in the shared capability note, which is always present.
    assert "This is retrieved data" not in provider.last_messages[0].content


class _RoutingRegistry(ToolRegistry):
    """A registry whose planner decision is fixed, so chat tests do not
    depend on what a fake LLM happens to reply to the planner prompt.
    """

    def __init__(self, tools: list[Tool], forced_tool: str, forced_query: str) -> None:
        super().__init__(tools)
        self._forced = (forced_tool, forced_query)

    async def available_for(self, session=None, *, user_id=None):
        return self.all()


@pytest.fixture(autouse=True)
def _force_plan(monkeypatch, request):
    """Pin the planner's answer for the chat integration tests above.

    Without this they would be testing the fake LLM provider's reply to a
    planner prompt, which is not the behaviour under test.
    """
    if "client" not in request.fixturenames:
        return

    async def fake_plan(self, message, tools):
        from app.tools.planner import ToolPlan

        if not tools:
            return ToolPlan(None, reason="no tools available")
        return ToolPlan(tools[0].name, "delhi weather")

    monkeypatch.setattr(ToolPlanner, "plan", fake_plan)
