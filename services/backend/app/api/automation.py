"""Permissioned automation: actions, approvals, the kill switch, the log.

Every route here is a thin shell over `AutomationGuard`. That is deliberate:
`guard.execute` is the only path to running an action, so no endpoint can
decide for itself that something is allowed, and adding a new route cannot
accidentally create a second, unguarded way in.

docs/architecture.md Section 9's permission model, as endpoints:

  GET    /automation/actions        what exists, its risk tier, whether it may run now
  POST   /automation/permissions    approve a category for the session, or trust it
  DELETE /automation/permissions/{name}   revoke
  POST   /automation/execute        run one action (the only executing route)
  POST   /automation/stop           kill switch on
  POST   /automation/resume         kill switch off
  GET    /automation/log            the audit trail
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.automation.actions import ACTIONS, ActionError, RiskTier, automation_enabled
from app.automation.guard import (
    AutomationGuard,
    PermissionDenied,
    get_guard,
    grant_permission,
    revoke_permissions,
)
from app.core.database import get_session
from app.models.db import ActivityLog
from app.models.schemas import (
    ActionInfo,
    ActivityLogOut,
    AutomationExecuteRequest,
    AutomationExecuteResponse,
    AutomationStatus,
    PermissionGrantRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/actions", response_model=AutomationStatus)
async def list_actions(
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    guard: AutomationGuard = Depends(get_guard),
) -> AutomationStatus:
    actions: list[ActionInfo] = []
    for action in ACTIONS.values():
        # Ask the guard rather than reimplementing the rules here. A second
        # copy of the permission logic that exists only to colour a badge is
        # a second copy that can disagree with the one that matters.
        try:
            await guard.check(session, user_id=user_id, action=action, confirmed=False)
        except PermissionDenied as exc:
            allowed, reason, needs_confirmation = False, str(exc), exc.needs_confirmation
        else:
            allowed, reason, needs_confirmation = True, "", False

        actions.append(
            ActionInfo(
                name=action.name,
                description=action.description,
                category=action.category,
                risk=action.risk,
                allowed_now=allowed,
                reason=reason,
                needs_confirmation=needs_confirmation
                or action.risk == RiskTier.SENSITIVE,
            )
        )

    return AutomationStatus(
        enabled=automation_enabled(),
        kill_switch_engaged=guard.kill_switch.engaged,
        actions=actions,
    )


@router.post("/permissions", response_model=AutomationStatus)
async def approve(
    payload: PermissionGrantRequest,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    guard: AutomationGuard = Depends(get_guard),
) -> AutomationStatus:
    known = {a.category for a in ACTIONS.values()} | set(ACTIONS)
    if payload.action_name not in known:
        # Refuse rather than storing a grant for something that does not
        # exist -- a permissions table full of names nothing matches is how
        # an approval silently stops corresponding to anything.
        raise HTTPException(
            status_code=404,
            detail=f"No action or category called {payload.action_name!r}. Known: {', '.join(sorted(known))}.",
        )

    sensitive = [a.name for a in ACTIONS.values() if a.risk == RiskTier.SENSITIVE and a.category == payload.action_name]
    await grant_permission(
        session, user_id=user_id, action_name=payload.action_name, level=payload.level.value
    )
    await session.commit()

    status = await list_actions(session=session, user_id=user_id, guard=guard)
    if sensitive:
        # Said out loud rather than left to be discovered: approving a
        # category does NOT cover the sensitive actions inside it.
        status.notice = (
            f"Approved {payload.action_name!r}. "
            f"{', '.join(sensitive)} still needs a separate confirmation each time."
        )
    return status


@router.delete("/permissions/{action_name}", response_model=AutomationStatus)
async def revoke(
    action_name: str,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    guard: AutomationGuard = Depends(get_guard),
) -> AutomationStatus:
    await revoke_permissions(session, user_id=user_id, action_name=action_name)
    await session.commit()
    return await list_actions(session=session, user_id=user_id, guard=guard)


@router.post("/stop", response_model=AutomationStatus)
async def stop(
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    guard: AutomationGuard = Depends(get_guard),
) -> AutomationStatus:
    """The kill switch. Takes effect immediately and needs no confirmation.

    A stop control that asks "are you sure?" is not a stop control.
    """
    guard.kill_switch.engage()
    return await list_actions(session=session, user_id=user_id, guard=guard)


@router.post("/resume", response_model=AutomationStatus)
async def resume(
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    guard: AutomationGuard = Depends(get_guard),
) -> AutomationStatus:
    guard.kill_switch.release()
    return await list_actions(session=session, user_id=user_id, guard=guard)


@router.post("/execute", response_model=AutomationExecuteResponse)
async def execute(
    payload: AutomationExecuteRequest,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    guard: AutomationGuard = Depends(get_guard),
) -> AutomationExecuteResponse:
    try:
        outcome = await guard.execute(
            session,
            user_id=user_id,
            action_name=payload.action_name,
            arguments=payload.arguments,
            persona=payload.persona,
            confirmed=payload.confirmed,
        )
    except PermissionDenied as exc:
        await session.commit()  # keep the denial in the log
        # 403 with `needs_confirmation` so the UI can offer the confirmation
        # rather than just reporting a wall.
        raise HTTPException(
            status_code=403,
            detail={"message": str(exc), "needs_confirmation": exc.needs_confirmation},
        ) from exc
    except ActionError as exc:
        await session.commit()  # keep the failure in the log
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await session.commit()
    return AutomationExecuteResponse(
        action_name=outcome.action_name,
        outcome=outcome.outcome,
        summary=outcome.summary,
        detail=outcome.detail,
    )


@router.get("/log", response_model=list[ActivityLogOut])
async def activity_log(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
) -> list[ActivityLogOut]:
    """Everything attempted: approved, denied, executed, failed and blocked.

    Section 9 requires the log to record attempts, not just successes. A log
    of only what ran would hide precisely the events worth reviewing.
    """
    result = await session.execute(
        select(ActivityLog)
        .where(ActivityLog.user_id == user_id)
        .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
        .limit(min(max(limit, 1), 500))
    )
    return [ActivityLogOut.model_validate(row) for row in result.scalars().all()]
