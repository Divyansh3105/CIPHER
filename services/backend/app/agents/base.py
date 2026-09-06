"""The agent interface and the shared conversation state (Phase 6).

docs/architecture.md Section 5 specifies orchestrator-as-hub: agents never
talk to each other, they report back to the orchestrator, which decides what
happens next. That is far easier to debug than a peer-to-peer mesh, and it
is what this implements.

The shape that makes it work is `AgentContext` -- one state object passed to
every agent, rather than each agent holding its own. Section 5 calls for
exactly that, and the reason is concrete: with per-agent state there is no
single answer to "what did the assistant know when it replied", which is the
question the whole audit trail exists to answer.

**Agents contribute; the persona composes.** No agent writes the final
message. Each returns material the orchestrator folds into one persona-voiced
reply. This is not an aesthetic choice -- CIPHER's central rule is that a
persona is a style applied to everything the assistant says
(docs/architecture.md Section 3). If a coding agent emitted the final text
directly, asking ULTRON a code question would silently get you a different
voice, and the persona would have become a routing decision rather than a
style. The exception would have to be argued for; there is not one yet.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AgentContext:
    """Everything an agent is allowed to see.

    Passed by the orchestrator, never assembled by an agent. An agent that
    reached into the database for its own context could see something the
    reply was not built from, and the run record would then be a record of
    something that did not happen.
    """

    message: str
    user_id: UUID
    persona_id: str
    session: AsyncSession
    conversation_id: UUID | None = None
    #: Prior turns, oldest first, as (role, content).
    history: list[tuple[str, str]] = field(default_factory=list)
    #: Memories already retrieved for this turn, so an agent does not
    #: re-embed the same question the chat endpoint just embedded.
    recalled_memories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResult:
    """What an agent produced.

    `context` is prose folded into the system prompt; `citations` are
    structured sources persisted on the message. Same split as ToolResult,
    for the same reason: the model needs readable text, the UI needs
    structure, and deriving one from the other at render time is fragile.
    """

    context: str = ""
    citations: list[dict] = field(default_factory=list)
    #: One line for the activity dashboard and the run record.
    summary: str = ""
    #: Stored on the run row. Truncated by the orchestrator, not here.
    output: str = ""


class AgentError(Exception):
    """An agent failed in a way worth recording and telling the user about."""


class Agent(ABC):
    #: Stable identifier, persisted in agent_runs. Renaming one breaks the
    #: audit trail's continuity, so treat it as an interface.
    name: str
    #: Written for the *router*, not for a human: it is injected into the
    #: routing prompt and is the entire basis on which this agent is chosen
    #: or ignored. Say when to use it and, just as importantly, when not to.
    description: str
    #: Seconds before the orchestrator gives up. Section 5 requires a
    #: timeout on every agent call; the orchestrator enforces it so an agent
    #: cannot opt out of being cancelled.
    timeout_seconds: float = 45.0

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Do the work. Raise AgentError for expected failures."""
        raise NotImplementedError

    async def available(self, context: AgentContext) -> tuple[bool, str]:
        """Whether this agent can run for this request, and why not if not.

        Same rule as tools: an agent that is offered to the router but always
        fails is worse than one that is absent, because the router keeps
        choosing it and every answer becomes an apology.
        """
        return True, ""
