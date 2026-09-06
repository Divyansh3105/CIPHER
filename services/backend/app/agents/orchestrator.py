"""Orchestrator-as-hub: routing, timeouts, fallback, and the audit trail.

docs/architecture.md Section 5 specifies the pattern and three requirements:
agents report back to a hub rather than to each other, every agent call has a
timeout and a retry, and a failed agent falls back to a plain LLM response
rather than crashing the conversation. All three are implemented here.

The routing call replaces the tool planner's old position in
app/api/chat.py. It does not duplicate it: the orchestrator chooses an
*agent*, and the research agent then chooses a *tool*. Two decisions, two
levels, one owner each.

**Every run is recorded, including the ones that did nothing.** A dashboard
that only shows successes is a dashboard that lies by omission -- "why was
that answer ungrounded" is answerable only if the skip, the timeout and the
failure are all in the table too.

**Retry is deliberately narrow.** Only timeouts are retried, once. Retrying a
failure means running the same failing call twice for the same error, and
retrying a slow call that eventually succeeds would double an already-slow
reply. A timeout is the one case where a second attempt is plausibly
different and the first attempt cost nothing usable.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import Agent, AgentContext, AgentError, AgentResult
from app.models.db import Agent as AgentRow
from app.llm.base import LLMMessage, LLMProviderError
from app.llm.router import LLMRouter
from app.models.db import AgentRun

logger = logging.getLogger(__name__)

#: Below this many words, skip routing entirely. Same gate as the tool
#: planner had, for the same reason: no agent helps with "thanks".
MIN_WORDS_FOR_ROUTING = 3

_NO_AGENT_NEEDED = re.compile(
    r"^\s*(?:hi|hey|hello|yo|thanks|thank you|thx|ok|okay|cool|nice|got it|sure|"
    r"good morning|good evening|good night|bye|goodbye|how are you|what'?s up)"
    r"[\s.!?]*$",
    re.IGNORECASE,
)

_ROUTER_PROMPT = """\
You route a user's message to at most one specialist. Reply with JSON only.

Specialists:
{agents}

Reply with exactly one JSON object and nothing else:
{{"agent": "<name>"}}
or
{{"agent": null}}

Rules:
- Choose null unless a specialist clearly helps. Answering directly is the default and is usually right.
- Choose null for greetings, small talk, opinions, and anything the conversation already answers.
- Never name a specialist that is not listed.

