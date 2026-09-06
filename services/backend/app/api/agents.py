"""GET /agents and GET /agents/runs -- the activity trail (Phase 6).

The runs endpoint is the point of the phase. Multi-agent systems fail in a
particular way: something invisible decides something, and the only evidence
is an answer that is subtly worse than it should have been. A per-run record
with input, output, status, error and duration turns "why was that answer
ungrounded?" from a guess into a lookup.

Which is why failed, timed-out and did-nothing runs are returned alongside
successful ones. An activity view that only lists what worked is a view that
lies by omission.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import Orchestrator
from app.agents.registry import get_orchestrator
from app.api.deps import get_current_user_id
from app.core.database import get_session
from app.models.db import Agent as AgentRow
from app.models.db import AgentRun
from app.models.schemas import AgentInfo, AgentRunOut, AgentToggle

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentInfo])
async def list_agents(
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> list[AgentInfo]:
    # Which agents EXIST comes from the orchestrator, because that is what
    # actually routes -- reading the table instead could describe a
    # specialist that is not reachable. The table only says which are
    # switched off.
    rows = await session.execute(select(AgentRow.name).where(AgentRow.enabled.is_(False)))
    disabled = {name for (name,) in rows}
    return [
        AgentInfo(
            name=agent.name,
            description=agent.description,
            timeout_seconds=agent.timeout_seconds,
            enabled=agent.name not in disabled,
        )
        for agent in orchestrator.agents()
    ]


@router.get("/runs", response_model=list[AgentRunOut])
async def list_agent_runs(
    limit: int = Query(default=50, ge=1, le=200),
    conversation_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> list[AgentRunOut]:
    query = select(AgentRun).where(AgentRun.user_id == user_id)
    if conversation_id is not None:
        query = query.where(AgentRun.conversation_id == conversation_id)
    # `id` as a tiebreaker for the same reason as /documents: created_at has
    # one-second resolution and two runs in the same tick would otherwise
    # swap order between requests.
    query = query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(limit)

    result = await session.execute(query)
    return [AgentRunOut.model_validate(run) for run in result.scalars().all()]


@router.patch("/{agent_name}", response_model=AgentInfo)
async def set_agent_enabled(
    agent_name: str,
    payload: AgentToggle,
    session: AsyncSession = Depends(get_session),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> AgentInfo:
    """Switch a specialist on or off.

    Declared last on this prefix, after every literal path. Nothing collides
    today -- `/agents/runs` is a GET and this is a PATCH -- but the moment
    someone adds `GET /agents/{agent_name}` above it, `/agents/runs` becomes
    a request to describe an agent called "runs". /memory/all and
    /memory/graph both hit that trap; keeping parameterised routes last is
    how this file avoids being the third.
    """
    agent = orchestrator.get(agent_name)
    if agent is None:
        # Refuse an unknown name rather than creating a row for it: a
        # settings table full of agents that do not exist is how a toggle
        # silently stops corresponding to anything.
        known = ", ".join(a.name for a in orchestrator.agents())
        raise HTTPException(status_code=404, detail=f"No agent called {agent_name!r}. Available: {known}.")

    existing = await session.execute(select(AgentRow).where(AgentRow.name == agent_name))
    row = existing.scalar_one_or_none()
    if row is None:
        row = AgentRow(name=agent_name, description=agent.description, enabled=payload.enabled)
        session.add(row)
    else:
        row.enabled = payload.enabled
        row.description = agent.description
    await session.commit()

    return AgentInfo(
        name=agent.name,
        description=agent.description,
        timeout_seconds=agent.timeout_seconds,
        enabled=payload.enabled,
    )
