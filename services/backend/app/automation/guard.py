"""The permission gate, the audit log, and the kill switch.

docs/architecture.md Section 14: "Every tool call passes through the Security
Agent / permission check *before* execution, not just before display -- never
trust the LLM's own judgment as the sole gate for sensitive actions."

That sentence shapes this module. `AutomationGuard.execute` is the only way
to run an action, the permission check happens inside it, and nothing else in
the codebase imports `Action.execute`. There is no path where a caller can
decide for itself that something is allowed -- not the orchestrator, not an
agent, and certainly not a model.

Four rules, each from Section 9:

1. **Read-only needs no grant.** Refusing to let the assistant look at
   anything protects nothing and makes it useless.
2. **Session grants expire.** They cover a category for a while, then stop.
3. **Sensitive actions always need an individual confirmation**, even when a
   session grant or a trusted-allowlist entry would otherwise cover them.
   "Session approval never silently covers these."
4. **Everything is logged** -- approved, denied, executed and failed alike,
   and logged even when the log write is the only thing that happened.

The kill switch is checked immediately before execution rather than only at
the start, so stopping mid-sequence actually stops the next action.
"""
import logging
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.actions import (
    Action,
    ActionError,
    RiskTier,
    automation_enabled,
    get_action,
)
from app.models.db import ActivityLog, Permission

logger = logging.getLogger(__name__)

#: How long a session grant lasts. Section 9 says "expires when the session
#: ends"; there is no server-side session yet (auth is deferred), so this is
#: a wall-clock stand-in, deliberately short.
SESSION_GRANT_MINUTES = 60

#: Argument keys never written to the audit log in full.
_REDACT_KEYS = {"password", "token", "secret", "key", "api_key", "authorization"}


class PermissionDenied(Exception):
    """The action was refused. Carries whether confirmation would help."""

    def __init__(self, message: str, *, needs_confirmation: bool = False) -> None:
        super().__init__(message)
        self.needs_confirmation = needs_confirmation


@dataclass(frozen=True)
class ExecutionOutcome:
    action_name: str
    outcome: str
    summary: str
    detail: str = ""


def _redact(arguments: dict) -> dict:
    return {
        key: ("[redacted]" if key.lower() in _REDACT_KEYS else value)
        for key, value in arguments.items()
    }


class KillSwitch:
    """Process-wide stop.

    In-memory on purpose. A kill switch that needs a database round trip to
    take effect is one that keeps working for the duration of an outage,
    which is exactly when someone is most likely to be reaching for it.
    Single-process is a real limitation and is written down rather than
    glossed: with more than one worker, this stops the worker that received
    the request. See README Section 5, Phase 7.
    """

    def __init__(self) -> None:
        self._engaged = False

    @property
    def engaged(self) -> bool:
        return self._engaged

    def engage(self) -> None:
        logger.warning("Kill switch ENGAGED: automation halted")
        self._engaged = True

    def release(self) -> None:
        logger.warning("Kill switch released: automation permitted again")
        self._engaged = False


