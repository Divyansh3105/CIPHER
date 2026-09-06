"""Orchestration: routing, timeouts, fallback, and the audit trail.

docs/architecture.md Section 5 makes three promises about this layer -- a
timeout and retry on every agent call, a fallback to a plain reply when an
agent fails, and agents that never talk to each other. The tests that matter
are the ones that hold it to them, because all three are invisible when
working and only show up as a hung request or a lost conversation when not.
"""
import asyncio

import pytest
from sqlalchemy import select

from app.agents.base import Agent, AgentContext, AgentError, AgentResult
from app.agents.orchestrator import Orchestrator
from app.agents.registry import get_orchestrator
from app.llm.base import LLMMessage, LLMProvider, LLMProviderError, LLMResponse
from app.llm.router import LLMRouter
from app.main import app
from app.models.db import AgentRun
from tests.conftest import RecordingAgent


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[LLMMessage]] = []

    async def agenerate(self, messages, model=None) -> LLMResponse:
        self.calls.append(messages)
        reply = self._replies.pop(0) if self._replies else '{"agent": null}'
        return LLMResponse(content=reply, model=model or "scripted", provider=self.name)


def _orchestrator(agents, reply: str) -> Orchestrator:
    provider = ScriptedProvider([reply])
    return Orchestrator(agents=agents, llm_router=LLMRouter(primary=provider, fallback=provider))


def _context(db_session, dev_user_id, message="please research the population of Delhi"):
    return AgentContext(message=message, user_id=dev_user_id, persona_id="jarvis", session=db_session)


# --- routing ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_named_agent_runs_and_contributes(db_session, dev_user_id):
    agent = RecordingAgent("research")

    result = await _orchestrator([agent], '{"agent": "research"}').run(_context(db_session, dev_user_id))

    assert result.agent_used == "research"
    assert result.context == "FAKE AGENT CONTEXT"
    assert agent.calls


@pytest.mark.asyncio
async def test_null_means_answer_directly(db_session, dev_user_id):
    agent = RecordingAgent("research")

    result = await _orchestrator([agent], '{"agent": null}').run(_context(db_session, dev_user_id))

    assert result.agent_used is None
    assert result.runs == []
    assert agent.calls == []


@pytest.mark.asyncio
async def test_a_hallucinated_agent_name_is_refused_not_matched(db_session, dev_user_id):
    """Same refusal rule as the model registry and the tool planner."""
    agent = RecordingAgent("research")

    result = await _orchestrator([agent], '{"agent": "researcher"}').run(_context(db_session, dev_user_id))

    assert result.agent_used is None
    assert agent.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["hi", "thanks", "ok", "good evening"])
async def test_small_talk_never_reaches_the_router(db_session, dev_user_id, message):
    provider = ScriptedProvider(['{"agent": "research"}'])
    orchestrator = Orchestrator(
        agents=[RecordingAgent("research")], llm_router=LLMRouter(primary=provider, fallback=provider)
    )

    result = await orchestrator.run(_context(db_session, dev_user_id, message))

    assert result.agent_used is None
    assert provider.calls == []


@pytest.mark.asyncio
async def test_an_unavailable_agent_is_not_offered(db_session, dev_user_id):
    """A specialist that is offered but always fails makes every answer an
    apology, so it is withheld from the router entirely.
    """

    class Unavailable(RecordingAgent):
        async def available(self, context):
            return False, "nothing indexed"

    agent = Unavailable("research")
    provider = ScriptedProvider(['{"agent": "research"}'])
    orchestrator = Orchestrator(agents=[agent], llm_router=LLMRouter(primary=provider, fallback=provider))

    result = await orchestrator.run(_context(db_session, dev_user_id))

    assert result.agent_used is None
    assert agent.calls == []
    # With no candidates there is nothing to route between, so no call is made.
    assert provider.calls == []


