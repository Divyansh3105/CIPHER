"""The permission gate: what must never be possible.

docs/architecture.md Section 9 lists what this system will not do by design.
These tests exist so that list stays true as the code changes, because every
item on it is invisible when working and catastrophic when not.

The tests are written as assertions about *refusal*. It is easy to test that
an allowed action runs; the ones with teeth are the ones proving that a
sensitive action cannot be reached by any amount of prior approval, that a
path with `..` in it cannot escape an allowlist, and that a denial still
leaves a row in the log.
"""
import os
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.automation.actions import (
    ACTIONS,
    ActionError,
    ListDirectoryAction,
    OpenAppAction,
    OpenUrlAction,
    RiskTier,
    get_action,
)
from app.automation.guard import (
    AutomationGuard,
    KillSwitch,
    PermissionDenied,
    grant_permission,
    revoke_permissions,
)
from app.models.db import ActivityLog


@pytest.fixture
def guard():
    return AutomationGuard(KillSwitch())


@pytest.fixture(autouse=True)
def _reset_process_guard():
    """Release the process-wide kill switch after every test.

    The guard is a singleton because the kill switch has to be -- a
    per-request one would mean STOP applied only to the request that pressed
    it. That makes it shared state across tests, so a test that engages it
    and then fails would silently block every test after it.
    """
    from app.automation.guard import get_guard

    yield
    get_guard().kill_switch.release()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")


async def _logs(session, user_id):
    result = await session.execute(
        select(ActivityLog).where(ActivityLog.user_id == user_id).order_by(ActivityLog.created_at)
    )
    return list(result.scalars().all())


# --- the master switch --------------------------------------------------


@pytest.mark.asyncio
async def test_automation_is_off_by_default(db_session, dev_user_id, guard, monkeypatch):
    """A fresh clone must not be able to act on anyone's machine."""
    monkeypatch.setenv("AUTOMATION_ENABLED", "false")

    with pytest.raises(PermissionDenied, match="switched off"):
        await guard.execute(
            db_session, user_id=dev_user_id, action_name="system_info", arguments={}
        )


@pytest.mark.asyncio
async def test_even_read_only_is_refused_while_disabled(db_session, dev_user_id, guard, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "false")
    action = get_action("system_info")
    assert action.risk == RiskTier.READ_ONLY

    with pytest.raises(PermissionDenied):
        await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=True)


# --- risk tiers ---------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_runs_without_any_grant(db_session, dev_user_id, guard, enabled):
    """Refusing to let the assistant look at anything protects nothing."""
    outcome = await guard.execute(
        db_session, user_id=dev_user_id, action_name="system_info", arguments={}
    )

    assert outcome.outcome == "executed"


@pytest.mark.asyncio
async def test_a_session_action_is_refused_without_a_grant(db_session, dev_user_id, guard, enabled):
    with pytest.raises(PermissionDenied) as excinfo:
        await guard.execute(
            db_session,
            user_id=dev_user_id,
            action_name="open_url",
            arguments={"url": "https://example.com"},
        )

    assert excinfo.value.needs_confirmation is True


@pytest.mark.asyncio
async def test_a_session_grant_covers_its_category(db_session, dev_user_id, guard, enabled):
    action = get_action("open_url")
    await grant_permission(db_session, user_id=dev_user_id, action_name=action.category, level="session")

    # check() rather than execute(): running it would open a real browser.
    await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=False)


@pytest.mark.asyncio
async def test_revoking_a_grant_takes_effect_immediately(db_session, dev_user_id, guard, enabled):
    action = get_action("open_url")
    await grant_permission(db_session, user_id=dev_user_id, action_name=action.category, level="session")
    await revoke_permissions(db_session, user_id=dev_user_id, action_name=action.category)

    with pytest.raises(PermissionDenied):
        await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=False)


@pytest.mark.asyncio
async def test_an_expired_session_grant_no_longer_counts(db_session, dev_user_id, guard, enabled):
    from datetime import datetime, timedelta, timezone

    action = get_action("open_url")
    grant = await grant_permission(
        db_session, user_id=dev_user_id, action_name=action.category, level="session"
    )
    grant.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()

    with pytest.raises(PermissionDenied):
        await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=False)


@pytest.mark.asyncio
async def test_a_trusted_grant_does_not_expire(db_session, dev_user_id, guard, enabled):
    action = get_action("open_url")
    await grant_permission(db_session, user_id=dev_user_id, action_name=action.category, level="trusted")

    await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=False)


# --- the rule that matters most -----------------------------------------


@pytest.mark.asyncio
async def test_a_session_grant_never_covers_a_sensitive_action(db_session, dev_user_id, guard, enabled):
    """Section 9: "session approval never silently covers these."

    The single most important assertion in this file. If it ever passes
    without a confirmation, the escalation tier has quietly become
    decoration.
    """
    action = get_action("open_app")
    assert action.risk == RiskTier.SENSITIVE
    await grant_permission(db_session, user_id=dev_user_id, action_name=action.category, level="session")

    with pytest.raises(PermissionDenied) as excinfo:
        await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=False)

    assert excinfo.value.needs_confirmation is True


