"""The coding agent: code generation and debugging (Phase 6).

Produces a technical draft with a code-focused prompt, which the persona
then delivers in its own voice. It does not write the final message -- see
app/agents/base.py for why that rule is not negotiable.

What this actually buys, since "a second LLM call about code" could easily
be ceremony: the drafting prompt is free to be blunt and specific in ways a
persona prompt cannot be. JARVIS is told to lead with conclusions and stay
brief; that is a bad instruction for a debugging answer, where the useful
reply names the likely cause, shows the fix, and says what it assumed. So
the draft is written under engineering instructions and then delivered under
persona instructions, instead of one prompt trying to be both.
"""
import logging

from app.agents.base import Agent, AgentContext, AgentError, AgentResult
from app.llm.base import LLMMessage, LLMProviderError
from app.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_CODING_PROMPT = """\
You are the coding specialist inside a larger assistant. Produce a technical
draft that another component will rewrite in its own voice. Write for a
competent developer.

Rules:
- Lead with the actual answer: the cause, or the code, not a preamble.
- Include complete, runnable code when code is asked for. No placeholder
  comments standing in for logic you were asked to write.
- Name your assumptions explicitly when the question is ambiguous, and
  answer the most likely reading rather than asking a question back.
- If the question cannot be answered without information you do not have
  (a file, an error message, a version), say exactly what is missing.
- Do not add pleasantries, apologies, or a summary of what you just said.
  Something else supplies the voice.

The user's message:
{message}"""


class CodingAgent(Agent):
    name = "coding"
    description = (
        "Writes, explains, reviews or debugs code, and answers questions about programming "
        "languages, libraries, error messages, stack traces and software design. "
        "Use it when the answer would contain code or diagnose a technical failure. "
        "Do NOT use it for general questions that merely mention software."
    )
    timeout_seconds = 60.0

    def __init__(self, llm_router: LLMRouter) -> None:
        self._router = llm_router

    async def run(self, context: AgentContext) -> AgentResult:
        messages = [LLMMessage(role="system", content=_CODING_PROMPT.format(message=context.message))]
        # Recent turns only. A debugging question usually depends on the last
        # thing shown, and sending the whole thread doubles the cost of an
        # already-doubled call for context the draft rarely uses.
        for role, content in context.history[-6:]:
            messages.append(LLMMessage(role=role, content=content))

        try:
            response, _ = await self._router.generate(messages)
        except LLMProviderError as exc:
            raise AgentError(f"The coding specialist could not be reached: {exc}") from exc

        draft = response.content.strip()
        if not draft:
            raise AgentError("The coding specialist returned nothing.")

        return AgentResult(
            context=(
                "A coding specialist drafted the following answer. Deliver it in your own "
                "voice: keep the technical content, the code and the assumptions exactly as "
                "they are, and do not silently change any of it. If it is wrong or "
                "incomplete, say so rather than papering over it.\n\n"
                f"{draft}"
            ),
            summary="drafted a technical answer",
            output=draft,
        )