@pytest.mark.asyncio
async def test_a_router_outage_degrades_to_answering_directly(db_session, dev_user_id):
    class Failing(LLMProvider):
        name = "failing"

        async def agenerate(self, messages, model=None):
            raise LLMProviderError("provider is down")

    orchestrator = Orchestrator(
        agents=[RecordingAgent("research")], llm_router=LLMRouter(primary=Failing(), fallback=Failing())
    )

    result = await orchestrator.run(_context(db_session, dev_user_id))

    assert result.agent_used is None


# --- timeouts, retries, failures ----------------------------------------


@pytest.mark.asyncio
async def test_a_timeout_is_retried_once_then_given_up_on(db_session, dev_user_id):
    """Section 5 requires a timeout and a retry. Only timeouts are retried:
    re-running a failure just produces the same error twice.
    """

    class Slow(Agent):
        name = "research"
        description = "sleeps"
        timeout_seconds = 0.05

        def __init__(self):
            self.attempts = 0

        async def run(self, context):
            self.attempts += 1
            await asyncio.sleep(5)

    agent = Slow()
    result = await _orchestrator([agent], '{"agent": "research"}').run(_context(db_session, dev_user_id))

    assert agent.attempts == 2
    assert result.agent_used is None
    assert result.runs[0].status == "timeout"
    # The conversation still gets a reply; only grounding was lost.
    assert "timeout" in result.notice


@pytest.mark.asyncio
async def test_an_agent_failure_is_not_retried(db_session, dev_user_id):
    class Broken(Agent):
        name = "research"
        description = "fails"

        def __init__(self):
            self.attempts = 0

        async def run(self, context):
            self.attempts += 1
            raise AgentError("the search provider is down")

    agent = Broken()
    result = await _orchestrator([agent], '{"agent": "research"}').run(_context(db_session, dev_user_id))

    assert agent.attempts == 1
    assert result.agent_used is None
    assert result.runs[0].status == "failed"
    assert "search provider is down" in result.runs[0].error


@pytest.mark.asyncio
async def test_an_unexpected_exception_does_not_escape_the_orchestrator(db_session, dev_user_id):
    """An agent calls the network and third-party code. It must not be able
    to take the conversation down with it.
    """

    class Exploding(Agent):
        name = "research"
        description = "explodes"

        async def run(self, context):
            raise ValueError("unexpected")

    result = await _orchestrator([Exploding()], '{"agent": "research"}').run(
        _context(db_session, dev_user_id)
    )

    assert result.agent_used is None
    assert result.runs[0].status == "failed"
    assert "ValueError" in result.runs[0].error


@pytest.mark.asyncio
async def test_an_agent_that_contributes_nothing_is_reported_as_unused(db_session, dev_user_id):
    """Research deciding not to search is a real outcome, not a failure --
    but nothing it produced reached the answer, so `agent_used` is None and
    the run is still recorded as ok.
    """
    agent = RecordingAgent("research", AgentResult(context="", summary="decided not to search"))

    result = await _orchestrator([agent], '{"agent": "research"}').run(_context(db_session, dev_user_id))

    assert result.agent_used is None
    assert result.runs[0].status == "ok"
    assert result.notice == "decided not to search"


# --- the audit trail ----------------------------------------------------


@pytest.mark.asyncio
async def test_every_run_is_recorded_with_its_timing(db_session, dev_user_id):
    result = await _orchestrator([RecordingAgent("research")], '{"agent": "research"}').run(
        _context(db_session, dev_user_id)
    )

    run = result.runs[0]
    assert run.agent_name == "research"
    assert run.status == "ok"
    assert run.duration_ms >= 0
    assert run.input.startswith("please research")


# --- chat integration ---------------------------------------------------


async def test_an_agent_contribution_reaches_the_prompt_and_the_response(client, provider):
    agent = RecordingAgent(
        "research",
        AgentResult(
            context="AGENT CONTEXT",
            citations=[{"kind": "web", "title": "T", "url": "u"}],
            summary="searched",
            output="out",
        ),
    )
    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator([agent], '{"agent": "research"}')
    try:
        response = await client.post("/chat/message", json={"content": "please research something"})
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    body = response.json()
    assert body["agent_used"] == "research"
    assert body["message"]["citations"][0]["title"] == "T"
    assert "AGENT CONTEXT" in provider.last_messages[0].content