@pytest.mark.asyncio
async def test_a_trusted_grant_never_covers_a_sensitive_action_either(db_session, dev_user_id, guard, enabled):
    """The trusted allowlist is for low-risk repeated actions, and must not
    become a way to make a sensitive one permanent.
    """
    action = get_action("open_app")
    await grant_permission(db_session, user_id=dev_user_id, action_name=action.category, level="trusted")
    await grant_permission(db_session, user_id=dev_user_id, action_name=action.name, level="trusted")

    with pytest.raises(PermissionDenied):
        await guard.check(db_session, user_id=dev_user_id, action=action, confirmed=False)


@pytest.mark.asyncio
async def test_a_confirmed_sensitive_action_is_allowed(db_session, dev_user_id, guard, enabled):
    await guard.check(
        db_session, user_id=dev_user_id, action=get_action("open_app"), confirmed=True
    )


# --- the kill switch ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_kill_switch_stops_everything_including_read_only(db_session, dev_user_id, guard, enabled):
    guard.kill_switch.engage()

    with pytest.raises(PermissionDenied, match="kill switch"):
        await guard.execute(
            db_session, user_id=dev_user_id, action_name="system_info", arguments={}
        )


@pytest.mark.asyncio
async def test_releasing_the_kill_switch_restores_normal_behaviour(db_session, dev_user_id, guard, enabled):
    guard.kill_switch.engage()
    guard.kill_switch.release()

    outcome = await guard.execute(
        db_session, user_id=dev_user_id, action_name="system_info", arguments={}
    )

    assert outcome.outcome == "executed"


# --- the allowlist is structural ----------------------------------------


def test_there_is_no_shell_or_arbitrary_command_action():
    """Section 9: never execute arbitrary shell commands.

    Enforced by there being no such action and no runtime registration path,
    rather than by a filter someone can be argued past.
    """
    names = set(ACTIONS)
    for forbidden in ("run", "run_command", "shell", "exec", "eval", "python", "cmd", "powershell"):
        assert forbidden not in names


def test_an_unknown_action_is_refused_not_matched():
    with pytest.raises(ActionError, match="no action called"):
        get_action("open_urls")


@pytest.mark.asyncio
async def test_an_unknown_action_is_logged_as_blocked(db_session, dev_user_id, guard, enabled):
    with pytest.raises(PermissionDenied):
        await guard.execute(db_session, user_id=dev_user_id, action_name="rm_rf", arguments={})

    logs = await _logs(db_session, dev_user_id)
    assert logs[-1].outcome == "blocked"
    assert logs[-1].action_name == "rm_rf"


# --- argument validation ------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "ftp://example.com",
        "https://",
        "",
    ],
)
def test_only_http_urls_can_be_opened(url):
    """A scheme allowlist, not a blocklist: a blocklist is a promise to have
    thought of every scheme anyone will ever invent.
    """
    with pytest.raises(ActionError):
        OpenUrlAction().validate({"url": url})


def test_a_directory_outside_the_allowlist_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ALLOWED_DIRS", str(tmp_path))
    outside = tmp_path.parent

    with pytest.raises(ActionError, match="outside every allowed directory"):
        ListDirectoryAction().validate({"path": str(outside)})


