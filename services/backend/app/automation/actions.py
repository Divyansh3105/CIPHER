"""The action allowlist: the only things CIPHER can do to a computer.

docs/architecture.md Section 9 is unusually specific about what this must
never do, and the list is worth restating because it is the specification:
never bypass OS authentication, never touch system security settings without
per-action confirmation, never execute arbitrary shell commands, and never
act outside an approved category. This module is where "never execute
arbitrary shell commands" is enforced structurally rather than by intention.

There is no `run_command` action and no way to add one at runtime. Every
action is a class registered here at import time, each one validating its own
arguments against a narrow schema before touching anything. An LLM cannot
introduce a new action; it can only name one of these, and naming one that
does not exist is refused rather than matched to the nearest.

**Risk tiers** map to Section 9's permission model:

  read_only  Reports, never changes anything. Available without any grant,
             because refusing to let the assistant *look* would make it
             useless while protecting nothing.
  session    Needs an approval that lasts for the session and then expires.
  sensitive  ALWAYS needs an individual confirmation, even when a session
             grant or a trusted-allowlist entry exists. Section 9: "session
             approval never silently covers these."

`open_app` is deliberately `sensitive`. It is the highest-consequence thing
in this allowlist -- launching software is the closest analogue here to the
"installing software" example Section 9 gives -- and having a real inhabitant
of the sensitive tier is what keeps the escalation path exercised rather than
theoretical.

**Off by default.** Nothing here runs unless AUTOMATION_ENABLED is true in
the environment. A fresh clone of this repository cannot act on anyone's
machine, which is the only safe default for code that opens applications.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import get_settings


class ActionError(Exception):
    """An action could not run, for a reason the user should see."""


class RiskTier:
    READ_ONLY = "read_only"
    SESSION = "session"
    SENSITIVE = "sensitive"


@dataclass(frozen=True)
class ActionResult:
    summary: str
    detail: str = ""


class Action(ABC):
    #: Stable identifier, recorded in every audit row. Renaming one breaks
    #: the log's continuity, so treat it as an interface.
    name: str
    description: str
    #: Grouping used by session approval: a user approves categories, not
    #: individual actions (Section 9).
    category: str
    risk: str

    @abstractmethod
    def validate(self, arguments: dict) -> dict:
        """Check and normalise arguments, or raise ActionError.

        Runs before any permission check, so a malformed request is refused
        without ever reaching an approval prompt -- and before execution, so
        no action ever sees an argument it has not inspected itself.
        """
        raise NotImplementedError

    @abstractmethod
    async def execute(self, arguments: dict) -> ActionResult:
        raise NotImplementedError


# --- read-only ------------------------------------------------------------


class SystemInfoAction(Action):
    name = "system_info"
    description = "Report the operating system, hostname and Python version. Changes nothing."
    category = "inspect"
    risk = RiskTier.READ_ONLY

    def validate(self, arguments: dict) -> dict:
        return {}

    async def execute(self, arguments: dict) -> ActionResult:
        return ActionResult(
            summary=f"{platform.system()} {platform.release()}",
            detail=(
                f"system={platform.system()} release={platform.release()} "
                f"machine={platform.machine()} python={platform.python_version()}"
            ),
        )


class ListDirectoryAction(Action):
    """List a directory, restricted to explicitly allowed roots.

    The allowlist is the whole point. Without it this is a filesystem
    enumeration primitive pointed at the user's entire disk, which is not
    something an LLM should be able to invoke on a hunch.
    """

    name = "list_directory"
    description = (
        "List the files in one of the directories the user has explicitly allowed. "
        "Reads names only; opens nothing and changes nothing."
    )
    category = "inspect"
    risk = RiskTier.READ_ONLY

    #: Comma-separated absolute paths in AUTOMATION_ALLOWED_DIRS. Empty means
    #: no directory is readable, which is the default.
    @staticmethod
    def allowed_roots() -> list[Path]:
        raw = _setting("AUTOMATION_ALLOWED_DIRS", "automation_allowed_dirs")
        roots = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                try:
                    roots.append(Path(part).expanduser().resolve())
                except OSError:
                    continue
        return roots

    def validate(self, arguments: dict) -> dict:
        raw = str(arguments.get("path", "")).strip()
        if not raw:
            raise ActionError("No directory was given.")

        roots = self.allowed_roots()
        if not roots:
            raise ActionError(
                "No directories are allowed. Set AUTOMATION_ALLOWED_DIRS to a comma-separated "
                "list of absolute paths before this action can run."
            )

        try:
            candidate = Path(raw).expanduser().resolve()
        except OSError as exc:
            raise ActionError(f"That path could not be read ({type(exc).__name__}).") from exc

        # resolve() first, then check containment: without resolving, a path
        # containing ".." escapes the allowlist while looking like it is
        # inside it.
        for root in roots:
            if candidate == root or root in candidate.parents:
                return {"path": str(candidate)}

        raise ActionError(
            f"{candidate} is outside every allowed directory. Allowed: "
            f"{', '.join(str(r) for r in roots)}."
        )

    async def execute(self, arguments: dict) -> ActionResult:
        path = Path(arguments["path"])
        if not path.is_dir():
            raise ActionError(f"{path} is not a directory.")
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        shown = entries[:200]
        detail = "\n".join(shown)
        if len(entries) > len(shown):
            detail += f"\n... and {len(entries) - len(shown)} more"
        return ActionResult(summary=f"{len(entries)} entries in {path}", detail=detail)


# --- session tier ---------------------------------------------------------


class OpenUrlAction(Action):
    name = "open_url"
    description = "Open a web address in the default browser."
    category = "browse"
    risk = RiskTier.SESSION

    def validate(self, arguments: dict) -> dict:
        raw = str(arguments.get("url", "")).strip()
        if not raw:
            raise ActionError("No URL was given.")

        parsed = urlparse(raw)
        # Scheme allowlist, not a blocklist. `file://` reads the disk,
        # `javascript:` executes, and a blocklist is a promise to have
        # thought of every scheme anyone will ever invent.
        if parsed.scheme not in ("http", "https"):
            raise ActionError(f"Only http and https URLs can be opened; got {parsed.scheme or 'no'} scheme.")
        if not parsed.netloc:
            raise ActionError("That URL has no host.")
        return {"url": raw}

    async def execute(self, arguments: dict) -> ActionResult:
        import webbrowser

        # webbrowser rather than a shell command: it takes the URL as a value,
        # so there is no string for a crafted URL to break out of.
        if not webbrowser.open(arguments["url"]):
            raise ActionError("No browser could be opened.")
        return ActionResult(summary=f"opened {arguments['url']}")


# --- sensitive tier -------------------------------------------------------


class OpenAppAction(Action):
    """Launch an application, from an allowlist, with per-action confirmation.

    Sensitive rather than session-tier on purpose. Launching software is the
    highest-consequence action here and the closest analogue to Section 9's
    "installing software" example, so it never runs on a session grant alone.
    """

    name = "open_app"
    description = "Launch one of the applications the user has explicitly allowed."
    category = "apps"
    risk = RiskTier.SENSITIVE

    @staticmethod
    def allowed_apps() -> dict[str, str]:
        """name -> executable, from AUTOMATION_ALLOWED_APPS ("code=code,notes=notepad")."""
        raw = _setting("AUTOMATION_ALLOWED_APPS", "automation_allowed_apps")
        apps: dict[str, str] = {}
        for part in raw.split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            label, executable = part.split("=", 1)
            label, executable = label.strip().lower(), executable.strip()
            if label and executable:
                apps[label] = executable
        return apps

    def validate(self, arguments: dict) -> dict:
        label = str(arguments.get("app", "")).strip().lower()
        if not label:
            raise ActionError("No application was named.")

        apps = self.allowed_apps()
        if not apps:
            raise ActionError(
                "No applications are allowed. Set AUTOMATION_ALLOWED_APPS to entries like "
                "'editor=code,notes=notepad' before this action can run."
            )
        if label not in apps:
            # Refuse rather than fuzzy-match, the same rule as the model
            # registry and the tool planner. Launching the nearest-named
            # program to the one that was asked for is exactly the class of
            # helpful guess this project does not make.
            raise ActionError(f"{label!r} is not an allowed application. Allowed: {', '.join(sorted(apps))}.")

        executable = apps[label]
        if shutil.which(executable) is None:
            raise ActionError(f"{label!r} maps to {executable!r}, which is not on PATH.")
        return {"app": label, "executable": executable}

    async def execute(self, arguments: dict) -> ActionResult:
        # A list, never a string, and shell=False: there is no shell for an
        # argument to be interpreted by. The executable came from the
        # allowlist, not from the model.
        try:
            subprocess.Popen([arguments["executable"]], shell=False)
        except OSError as exc:
            raise ActionError(f"Could not launch {arguments['app']}: {exc}") from exc
        return ActionResult(summary=f"launched {arguments['app']}")


#: The complete allowlist. There is no runtime registration path on purpose.
ACTIONS: dict[str, Action] = {
    action.name: action
    for action in (
        SystemInfoAction(),
        ListDirectoryAction(),
        OpenUrlAction(),
        OpenAppAction(),
    )
}


def get_action(name: str) -> Action:
    action = ACTIONS.get(name)
    if action is None:
        raise ActionError(
            f"There is no action called {name!r}. Available: {', '.join(sorted(ACTIONS))}."
        )
    return action


def _setting(env_name: str, settings_attr: str) -> str:
    """An environment variable if set, otherwise the .env-backed setting.

    Both, in that order, for a reason each: os.environ first so a value can
    be changed live (and so tests can monkeypatch it), Settings second
    because uvicorn does not load .env into the process environment -- and an
    os.environ-only read silently ignored the very file this project
    documents these settings in. Caught by preflight against a running
    server, not by any unit test, since the tests set the environment
    directly.
    """
    raw = os.environ.get(env_name)
    if raw is not None:
        return raw
    return str(getattr(get_settings(), settings_attr, "") or "")


def automation_enabled() -> bool:
    """The master switch. Nothing runs unless this is true."""
    return _setting("AUTOMATION_ENABLED", "automation_enabled").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