async def test_a_failing_agent_still_answers_and_says_so(client):
    """The one thing that must not happen is answering as though a
    specialist had contributed.
    """

    class Broken(Agent):
        name = "research"
        description = "fails"

        async def run(self, context):
            raise AgentError("the search provider is down")

    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator([Broken()], '{"agent": "research"}')
    try:
        response = await client.post("/chat/message", json={"content": "please research something"})
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    assert response.status_code == 200
    body = response.json()
    assert body["agent_used"] is None
    assert "search provider is down" in body["activity"]


async def test_a_run_row_is_committed_with_the_message(client, dev_user_id):
    """The run and the reply commit together. An audit trail that can
    disagree with the conversation is worse than none.
    """
    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator(
        [RecordingAgent("research")], '{"agent": "research"}'
    )
    try:
        await client.post("/chat/message", json={"content": "please research something"})
        runs = (await client.get("/agents/runs")).json()
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    assert len(runs) == 1
    assert runs[0]["agent_name"] == "research"
    assert runs[0]["status"] == "ok"


async def test_no_agents_registered_leaves_chat_unchanged(client, provider):
    """The default path for five phases must keep working."""
    response = await client.post("/chat/message", json={"content": "what is the capital of France"})

    assert response.status_code == 200
    assert response.json()["agent_used"] is None
    assert "This is retrieved data" not in provider.last_messages[0].content


# --- the API ------------------------------------------------------------


async def test_agents_endpoint_lists_the_registered_specialists(client):
    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator(
        [RecordingAgent("research"), RecordingAgent("coding")], '{"agent": null}'
    )
    try:
        agents = (await client.get("/agents")).json()
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    assert {a["name"] for a in agents} == {"research", "coding"}


async def test_runs_are_newest_first_and_scoped_to_the_user(client):
    from uuid import uuid4

    from app.api.deps import get_current_user_id

    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator(
        [RecordingAgent("research")], '{"agent": "research"}'
    )
    try:
        await client.post("/chat/message", json={"content": "please research something"})
        mine = (await client.get("/agents/runs")).json()

        app.dependency_overrides[get_current_user_id] = lambda: uuid4()
        other = (await client.get("/agents/runs")).json()
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_orchestrator, None)

    assert len(mine) == 1
    assert other == []


async def test_a_disabled_agent_is_not_offered_to_the_router(client, provider):
    """Switched off means not routed to at all, rather than routed to and
    refused -- a router that keeps picking an agent that will not run wastes
    a round trip on every message.
    """
    agent = RecordingAgent("research")
    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator([agent], '{"agent": "research"}')
    try:
        assert (await client.patch("/agents/research", json={"enabled": False})).status_code == 200
        listing = (await client.get("/agents")).json()
        response = await client.post("/chat/message", json={"content": "please research something"})
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    assert listing[0]["enabled"] is False
    assert response.json()["agent_used"] is None
    assert agent.calls == []


async def test_re_enabling_an_agent_brings_it_back(client):
    agent = RecordingAgent("research")
    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator([agent], '{"agent": "research"}')
    try:
        await client.patch("/agents/research", json={"enabled": False})
        await client.patch("/agents/research", json={"enabled": True})
        response = await client.post("/chat/message", json={"content": "please research something"})
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    assert response.json()["agent_used"] == "research"


async def test_toggling_an_unknown_agent_is_refused_with_the_real_list(client):
    """Creating a settings row for an agent that does not exist is how a
    toggle silently stops corresponding to anything.
    """
    app.dependency_overrides[get_orchestrator] = lambda: _orchestrator(
        [RecordingAgent("research")], '{"agent": null}'
    )
    try:
        response = await client.patch("/agents/researcher", json={"enabled": False})
    finally:
        app.dependency_overrides.pop(get_orchestrator, None)

    assert response.status_code == 404
    assert "research" in response.json()["detail"]