class AutomationGuard:
    def __init__(self, kill_switch: KillSwitch) -> None:
        self._kill_switch = kill_switch

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    async def _active_grant(
        self, session: AsyncSession, *, user_id: UUID, names: list[str]
    ) -> Permission | None:
        """The newest live grant covering any of `names`.

        Revoked and expired rows are excluded here rather than deleted, so
        the history of what was granted and when stays readable.
        """
        now = datetime.now(timezone.utc)
        result = await session.execute(
            select(Permission)
            .where(
                Permission.user_id == user_id,
                Permission.action_name.in_(names),
                Permission.revoked_at.is_(None),
            )
            .order_by(Permission.granted_at.desc())
        )
        for grant in result.scalars().all():
            if grant.expires_at is None:
                return grant
            expires = grant.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires > now:
                return grant
        return None

    async def check(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        action: Action,
        confirmed: bool,
    ) -> None:
        """Raise PermissionDenied unless this action may run right now."""
        if not automation_enabled():
            raise PermissionDenied(
                "Automation is switched off. Set AUTOMATION_ENABLED=true to allow any action at all."
            )
        if self._kill_switch.engaged:
            raise PermissionDenied("The kill switch is engaged. Release it before running anything.")

        if action.risk == RiskTier.READ_ONLY:
            return

        if action.risk == RiskTier.SENSITIVE:
            # Rule 3. Checked BEFORE looking for a grant, so no grant can
            # ever satisfy it -- the ordering is the enforcement.
            if not confirmed:
                raise PermissionDenied(
                    f"{action.name} is a sensitive action and needs an explicit confirmation "
                    f"every time, even with a session approval.",
                    needs_confirmation=True,
                )
            return

        grant = await self._active_grant(
            session, user_id=user_id, names=[action.name, action.category]
        )
        if grant is None:
            raise PermissionDenied(
                f"No active approval for {action.category!r}. Approve it for this session first.",
                needs_confirmation=True,
            )

    async def execute(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        action_name: str,
        arguments: dict,
        persona: str | None = None,
        confirmed: bool = False,
    ) -> ExecutionOutcome:
        """Validate, authorise, run, and log. The only way to run an action."""
        # Resolve and validate first: a malformed request is refused without
        # ever reaching an approval prompt, and no action sees an argument it
        # has not inspected itself.
        try:
            action = get_action(action_name)
        except ActionError as exc:
            await self._log(
                session, user_id=user_id, action_name=action_name, category="unknown",
                risk="unknown", persona=persona, arguments=arguments,
                outcome="blocked", reason=str(exc),
            )
            raise PermissionDenied(str(exc)) from exc

        try:
            validated = action.validate(arguments)
        except ActionError as exc:
            await self._log(
                session, user_id=user_id, action_name=action.name, category=action.category,
                risk=action.risk, persona=persona, arguments=arguments,
                outcome="blocked", reason=str(exc),
            )
            raise PermissionDenied(str(exc)) from exc

        try:
            await self.check(session, user_id=user_id, action=action, confirmed=confirmed)
        except PermissionDenied as exc:
            await self._log(
                session, user_id=user_id, action_name=action.name, category=action.category,
                risk=action.risk, persona=persona, arguments=validated,
                outcome="denied", reason=str(exc),
            )
            raise

        # Re-checked here rather than only above: a long approval prompt or a
        # slow validation must not leave a window in which STOP was pressed
        # and the action runs anyway.
        if self._kill_switch.engaged:
            reason = "The kill switch was engaged before this action started."
            await self._log(
                session, user_id=user_id, action_name=action.name, category=action.category,
                risk=action.risk, persona=persona, arguments=validated,
                outcome="denied", reason=reason,
            )
            raise PermissionDenied(reason)

        await self._log(
            session, user_id=user_id, action_name=action.name, category=action.category,
            risk=action.risk, persona=persona, arguments=validated, outcome="approved",
        )

        try:
            result = await action.execute(validated)
        except ActionError as exc:
            await self._log(
                session, user_id=user_id, action_name=action.name, category=action.category,
                risk=action.risk, persona=persona, arguments=validated,
                outcome="failed", reason=str(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Action %s raised unexpectedly", action.name)
            await self._log(
                session, user_id=user_id, action_name=action.name, category=action.category,
                risk=action.risk, persona=persona, arguments=validated,
                outcome="failed", reason=f"unexpected {type(exc).__name__}: {exc}",
            )
            raise ActionError(f"{action.name} failed unexpectedly ({type(exc).__name__}).") from exc

        await self._log(
            session, user_id=user_id, action_name=action.name, category=action.category,
            risk=action.risk, persona=persona, arguments=validated,
            outcome="executed", result=result.summary,
        )
        return ExecutionOutcome(
            action_name=action.name, outcome="executed", summary=result.summary, detail=result.detail
        )

    async def _log(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        action_name: str,
        category: str,
        risk: str,
        persona: str | None,
        arguments: dict,
        outcome: str,
        reason: str | None = None,
        result: str | None = None,
    ) -> None:
        session.add(
            ActivityLog(
                id=uuid4(),
                user_id=user_id,
                action_name=action_name,
                category=category,
                risk=risk,
                persona=persona,
                arguments=_redact(arguments),
                outcome=outcome,
                reason=reason,
                result=result,
            )
        )
        # Flushed, not committed: the caller owns the transaction. Flushing
        # means a later failure in the same request cannot leave the log
        # claiming something ran when the row was never written.
        await session.flush()


@lru_cache
def get_guard() -> AutomationGuard:
    """Process-wide singleton.

    Deliberately a singleton rather than per-request: the kill switch lives
    on it, and a per-request guard would mean STOP applied only to the
    request that pressed it.
    """
    return AutomationGuard(KillSwitch())


async def grant_permission(
    session: AsyncSession,
    *,
    user_id: UUID,
    action_name: str,
    level: str,
) -> Permission:
    """Approve a category for the session, or add it to the trusted allowlist."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=SESSION_GRANT_MINUTES)
        if level == "session"
        else None
    )
    permission = Permission(
        id=uuid4(),
        user_id=user_id,
        action_name=action_name,
        level=level,
        expires_at=expires_at,
    )
    session.add(permission)
    await session.flush()
    return permission


async def revoke_permissions(session: AsyncSession, *, user_id: UUID, action_name: str) -> int:
    """Revoke every live grant for a name. Rows are marked, never deleted."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Permission).where(
            Permission.user_id == user_id,
            Permission.action_name == action_name,
            Permission.revoked_at.is_(None),
        )
    )
    grants = result.scalars().all()
    for grant in grants:
        grant.revoked_at = now
    await session.flush()
    return len(grants)
