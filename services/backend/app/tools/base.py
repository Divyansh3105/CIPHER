"""The tool interface and its result type (Phase 5).

A tool is anything that fetches information the model does not have. Phase 5
ships two: web search and document search. Phase 7 adds tools that *act*
rather than fetch, which is a different risk class entirely -- hence
`requires_permission`, which is declared here and enforced later rather than
retrofitted onto a design that assumed every tool was safe.

Deliberately not provider function-calling. Gemini and Groq both support it,
with incompatible schemas, and `LLMProvider` (app/llm/base.py) exists
precisely so the rest of the codebase never learns which provider is
answering. Threading two vendor tool-calling formats through that interface
would undo it. Instead app/tools/planner.py asks the model, in plain text,
which tool to use -- one extra call, works on any model, and keeps the
provider swap a config change (docs/architecture.md Section 4).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ToolError(Exception):
    """A tool failed in a way worth telling the user about.

    Tools are expected to raise this rather than returning empty results:
    "the search provider is down" and "there are no results" are different
    answers, and collapsing them teaches the model to confabulate.
    """


@dataclass(frozen=True)
class ToolResult:
    """What a tool found.

    `citations` is the machine-readable record persisted on the message and
    rendered as chips; `context` is the prose actually injected into the
    prompt. They are separate because the model needs readable text while the
    UI needs structure, and deriving one from the other at render time was
    how the recalled-memory chips got fragile in Phase 3.
    """

    #: Prose injected into the system prompt. Empty means "nothing found",
    #: which the caller must pass on honestly rather than hiding.
    context: str
    #: Structured sources: see Message.citations in app/models/db.py.
    citations: list[dict] = field(default_factory=list)
    #: Short line for the UI ("searched the web for X, 3 results").
    summary: str = ""


class Tool(ABC):
    #: Stable identifier. Used by the planner's output and persisted in
    #: activity logs, so renaming one is a breaking change.
    name: str
    #: Written for the *model*, not for a human reader: it is injected into
    #: the planner prompt and is the entire basis on which the tool gets
    #: chosen or ignored. Say what it is for and, just as importantly, when
    #: not to use it.
    description: str
    #: Phase 7 gate. False for anything that only reads.
    requires_permission: bool = False

    @abstractmethod
    async def run(self, query: str, **context) -> ToolResult:
        """Execute the tool for `query`.

        `context` carries request-scoped things a tool may need (the DB
        session, the user id) without putting them in the constructor, since
        tools are process-wide singletons.
        """
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """Whether this tool can run right now, and why not if it cannot.

        Checked before the tool is offered to the planner. A tool that is
        listed but always fails is worse than one that is absent: the model
        keeps choosing it and every answer becomes an apology.
        """
        return True, ""