User's message:
{message}"""


@dataclass
class OrchestrationResult:
    """What the orchestrator produced for one turn."""

    #: Folded into the system prompt by the caller.
    context: str = ""
    citations: list[dict] = field(default_factory=list)
    #: The agent that actually contributed, or None. None *includes* the
    #: case where an agent was chosen and failed -- see `notice`.
    agent_used: str | None = None
    #: One line for the UI. Non-empty with agent_used=None means "tried a
    #: specialist and it did not work", which the user must be able to tell
    #: apart from "answered directly".
    notice: str = ""
    #: Run records to persist. Written by the caller inside its own
    #: transaction rather than here, so the orchestrator never commits.
    runs: list[AgentRun] = field(default_factory=list)


class Orchestrator:
    def __init__(self, agents: list[Agent], llm_router: LLMRouter) -> None:
        self._agents = {a.name: a for a in agents}
        self._router = llm_router

    def agents(self) -> list[Agent]:
        return list(self._agents.values())

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    async def _route(self, context: AgentContext, candidates: list[Agent]) -> tuple[str | None, str]:
        """Pick an agent, or none. Returns (name, reason)."""
        if not candidates:
            return None, "no agents available"
        if len(context.message.split()) < MIN_WORDS_FOR_ROUTING:
            return None, "message too short to need a specialist"
        if _NO_AGENT_NEEDED.match(context.message):
            return None, "small talk"

        catalogue = "\n".join(f"- {a.name}: {a.description}" for a in candidates)
        prompt = _ROUTER_PROMPT.format(agents=catalogue, message=context.message[:2000])

        try:
            response, _ = await self._router.generate([LLMMessage(role="user", content=prompt)])
        except LLMProviderError as exc:
            # A routing outage means answering directly, which is what the
            # assistant did for five phases. Strictly better than an error.
            logger.warning("Agent routing failed, answering directly: %s", exc)
            return None, f"router unavailable: {exc}"

        return self._parse_route(response.content, {a.name for a in candidates})

    @staticmethod
    def _parse_route(raw: str, allowed: set[str]) -> tuple[str | None, str]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, "router did not return JSON"
        try:
            payload = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None, "router returned malformed JSON"
        if not isinstance(payload, dict):
            return None, "router returned JSON that was not an object"

        name = payload.get("agent")
        if name in (None, "", "null", "none"):
            return None, "router chose to answer directly"
        # Same refusal rule as the model registry and the tool planner: an
        # unrecognised name produces no call, never a guess at which real
        # agent was meant.
        if not isinstance(name, str) or name not in allowed:
            return None, f"router named an unknown agent {name!r}"
        return name, ""

    async def run(self, context: AgentContext) -> OrchestrationResult:
        """Route to one agent and return what it contributed.

        Never raises. Every failure path degrades to answering directly,
        because a broken specialist should cost grounding, not the reply.
        """
        disabled = await self._disabled_names(context.session)
        candidates: list[Agent] = []
        for agent in self._agents.values():
            if agent.name in disabled:
                # Switched off by the user. Not offered to the router at all,
                # rather than offered and refused -- a router that keeps
                # picking a specialist that will not run wastes a round trip
                # on every message.
                continue
            usable, reason = await agent.available(context)
            if usable:
                candidates.append(agent)
            else:
                logger.debug("Agent %s unavailable: %s", agent.name, reason)

        name, reason = await self._route(context, candidates)
        if name is None:
            return OrchestrationResult(notice="")

        agent = self._agents[name]
        started = time.monotonic()
        result, status, error = await self._invoke(agent, context)
        duration_ms = int((time.monotonic() - started) * 1000)

        run = AgentRun(
            id=uuid4(),
            user_id=context.user_id,
            agent_name=agent.name,
            conversation_id=context.conversation_id,
            input=context.message[:4000],
            output=(result.output[:8000] if result else None),
            status=status,
            error=error,
            duration_ms=duration_ms,
        )

        if result is None:
            return OrchestrationResult(
                agent_used=None,
                notice=f"the {agent.name} specialist {status}: {error}",
                runs=[run],
            )

        return OrchestrationResult(
            context=result.context,
            citations=result.citations,
            # An agent that ran but contributed nothing (research deciding
            # not to search) is reported as *not used*, because nothing it
            # produced reached the answer. The run row still records it.
            agent_used=agent.name if result.context else None,
            notice=result.summary,
            runs=[run],
        )

    @staticmethod
    async def _disabled_names(session: AsyncSession) -> set[str]:
        """Agents the user has switched off.

        Absence from the table means enabled. That inversion is deliberate:
        a new agent added in code should work immediately rather than
        waiting for a row to be created for it, and there is no startup sync
        to forget to run.
        """
        try:
            rows = await session.execute(select(AgentRow.name).where(AgentRow.enabled.is_(False)))
            return {name for (name,) in rows}
        except Exception:  # noqa: BLE001
            # Orchestration must not fail because a settings lookup did.
            logger.warning("Could not read agent settings; treating all agents as enabled", exc_info=True)
            return set()

    async def _invoke(
        self, agent: Agent, context: AgentContext
    ) -> tuple[AgentResult | None, str, str | None]:
        """Run an agent with a timeout, retrying a timeout exactly once."""
        for attempt in (1, 2):
            try:
                result = await asyncio.wait_for(agent.run(context), timeout=agent.timeout_seconds)
            except asyncio.TimeoutError:
                if attempt == 1:
                    logger.warning("Agent %s timed out, retrying once", agent.name)
                    continue
                return None, "timeout", f"took longer than {agent.timeout_seconds:.0f}s, twice"
            except AgentError as exc:
                return None, "failed", str(exc)[:500]
            except Exception as exc:  # noqa: BLE001
                # An agent calls the network and third-party code. It must
                # not be able to take the conversation down with it.
                logger.exception("Agent %s raised unexpectedly", agent.name)
                return None, "failed", f"unexpected {type(exc).__name__}: {exc}"[:500]
            else:
                return result, "ok", None
        return None, "failed", "unreachable"  # pragma: no cover
