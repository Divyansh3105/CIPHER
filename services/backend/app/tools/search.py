"""Web search (Phase 5), with a provider abstraction and a keyless default.

The blueprint names Tavily and reserves SEARCH_API_KEY for it. That key is
still a placeholder in this project's .env, and a tool that cannot run until
someone signs up for something is a tool that does not exist -- so search
degrades to DuckDuckGo's Instant Answer API, which needs no key, no account
and no card.

The honest part, stated here because it matters more than the code: those
two providers are not equivalent. Tavily returns ranked full-text results
from across the web. DuckDuckGo's Instant Answer API returns an abstract and
related topics -- effectively encyclopaedic lookups. It answers "who is Ada
Lovelace" well and "what did the Fed do yesterday" not at all. Which one ran
is reported in every result and shown in the UI, because a search tool that
quietly answers from a weaker source than the user assumes is how a system
earns distrust.

Set SEARCH_API_KEY (https://app.tavily.com, free tier) and Tavily is used
automatically. Nothing else changes.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import Settings, get_settings
from app.tools.base import Tool, ToolError, ToolResult

logger = logging.getLogger(__name__)

#: Results injected into a prompt. More than this and the search output
#: crowds out the conversation; fewer and a question with a split answer
#: gets half of it.
SEARCH_TOP_K = 4

#: Per-result snippet ceiling. Whole pages do not fit and are not needed --
#: the citation carries the URL for anyone who wants the rest.
SNIPPET_CHARS = 700

SEARCH_TIMEOUT_SECONDS = 12.0

#: Placeholder value shipped in .env.example. Treated as absent, so a user
#: who copied the template without editing it gets the keyless provider
#: rather than a confusing 401 from Tavily.
_PLACEHOLDER_KEYS = {"", "your-tavily-or-serpapi-key", "your-search-api-key"}


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(ABC):
    name: str
    #: Shown to the user alongside results. Says what this provider actually
    #: is, so nobody mistakes an encyclopaedia lookup for a web crawl.
    caveat: str = ""

    @abstractmethod
    async def search(self, query: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self._api_key,
                        "query": query,
                        "max_results": limit,
                        "search_depth": "basic",
                    },
                )
        except httpx.HTTPError as exc:
            raise ToolError(f"Web search could not reach Tavily ({type(exc).__name__}).") from exc

        if response.status_code == 401:
            raise ToolError("Tavily rejected SEARCH_API_KEY. Check it, or clear it to fall back to DuckDuckGo.")
        if response.status_code != 200:
            raise ToolError(f"Tavily returned {response.status_code}.")

        payload = response.json()
        return [
            SearchResult(
                title=item.get("title") or item.get("url", ""),
                url=item.get("url", ""),
                snippet=(item.get("content") or "")[:SNIPPET_CHARS],
            )
            for item in (payload.get("results") or [])[:limit]
        ]


class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo's Instant Answer API: no key, no account, real limits.

    The official JSON endpoint, not scraped HTML -- scraping their results
    page would be both fragile and against their terms. The trade-off is
    coverage: this returns an abstract and related topics rather than ranked
    web results, so it is good at defined things and bad at recent events.
    That limitation is surfaced, not hidden.
    """

    name = "duckduckgo"
    caveat = (
        "DuckDuckGo Instant Answers: encyclopaedic lookups only, no ranked web results "
        "and nothing about recent events. Set SEARCH_API_KEY for real web search."
    )

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                    headers={"User-Agent": "CIPHER/0.5 (personal assistant project)"},
                )
        except httpx.HTTPError as exc:
            raise ToolError(f"Web search could not reach DuckDuckGo ({type(exc).__name__}).") from exc

        if response.status_code != 200:
            raise ToolError(f"DuckDuckGo returned {response.status_code}.")

        # The endpoint advertises JSON but has been observed serving
        # text/javascript; parse defensively rather than trusting the header.
        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolError("DuckDuckGo returned something that was not JSON.") from exc

        results: list[SearchResult] = []

        abstract = (payload.get("AbstractText") or "").strip()
        if abstract:
            results.append(
                SearchResult(
                    title=payload.get("Heading") or query,
                    url=payload.get("AbstractURL") or "",
                    snippet=abstract[:SNIPPET_CHARS],
                )
            )

        for topic in payload.get("RelatedTopics") or []:
            if len(results) >= limit:
                break
            # Grouped topics nest their entries under "Topics"; flatten one
            # level rather than skipping the group entirely.
            entries = topic.get("Topics") or [topic]
            for entry in entries:
                if len(results) >= limit:
                    break
                snippet = (entry.get("Text") or "").strip()
                url = entry.get("FirstURL") or ""
                if not snippet or not url:
                    continue
                results.append(
                    SearchResult(
                        title=snippet.split(" - ")[0][:120],
                        url=url,
                        snippet=snippet[:SNIPPET_CHARS],
                    )
                )

        return results[:limit]


def build_search_provider(settings: Settings) -> SearchProvider:
    key = (settings.search_api_key or "").strip()
    if key and key not in _PLACEHOLDER_KEYS:
        return TavilyProvider(api_key=key)
    return DuckDuckGoProvider()


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the public web for facts the assistant does not know: current events, "
        "specific people or organisations, documentation, prices, definitions. "
        "Do NOT use it for anything about the user themselves, for opinions, for maths, "
        "or for anything already answered by the conversation."
    )
    requires_permission = False

    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def run(self, query: str, **context) -> ToolResult:
        results = await self._provider.search(query, SEARCH_TOP_K)

        if not results:
            # Deliberately not an empty ToolResult with empty context: the
            # difference between "nothing found" and "not searched" has to
            # survive into the prompt, or the model fills the gap itself.
            return ToolResult(
                context=(
                    f'A web search for "{query}" returned no results. '
                    f"Say so plainly rather than answering from memory."
                ),
                citations=[],
                summary=f"searched the web for “{query}” — no results",
            )

        lines = [f'Web search results for "{query}":', ""]
        citations: list[dict] = []
        for index, result in enumerate(results, start=1):
            lines.append(f"[{index}] {result.title}\n{result.snippet}\nSource: {result.url}\n")
            citations.append(
                {
                    "kind": "web",
                    "title": result.title,
                    "url": result.url,
                    "snippet": result.snippet,
                    "provider": self._provider.name,
                }
            )

        if self._provider.caveat:
            lines.append(f"(Search provider note: {self._provider.caveat})")

        return ToolResult(
            context="\n".join(lines),
            citations=citations,
            summary=f"searched the web for “{query}” — {len(results)} result"
            + ("s" if len(results) != 1 else ""),
        )


def build_web_search_tool(settings: Settings | None = None) -> WebSearchTool:
    return WebSearchTool(build_search_provider(settings or get_settings()))
