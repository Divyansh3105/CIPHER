"""Deciding whether a tool is needed, and which (Phase 5).

One extra LLM call that returns JSON: `{"tool": "web_search"|null, "query": "..."}`.

Why not provider function-calling, which both Gemini and Groq support: their
schemas are incompatible, and `LLMProvider` (app/llm/base.py) exists so that
nothing above it knows which provider answered. Threading two vendor formats
through that interface would undo the abstraction the whole project is built
on -- swapping providers is supposed to be a config change
(docs/architecture.md Section 4). A plain-text planner works on any model
that can follow an instruction, which is all of them.

What it costs, stated plainly: one additional round trip on every message,
adding roughly a second. That is why `_OBVIOUSLY_NO_TOOL` short-circuits the
common cases before the call is made -- greetings, thanks, and anything short
enough that no tool could help.

The bias is deliberately towards *not* using a tool. A false positive is
slow, noisy and occasionally wrong; a false negative just answers from the
model's own knowledge, which is what the previous four phases did anyway.
"""
import json
import logging
import re
from dataclasses import dataclass

from app.llm.base import LLMMessage, LLMProviderError
from app.llm.router import LLMRouter
from app.tools.base import Tool

logger = logging.getLogger(__name__)

#: Below this many words, no tool is worth a round trip.
MIN_WORDS_FOR_PLANNING = 3

#: Cheap pre-filter for messages that are obviously conversational. Matched
#: against the whole message, not a prefix: "thanks" is small talk, "thanks,
#: now search for X" is not.
_OBVIOUSLY_NO_TOOL = re.compile(
    r"^\s*(?:hi|hey|hello|yo|thanks|thank you|thx|ok|okay|cool|nice|got it|sure|"
    r"good morning|good evening|good night|bye|goodbye|how are you|what'?s up)"
    r"[\s.!?]*$",
    re.IGNORECASE,
)

_PLANNER_PROMPT = """\
You route a user's message to at most one tool. Reply with JSON only.

Available tools:
{tools}

Reply with exactly one JSON object and nothing else:
{{"tool": "<tool name>", "query": "<what to look up>"}}
or
{{"tool": null}}

Rules:
- Choose null unless a tool clearly helps. Answering from your own knowledge is fine and is the default.
- Choose null for greetings, small talk, opinions, arithmetic, writing tasks, and anything about the assistant itself.
- Choose null if the conversation above already contains the answer.
- "query" is what to search for, not the user's message verbatim. Make it a good standalone search.
- Never choose a tool that is not in the list.

User's message:
{message}"""


@dataclass(frozen=True)
class ToolPlan:
    tool_name: str | None
    query: str = ""
    #: Why no tool was chosen, for logging and the preflight harness. Not
    #: shown to the user.
    reason: str = ""


def _parse(raw: str, allowed: set[str]) -> ToolPlan:
    """Parse the planner's reply, refusing anything unrecognised.

    Same rule as app/llm/registry.py: an unknown name is refused rather than
    matched to the nearest one. A planner that hallucinates "search_web"
    should produce no tool call, not a guess at which real tool was meant.
    """
    text = raw.strip()
    # Models wrap JSON in fences despite being told not to.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return ToolPlan(None, reason="planner did not return JSON")

    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return ToolPlan(None, reason="planner returned malformed JSON")

    if not isinstance(payload, dict):
        return ToolPlan(None, reason="planner returned JSON that was not an object")

    name = payload.get("tool")
    if name in (None, "", "null", "none"):
        return ToolPlan(None, reason="planner chose no tool")
    if not isinstance(name, str) or name not in allowed:
        return ToolPlan(None, reason=f"planner named an unknown tool {name!r}")

    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolPlan(None, reason="planner chose a tool but gave no query")

    return ToolPlan(name, query.strip()[:400])


class ToolPlanner:
    def __init__(self, llm_router: LLMRouter) -> None:
        self._router = llm_router

    async def plan(self, message: str, tools: list[Tool]) -> ToolPlan:
        """Decide which tool, if any, should run for this message.

        Never raises: a planner failure means "no tool", because the reply
        that follows is strictly better than an error.
        """
        if not tools:
            return ToolPlan(None, reason="no tools available")
        if len(message.split()) < MIN_WORDS_FOR_PLANNING:
            return ToolPlan(None, reason="message too short to need a tool")
        if _OBVIOUSLY_NO_TOOL.match(message):
            return ToolPlan(None, reason="small talk")

        catalogue = "\n".join(f"- {t.name}: {t.description}" for t in tools)
        prompt = _PLANNER_PROMPT.format(tools=catalogue, message=message[:2000])

        try:
            response, _ = await self._router.generate([LLMMessage(role="user", content=prompt)])
        except LLMProviderError as exc:
            logger.warning("Tool planning failed, continuing without a tool: %s", exc)
            return ToolPlan(None, reason=f"planner call failed: {exc}")

        plan = _parse(response.content, {t.name for t in tools})
        logger.info("Tool plan: %s (%s)", plan.tool_name or "none", plan.reason or plan.query)
        return plan