def test_dot_dot_cannot_escape_the_allowlist(tmp_path, monkeypatch):
    """Without resolving first, a path containing ".." escapes the allowlist
    while looking like it is inside it.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("AUTOMATION_ALLOWED_DIRS", str(allowed))

    with pytest.raises(ActionError, match="outside every allowed directory"):
        ListDirectoryAction().validate({"path": str(allowed / ".." / ".." / "elsewhere")})


def test_no_allowed_directories_means_nothing_is_readable(monkeypatch):
    monkeypatch.setenv("AUTOMATION_ALLOWED_DIRS", "")

    with pytest.raises(ActionError, match="No directories are allowed"):
        ListDirectoryAction().validate({"path": "/"})


def test_an_app_outside_the_allowlist_is_refused(monkeypatch):
    monkeypatch.setenv("AUTOMATION_ALLOWED_APPS", "editor=python")

    with pytest.raises(ActionError, match="not an allowed application"):
        OpenAppAction().validate({"app": "calculator"})


def test_a_near_miss_app_name_is_refused_not_matched(monkeypatch):
    """Launching the nearest-named program to the one that was asked for is
    exactly the class of helpful guess this project does not make.
    """
    monkeypatch.setenv("AUTOMATION_ALLOWED_APPS", "editor=python")

    with pytest.raises(ActionError, match="not an allowed application"):
        OpenAppAction().validate({"app": "edito"})


# --- the audit log ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_denial_is_logged(db_session, dev_user_id, guard, enabled):
    """Section 9 requires attempts to be logged, not just successes. A log of
    only what ran would hide precisely the events worth reviewing.
    """
    with pytest.raises(PermissionDenied):
        await guard.execute(
            db_session,
            user_id=dev_user_id,
            action_name="open_url",
            arguments={"url": "https://example.com"},
        )

    logs = await _logs(db_session, dev_user_id)
    assert [row.outcome for row in logs] == ["denied"]
    assert logs[0].action_name == "open_url"


@pytest.mark.asyncio
async def test_an_execution_logs_both_approval_and_result(db_session, dev_user_id, guard, enabled):
    await guard.execute(db_session, user_id=dev_user_id, action_name="system_info", arguments={})

    assert [row.outcome for row in await _logs(db_session, dev_user_id)] == ["approved", "executed"]


@pytest.mark.asyncio
async def test_invalid_arguments_are_logged_as_blocked(db_session, dev_user_id, guard, enabled):
    with pytest.raises(PermissionDenied):
        await guard.execute(
            db_session, user_id=dev_user_id, action_name="open_url", arguments={"url": "file:///etc/passwd"}
        )

    logs = await _logs(db_session, dev_user_id)
    assert logs[-1].outcome == "blocked"


@pytest.mark.asyncio
async def test_secrets_are_redacted_from_the_log(db_session, dev_user_id, guard, enabled):
    """The log is reviewed by a human and may be exported. It must not become
    the place credentials end up in plain text.
    """
    with pytest.raises(PermissionDenied):
        await guard.execute(
            db_session,
            user_id=dev_user_id,
            action_name="rm_rf",
            arguments={"password": "hunter2", "path": "/tmp"},
        )

    logs = await _logs(db_session, dev_user_id)
    assert logs[-1].arguments["password"] == "[redacted]"
    assert logs[-1].arguments["path"] == "/tmp"


@pytest.mark.asyncio
async def test_the_persona_is_recorded(db_session, dev_user_id, guard, enabled):
    """Section 9 says the boundary applies to all three personas equally. If
    it ever did not, this column is where that would be visible.
    """
    await guard.execute(
        db_session, user_id=dev_user_id, action_name="system_info", arguments={}, persona="ultron"
    )

    assert (await _logs(db_session, dev_user_id))[-1].persona == "ultron"


@pytest.mark.asyncio
async def test_ultron_gets_no_extra_latitude(db_session, dev_user_id, guard, enabled):
    """A persona is a style, never a permission tier."""
    action = get_action("open_app")

    for persona in ("jarvis", "friday", "ultron"):
        with pytest.raises(PermissionDenied):
            await guard.execute(
                db_session,
                user_id=dev_user_id,
                action_name=action.name,
                arguments={"app": "anything"},
                persona=persona,
            )


# --- the HTTP layer -----------------------------------------------------


async def test_actions_endpoint_reports_why_each_is_blocked(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")

    body = (await client.get("/automation/actions")).json()

    assert body["enabled"] is True
    by_name = {a["name"]: a for a in body["actions"]}
    assert by_name["system_info"]["allowed_now"] is True
    assert by_name["open_url"]["allowed_now"] is False
    assert "approval" in by_name["open_url"]["reason"].lower()
    # Every sensitive action always advertises that it needs confirming.
    assert by_name["open_app"]["needs_confirmation"] is True


async def test_actions_endpoint_says_when_automation_is_off(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "false")

    body = (await client.get("/automation/actions")).json()

    assert body["enabled"] is False
    assert all(not a["allowed_now"] for a in body["actions"])


async def test_approving_a_category_warns_about_its_sensitive_actions(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")

    body = (await client.post("/automation/permissions", json={"action_name": "apps"})).json()

    # Said out loud rather than left to be discovered later.
    assert "open_app" in body["notice"]
    assert "separate confirmation" in body["notice"]


async def test_approving_an_unknown_category_is_refused(client):
    response = await client.post("/automation/permissions", json={"action_name": "everything"})

    assert response.status_code == 404
    assert "inspect" in response.json()["detail"]


async def test_execute_refuses_a_sensitive_action_without_confirmation(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("AUTOMATION_ALLOWED_APPS", "editor=python")
    await client.post("/automation/permissions", json={"action_name": "apps"})

    response = await client.post(
        "/automation/execute", json={"action_name": "open_app", "arguments": {"app": "editor"}}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["needs_confirmation"] is True


async def test_the_stop_endpoint_blocks_everything_and_resume_restores_it(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")

    stopped = (await client.post("/automation/stop")).json()
    assert stopped["kill_switch_engaged"] is True
    blocked = await client.post("/automation/execute", json={"action_name": "system_info"})
    assert blocked.status_code == 403

    resumed = (await client.post("/automation/resume")).json()
    assert resumed["kill_switch_engaged"] is False
    ran = await client.post("/automation/execute", json={"action_name": "system_info"})
    assert ran.status_code == 200
    assert ran.json()["outcome"] == "executed"


async def test_the_log_records_denials_over_http(client, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ENABLED", "true")

    await client.post(
        "/automation/execute",
        json={"action_name": "open_url", "arguments": {"url": "https://example.com"}},
    )
    log = (await client.get("/automation/log")).json()

    assert log[0]["outcome"] == "denied"
    assert log[0]["action_name"] == "open_url"
