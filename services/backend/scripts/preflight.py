"""Live end-to-end checks against a RUNNING CIPHER, with a pass/fail verdict.

Not unit tests, and deliberately not mocks. The 177-test suite runs against
in-memory SQLite with a fake embedder and a fake LLM provider, which is the
right way to test logic and is structurally incapable of catching the
failures that have actually cost this project time. Every one of those was
live-only:

  * Phase 1: the Supavisor transaction-mode pooler rejecting asyncpg's
    prepared statements, intermittently.
  * Phase 1: `gemini-2.5-flash` and `llama-3.3-70b-versatile` silently
    withdrawn by their providers -- the code was correct, the model ids were
    not.
  * Phase 3: memory recall returning nothing, for an hour, because a stale
    `uvicorn` from an earlier manual test was still serving the
    pre-retrieval version of the chat endpoint.
  * Phase 4: `/memory/graph` answering with the previous revision's response
    schema after an edit, for the same reason.
  * After Phase 8: speech input failing in every browser that is not Google
    Chrome, reported by a user as a network error. The fallback that fixes it
    is a live provider round trip, so only a live check can prove it works.

Green unit tests are not evidence the system works. This is.

    "Done" means preflight passed, not that the code looks right.

Add one check every time something breaks on you. A harness that grows one
check per real incident becomes the most valuable file in the repo.

Usage (from services/backend, with the backend already running):
    python -m scripts.preflight
    python -m scripts.preflight --all-models     # probe every registry model
    python -m scripts.preflight --skip-llm       # no billable calls

Exits non-zero if any check fails.
"""
import argparse
import asyncio
import io
import math
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.llm.base import LLMMessage, LLMProviderError  # noqa: E402
from app.llm.gemini import GeminiProvider  # noqa: E402
from app.llm.groq import GroqProvider  # noqa: E402
from app.llm.registry import MODEL_REGISTRY  # noqa: E402
from app.main import app  # noqa: E402
from app.memory.embedder import EMBEDDING_DIM, build_embedder  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

# Marker on everything this script writes, so cleanup can find its own
# leavings and a human reading the dashboard knows where a stray row came
# from.
MARKER = "cipher-preflight"

# Conversation ids this run created, so cleanup can delete exactly those.
#
# Cleanup used to find them by matching message CONTENT ("%archival
# subsystem%", "%verification codeword%"). That is a pattern over the user's
# real data, and it did what patterns over real data do: a genuine
# conversation that happened to discuss the same subject as a probe question
# matched, and was deleted. Ids are not a heuristic -- a conversation is
# either one this run made or it is not.
CREATED_CONVERSATIONS: set[str] = set()


def remember_conversation(payload: dict) -> None:
    """Record a conversation this run created, for scoped cleanup."""
    conversation_id = payload.get("conversation_id")
    if conversation_id:
        CREATED_CONVERSATIONS.add(str(conversation_id))


class Report:
    """Collects results and prints them as they happen."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.warned = 0

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        print(f"  [ok]   {name}" + (f"  -- {detail}" if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.failed += 1
        print(f"  [FAIL] {name}  -- {detail}")

    def warn(self, name: str, detail: str) -> None:
        self.warned += 1
        print(f"  [warn] {name}  -- {detail}")

    def summary(self) -> int:
        print()
        print(f"{self.passed} pass, {self.failed} fail, {self.warned} warn")
        if self.failed:
            print("\nPreflight FAILED. Do not call this done.")
        return 1 if self.failed else 0


def section(title: str) -> None:
    print(f"\n{title}")


async def check_server(client: httpx.AsyncClient, report: Report) -> bool:
    section("Server")
    try:
        response = await client.get("/health")
    except httpx.HTTPError as exc:
        report.fail("backend is up", f"{type(exc).__name__}: {exc}. Start it before running preflight.")
        return False
    if response.status_code != 200 or response.json().get("status") != "ok":
        report.fail("backend is up", f"/health returned {response.status_code} {response.text[:80]}")
        return False
    report.ok("backend is up")
    return True


async def check_served_code_is_this_checkout(client: httpx.AsyncClient, report: Report) -> None:
    """The stale-server check, and the reason this file exists.

    Compares the OpenAPI document the RUNNING process serves against the one
    this working copy generates. A reloader that died, a second uvicorn on
    the same port, or an edit made after the last restart all surface here --
    instead of surfacing an hour later as "my fix did nothing".

    Both documents come from `app.openapi()` rather than from `app.routes`.
    That is not a stylistic choice: `include_router` in this FastAPI version
    leaves `_IncludedRouter` objects in `app.routes` instead of flattening
    the child APIRoutes into it, so walking `app.routes` sees only `/health`
    and this check silently passed on a genuinely stale server. Found by
    feeding the check a deliberately stale spec, which is the only way that
    class of bug ever gets found.
    """
    name = "served routes match this checkout"
    try:
        served_spec = (await client.get("/openapi.json")).json()
    except (httpx.HTTPError, ValueError) as exc:
        report.fail(name, f"could not read /openapi.json: {exc}")
        return

    local_spec = app.openapi()
    served_paths = set(served_spec.get("paths", {}))
    local_paths = set(local_spec.get("paths", {}))

    problems = []
    if missing := sorted(local_paths - served_paths):
        problems.append(f"not served: {missing}")
    if extra := sorted(served_paths - local_paths):
        problems.append(f"served but not in this checkout: {extra}")

    # Route names alone miss a changed response *shape*, which is how the
    # /memory/graph staleness actually presented: the path was there, the
    # response was a revision behind. Compare schema property sets too,
    # generically -- naming a specific field here would only ever catch the
    # one bug already found.
    local_schemas = local_spec.get("components", {}).get("schemas", {})
    served_schemas = served_spec.get("components", {}).get("schemas", {})
    for schema_name, definition in local_schemas.items():
        local_props = set(definition.get("properties", {}))
        if schema_name not in served_schemas:
            problems.append(f"schema {schema_name} not served")
        elif (served_props := set(served_schemas[schema_name].get("properties", {}))) != local_props:
            drift = sorted(local_props ^ served_props)
            problems.append(f"schema {schema_name} differs on {drift}")

    if problems:
        report.fail(name, "; ".join(problems) + " -- the running server is not this code. Restart it.")
        return
    report.ok(name, f"{len(served_paths)} routes, {len(local_schemas)} schemas")


async def check_database(report: Report) -> None:
    section("Database")
    settings = get_settings()
    try:
        async with async_session_factory() as session:
            await session.scalar(text("SELECT 1"))
            report.ok("database reachable")

            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
            heads = subprocess.run(
                [sys.executable, "-m", "alembic", "heads"],
                cwd=str(Path(__file__).resolve().parents[1]),
                capture_output=True,
                text=True,
            )
            head = heads.stdout.split()[0] if heads.returncode == 0 and heads.stdout.split() else None
            if head is None:
                report.warn("schema at head", "could not read alembic heads")
            elif revision == head:
                report.ok("schema at head", revision)
            else:
                report.fail("schema at head", f"database is at {revision}, code expects {head}. Run alembic upgrade head.")

            seeded = await session.scalar(
                text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": settings.dev_user_id}
            )
            if seeded:
                report.ok("dev user seeded")
            else:
                report.fail("dev user seeded", "run scripts/seed_dev_user.py -- every request is attributed to it")

            pending = await session.scalar(text("SELECT count(*) FROM memories WHERE embedding IS NULL"))
            total = await session.scalar(text("SELECT count(*) FROM memories"))
            if pending:
                report.warn("all memories embedded", f"{pending} of {total} have no vector and are unsearchable")
            else:
                report.ok("all memories embedded", f"{total} memories")
    except Exception as exc:  # noqa: BLE001 -- a live check reports, it does not raise
        report.fail("database reachable", f"{type(exc).__name__}: {exc}")


async def check_llm(report: Report, all_models: bool) -> None:
    section("LLM providers")
    settings = get_settings()
    probe = [LLMMessage(role="user", content="Reply with the single word: ok")]
    providers = {
        "gemini": GeminiProvider(api_key=settings.gemini_api_key),
        "groq": GroqProvider(api_key=settings.groq_api_key),
    }

    specs = list(MODEL_REGISTRY) if all_models else []
    if not all_models:
        # Default: prove the two models actually used by default routing.
        specs = [s for s in MODEL_REGISTRY if "default" in s.note.lower()]

    for spec in specs:
        try:
            response = await providers[spec.provider].agenerate(probe, model=spec.id)
        except LLMProviderError as exc:
            message = str(exc)
            if "429" in message or "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                # A warn, not a fail, and the distinction is the point: the
                # model exists and works, the free-tier allowance for today
                # is spent. Nothing is broken, and the router's fallback
                # covers it -- reporting that as a failure would train
                # whoever runs this to ignore red output.
                report.warn(
                    f"model {spec.id}",
                    "free-tier quota exhausted for now; the router falls back, so chat still works",
                )
            else:
                report.fail(f"model {spec.id}", message[:120])
        else:
            report.ok(f"model {spec.id}", f"replied {response.content.strip()[:20]!r}")


async def check_embeddings(report: Report) -> None:
    section("Embeddings")
    embedder = build_embedder(get_settings())
    try:
        vectors = await embedder.aembed(["preflight embedding round trip"], task="document")
    except Exception as exc:  # noqa: BLE001
        report.fail("embedding call", f"{type(exc).__name__}: {str(exc)[:120]}")
        return
    if len(vectors) != 1 or len(vectors[0]) != EMBEDDING_DIM:
        report.fail(
            "embedding call",
            f"expected 1 vector of {EMBEDDING_DIM} dims, got {len(vectors)} of {len(vectors[0]) if vectors else 0}",
        )
        return
    report.ok("embedding call", f"{embedder.model}, {EMBEDDING_DIM} dims")


async def check_chat_and_memory(client: httpx.AsyncClient, report: Report) -> None:
    """The chain that actually matters: a fact written now must be findable
    by the very next question, without a restart or a re-index.

    Writing a file is not the same as indexing it; this is the check that
    knows the difference.
    """
    section("Chat and memory chain")
    fact = f"My {MARKER} verification codeword is Marmalade Seventeen."
    memory_id = None

    try:
        created = await client.post("/memory", json={"content": fact})
        if created.status_code != 201:
            report.fail("memory write", f"POST /memory returned {created.status_code} {created.text[:100]}")
            return
        body = created.json()
        memory_id = body["memory"]["id"]
        if body["memory"]["embedding_pending"]:
            report.fail("memory write", "stored with no embedding -- it will never be recalled")
        else:
            report.ok("memory write")

        reply = await client.post(
            "/chat/message",
            json={"content": f"What is my verification codeword? ({MARKER})"},
            timeout=90.0,
        )
        if reply.status_code != 200:
            report.fail("chat replies", f"POST /chat/message returned {reply.status_code} {reply.text[:120]}")
            return
        payload = reply.json()
        remember_conversation(payload)
        report.ok("chat replies", f"on {payload['model_used']}")

        recalled = payload["message"].get("recalled_memories", [])
        if any(m["id"] == memory_id for m in recalled):
            report.ok("new memory is immediately recallable", f"{len(recalled)} memories injected")
        else:
            report.fail(
                "new memory is immediately recallable",
                "the memory written seconds ago was not retrieved -- retrieval, the embedding, "
                "or the similarity threshold is broken",
            )

        if "marmalade" in payload["message"]["content"].lower():
            report.ok("the model used what was recalled")
        else:
            report.warn(
                "the model used what was recalled",
                "the memory was injected but the reply did not use it (prompt or model behaviour)",
            )
    finally:
        if memory_id:
            await client.delete(f"/memory/{memory_id}")


async def check_conversation_delete(client: httpx.AsyncClient, report: Report) -> None:
    """DELETE /chat/conversations/{id}, against real Postgres.

    Worth a live check rather than trusting the unit tests: those run on
    SQLite, which does not enforce the ON DELETE CASCADE that this endpoint
    deliberately works around by detaching agent_runs first. On Postgres the
    cascade is real, so "the runs survived" is only actually proven here.
    """
    section("Deleting a conversation")

    created = await client.post(
        "/chat/message",
        json={"content": f"Reply with the single word ok. ({MARKER} delete check)"},
        timeout=90.0,
    )
    if created.status_code != 200:
        report.fail("create a conversation to delete", f"{created.status_code} {created.text[:120]}")
        return
    conversation_id = created.json()["conversation_id"]
    remember_conversation(created.json())

    runs_before = len((await client.get("/agents/runs")).json())

    response = await client.delete(f"/chat/conversations/{conversation_id}")
    if response.status_code != 200:
        report.fail("DELETE /chat/conversations/{id}", f"{response.status_code} {response.text[:120]}")
        return
    body = response.json()
    report.ok(
        "DELETE /chat/conversations/{id}",
        f"{body['messages_removed']} messages removed, {body['agent_runs_detached']} runs detached",
    )

    gone = await client.get(f"/chat/conversations/{conversation_id}")
    if gone.status_code == 404:
        report.ok("the conversation is actually gone")
    else:
        report.fail("the conversation is actually gone", f"GET returned {gone.status_code}")

    listing = [c["id"] for c in (await client.get("/chat/conversations")).json()]
    if conversation_id not in listing:
        report.ok("it is out of the history list")
    else:
        report.fail("it is out of the history list", "still listed after delete")

    # The point of detaching rather than cascading: the activity trail keeps
    # its record of what ran, even for a chat that no longer exists.
    runs_after = len((await client.get("/agents/runs")).json())
    if runs_after >= runs_before:
        report.ok("agent runs survived the delete", f"{runs_after} runs still recorded")
    else:
        report.fail(
            "agent runs survived the delete",
            f"{runs_before} runs before, {runs_after} after -- the cascade took audit rows",
        )


async def check_memory_graph(client: httpx.AsyncClient, report: Report) -> None:
    """Exercise the graph query against real pgvector.

    Earned its place immediately: the first live call returned 503 because
    the adaptive floor binds a nullable float that Postgres could not type
    (`AmbiguousParameterError`), while all 183 unit tests passed -- the
    SQLite fake reimplements that logic in Python and never runs the SQL.
    """
    section("Memory graph")
    try:
        response = await client.get("/memory/graph")
    except httpx.HTTPError as exc:
        report.fail("GET /memory/graph", f"{type(exc).__name__}: {exc}")
        return
    if response.status_code != 200:
        report.fail("GET /memory/graph", f"{response.status_code} {response.text[:140]}")
        return

    body = response.json()
    node_ids = {n["id"] for n in body["nodes"]}
    report.ok(
        "GET /memory/graph",
        f"{len(body['nodes'])} nodes, {len(body['links'])} links, floor {body['min_similarity']:.3f}",
    )

    # The invariant the frontend layout depends on: a link pointing at a node
    # that is not in the list breaks the whole render, not just that line.
    dangling = [
        link for link in body["links"] if link["source"] not in node_ids or link["target"] not in node_ids
    ]
    if dangling:
        report.fail("graph has no dangling links", f"{len(dangling)} link(s) point at absent nodes")
    else:
        report.ok("graph has no dangling links")

    # An explicit floor must actually be applied, not merely echoed back.
    override = await client.get("/memory/graph?min_similarity=0.99")
    if override.status_code != 200:
        report.fail("explicit floor is honoured", f"{override.status_code} {override.text[:120]}")
    else:
        strict = override.json()
        if strict["adaptive"] or any(l["similarity"] < 0.99 for l in strict["links"]):
            report.fail("explicit floor is honoured", "links below the requested floor came back")
        else:
            report.ok("explicit floor is honoured", f"{len(strict['links'])} links survive at 0.99")


async def check_tools_and_rag(client: httpx.AsyncClient, report: Report) -> None:
    """Phase 5: tools, and the document chain end to end.

    The document round trip is the one worth having. It uploads a real file,
    waits for background ingestion, asks a question whose answer exists only
    in that file, and asserts the reply came back with a citation naming it.
    Every step of that is a separate thing that can silently not work --
    extraction, chunking, embedding, the pgvector query, the planner
    choosing the tool, the citation surviving onto the message -- and none
    of them is exercised by the unit suite, which fakes the embedder and the
    vector search.
    """
    section("Tools and RAG")

    try:
        tools = (await client.get("/tools")).json()
    except (httpx.HTTPError, ValueError) as exc:
        report.fail("GET /tools", f"{type(exc).__name__}: {exc}")
        return

    names = {t["name"] for t in tools}
    if {"web_search", "document_search"} <= names:
        report.ok("GET /tools", ", ".join(sorted(names)))
    else:
        report.fail("GET /tools", f"expected web_search and document_search, got {sorted(names)}")

    web = next((t for t in tools if t["name"] == "web_search"), None)
    if web and web["available"]:
        report.ok("web search is available")
    elif web:
        report.warn("web search is available", web["reason"])

    # --- the document chain -------------------------------------------
    marker_answer = "Zarquon Fourteen"
    body = (
        f"{MARKER} verification document.\n\n"
        f"The internal project codename for the archival subsystem is {marker_answer}. "
        "This sentence exists so a retrieval test can prove the answer came from this "
        "file rather than from the model's own knowledge, because no model has ever "
        "seen this codename before.\n"
    ) * 3

    document_id = None
    try:
        upload = await client.post(
            "/documents",
            files={"file": (f"{MARKER}-check.txt", body.encode("utf-8"), "text/plain")},
        )
        if upload.status_code != 201:
            report.fail("document upload", f"{upload.status_code} {upload.text[:120]}")
            return
        document_id = upload.json()["document"]["id"]
        report.ok("document upload", "queued for ingestion")

        # Ingestion is a background task; wait for it to settle.
        status, error, chunks = "pending", None, 0
        for _ in range(45):
            await asyncio.sleep(2)
            listing = await client.get("/documents")
            record = next((d for d in listing.json() if d["id"] == document_id), None)
            if record is None:
                break
            status, error, chunks = record["status"], record["error"], record["chunk_count"]
            if status != "pending":
                break

        if status == "ready":
            report.ok("document ingestion", f"{chunks} passages embedded")
        else:
            report.fail("document ingestion", f"status={status} error={error}")
            return

        # The passage list behind the documents dashboard's right-hand pane.
        # Worth a live check specifically because the unit tests exercise it
        # against SQLite: this is the query that has to survive real
        # Postgres, and `embedding IS NOT NULL` on a `vector` column is
        # exactly the kind of thing a fake never proves.
        passages = await client.get(f"/documents/{document_id}/chunks")
        if passages.status_code != 200:
            report.fail("GET /documents/{id}/chunks", f"{passages.status_code} {passages.text[:120]}")
        else:
            rows = passages.json()
            if len(rows) != chunks:
                report.fail(
                    "passage list matches the chunk count",
                    f"{len(rows)} passages listed but chunk_count is {chunks}",
                )
            elif [r["chunk_index"] for r in rows] != sorted(r["chunk_index"] for r in rows):
                report.fail("passages are in document order", "chunk_index is not ascending")
            elif not all(r["embedded"] for r in rows):
                # Every passage of a "ready" document was embedded, or the
                # document should not be ready. A False here means the
                # dashboard would show a passage CIPHER cannot actually quote.
                report.fail(
                    "every passage of a ready document is embedded",
                    f"{sum(1 for r in rows if not r['embedded'])} of {len(rows)} have no vector",
                )
            else:
                report.ok(
                    "GET /documents/{id}/chunks",
                    f"{len(rows)} passages, in order, all embedded",
                )

        answer = await client.post(
            "/chat/message",
            json={
                "content": "What is the internal project codename for the archival subsystem, "
                f"according to my uploaded documents? ({MARKER})"
            },
            timeout=120.0,
        )
        if answer.status_code != 200:
            report.fail("document-grounded answer", f"{answer.status_code} {answer.text[:120]}")
            return

        payload = answer.json()
        remember_conversation(payload)
        citations = payload["message"].get("citations", [])
        cited = [c for c in citations if c.get("kind") == "document"]

        # Phase 6 renamed these: the orchestrator picks an *agent*, and the
        # research agent then picks the document_search tool. The activity
        # line is what names the tool now.
        activity = payload.get("activity", "")
        if payload.get("agent_used") == "research" and "document" in activity:
            report.ok("routed to research, which searched documents", activity)
        else:
            report.fail(
                "routed to research, which searched documents",
                f"agent_used={payload.get('agent_used')!r} activity={activity!r} -- "
                f"the question was explicitly about uploaded documents",
            )

        if cited:
            report.ok("reply carries a document citation", cited[0].get("filename", ""))
        else:
            report.fail(
                "reply carries a document citation",
                "the answer was not grounded in any passage, so nothing can be traced back to a source",
            )

        if marker_answer.lower() in payload["message"]["content"].lower():
            report.ok("the answer came from the document", f"found {marker_answer!r}")
        else:
            report.fail(
                "the answer came from the document",
                f"{marker_answer!r} is in the uploaded file and not in the reply -- retrieval reached "
                f"the prompt but the model did not use it, or retrieval missed",
            )
    finally:
        if document_id:
            await client.delete(f"/documents/{document_id}")


async def check_agents(client: httpx.AsyncClient, report: Report) -> None:
    """Phase 6: the orchestrator routes, and every run is recorded.

    The audit trail is the check worth having. Routing itself already shows
    up in the RAG check above -- if the research agent were not being chosen,
    the document round trip would fail. What that does not prove is that the
    run was *recorded*, and an activity view that silently records nothing is
    exactly as useless as no activity view while looking perfectly healthy.
    """
    section("Agents")
    try:
        agents = (await client.get("/agents")).json()
    except (httpx.HTTPError, ValueError) as exc:
        report.fail("GET /agents", f"{type(exc).__name__}: {exc}")
        return

    names = {a["name"] for a in agents}
    if {"research", "coding", "memory"} <= names:
        report.ok("GET /agents", ", ".join(sorted(names)))
    else:
        report.fail("GET /agents", f"expected research, coding and memory; got {sorted(names)}")

    disabled = [a["name"] for a in agents if not a["enabled"]]
    if disabled:
        report.warn("all agents enabled", f"switched off: {', '.join(disabled)}")
    else:
        report.ok("all agents enabled")

    before = len((await client.get("/agents/runs")).json())

    answer = await client.post(
        "/chat/message",
        json={"content": f"Write a one-line Python function called {MARKER.replace('-', '_')}_add that adds two numbers."},
        timeout=120.0,
    )
    if answer.status_code != 200:
        report.fail("a coding question routes to a specialist", f"{answer.status_code} {answer.text[:120]}")
        return

    payload = answer.json()
    remember_conversation(payload)
    if payload.get("agent_used") == "coding":
        report.ok("a coding question routes to the coding agent")
    else:
        report.warn(
            "a coding question routes to the coding agent",
            f"router chose {payload.get('agent_used')!r} -- routing is a model decision, so this is "
            f"a signal about prompt quality rather than a broken system",
        )

    runs = (await client.get("/agents/runs")).json()
    if len(runs) > before:
        newest = runs[0]
        report.ok(
            "the run was recorded",
            f"{newest['agent_name']} {newest['status']} in {newest['duration_ms']}ms",
        )
    else:
        report.fail(
            "the run was recorded",
            "an agent ran but no row was written -- the activity view is blind",
        )


async def check_automation_safety(client: httpx.AsyncClient, report: Report) -> None:
    """Phase 7: the permission boundary, verified against the running server.

    Unit tests already prove the guard's logic. What they cannot prove is
    that the deployed process is enforcing it -- a misconfigured environment,
    a stale worker, or a route added without the guard would all pass the
    suite and fail here. These checks assert refusals, because a permission
    system that has quietly stopped refusing looks exactly like one that is
    working.

    Nothing here executes a state-changing action. It asserts that they are
    blocked, which is the only assertion worth making against a real machine.
    """
    section("Automation safety")

    try:
        status = (await client.get("/automation/actions")).json()
    except (httpx.HTTPError, ValueError) as exc:
        report.fail("GET /automation/actions", f"{type(exc).__name__}: {exc}")
        return

    by_name = {a["name"]: a for a in status["actions"]}
    if {"system_info", "open_url", "open_app"} <= set(by_name):
        report.ok("GET /automation/actions", f"{len(by_name)} actions")
    else:
        report.fail("GET /automation/actions", f"unexpected action list: {sorted(by_name)}")
        return

    # There must be no way to run a command. This is checked against the
    # RUNNING server rather than the source, because "the allowlist has no
    # shell action" and "the deployed process has no shell action" are
    # different claims.
    forbidden = {"run", "run_command", "shell", "exec", "eval", "cmd", "powershell"} & set(by_name)
    if forbidden:
        report.fail("no arbitrary command action is exposed", f"found {sorted(forbidden)}")
    else:
        report.ok("no arbitrary command action is exposed")

    if not status["enabled"]:
        report.warn(
            "automation is enabled",
            "AUTOMATION_ENABLED is off, so nothing can run. That is the safe default; "
            "the checks below verify it is actually enforced.",
        )
        denied = await client.post("/automation/execute", json={"action_name": "system_info"})
        if denied.status_code == 403:
            report.ok("the master switch is enforced", "even read-only actions are refused")
        else:
            report.fail(
                "the master switch is enforced",
                f"automation is off but system_info returned {denied.status_code}",
            )
        return

    # Enabled: assert the escalation rule holds against the live server.
    sensitive = [a for a in status["actions"] if a["risk"] == "sensitive"]
    if not sensitive:
        report.warn("a sensitive action exists", "nothing occupies the sensitive tier, so escalation is untested")
    elif all(a["needs_confirmation"] for a in sensitive):
        report.ok("sensitive actions always demand confirmation", ", ".join(a["name"] for a in sensitive))
    else:
        report.fail(
            "sensitive actions always demand confirmation",
            f"{[a['name'] for a in sensitive if not a['needs_confirmation']]} do not",
        )

    # Approve the category, then assert the sensitive action inside it is
    # STILL refused. This is the rule most likely to rot silently.
    target = sensitive[0] if sensitive else None
    if target:
        await client.post("/automation/permissions", json={"action_name": target["category"]})
        attempt = await client.post(
            "/automation/execute",
            json={"action_name": target["name"], "arguments": {"app": MARKER}, "confirmed": False},
        )
        if attempt.status_code == 403:
            report.ok("a session grant does not cover a sensitive action", target["name"])
        else:
            report.fail(
                "a session grant does not cover a sensitive action",
                f"{target['name']} returned {attempt.status_code} after only a category approval",
            )
        await client.delete(f"/automation/permissions/{target['category']}")

    # The kill switch, exercised for real: engage, prove a read-only action
    # is refused, release. Left released whatever happens.
    try:
        engaged = (await client.post("/automation/stop")).json()
        if not engaged["kill_switch_engaged"]:
            report.fail("the kill switch engages", "STOP returned without engaging")
        else:
            blocked = await client.post("/automation/execute", json={"action_name": "system_info"})
            if blocked.status_code == 403:
                report.ok("the kill switch halts even read-only actions")
            else:
                report.fail(
                    "the kill switch halts even read-only actions",
                    f"system_info returned {blocked.status_code} while stopped",
                )
    finally:
        await client.post("/automation/resume")

    log = await client.get("/automation/log")
    if log.status_code == 200 and any(row["outcome"] in ("denied", "blocked") for row in log.json()):
        report.ok("refusals are written to the audit log")
    else:
        report.fail(
            "refusals are written to the audit log",
            "actions were refused above but no denial reached the log",
        )


async def check_model_swap(client: httpx.AsyncClient, report: Report) -> None:
    section("Runtime model swap")
    try:
        listing = await client.get("/models")
        available = listing.json()["available"]
        report.ok("GET /models", f"{len(available)} models offered")

        refusal = await client.post("/models/active", json={"spoken": "switch to gemini 4 flash"})
        if refusal.status_code == 404 and "Gemini" in refusal.json().get("detail", ""):
            report.ok("unknown model is refused, with the real list")
        else:
            report.fail(
                "unknown model is refused, with the real list",
                f"expected 404 naming the real models, got {refusal.status_code}",
            )

        target = available[0]["id"]
        pinned = await client.post("/models/active", json={"spoken": target})
        if pinned.status_code == 200 and pinned.json()["pinned"]:
            report.ok("pinning a model", pinned.json()["display_name"])
        else:
            report.fail("pinning a model", f"{pinned.status_code} {pinned.text[:100]}")
    finally:
        # Always hand the running server back the way it was found.
        await client.delete("/models/active")


def _tone_wav(seconds: float) -> bytes:
    """A real WAV, generated rather than committed as a fixture.

    What is in it does not matter -- Whisper is free to hear nothing in a
    440Hz tone. The check is that the round trip happens at all: the route
    is served, the key is accepted, the model id still exists, and the
    response parses. A binary fixture would prove the same thing and would
    have to be carried in the repo.
    """
    rate = 16000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = [
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))
        ]
        out.writeframes(b"".join(frames))
    return buffer.getvalue()


async def check_speech_to_text(client: httpx.AsyncClient, report: Report) -> None:
    section("Speech to text")

    # A clip too short to contain speech must come back empty with 200. It is
    # normal operation -- a recorder opening and closing on silence -- and an
    # error here would switch the user's microphone off mid-conversation.
    tiny = await client.post(
        "/voice/transcribe",
        files={"audio": ("tiny.wav", _tone_wav(0.01), "audio/wav")},
    )
    if tiny.status_code == 200 and tiny.json()["text"] == "":
        report.ok("a clip too short to hold speech is not an error")
    else:
        report.fail(
            "a clip too short to hold speech is not an error",
            f"expected 200 with empty text, got {tiny.status_code} {tiny.text[:100]}",
        )

    clip = await client.post(
        "/voice/transcribe",
        files={"audio": ("preflight.wav", _tone_wav(1.0), "audio/wav")},
        timeout=60.0,
    )
    if clip.status_code == 200 and clip.json().get("model_used"):
        report.ok("POST /voice/transcribe", clip.json()["model_used"])
    else:
        report.fail(
            "POST /voice/transcribe",
            f"{clip.status_code} {clip.text[:160]}",
        )


async def check_secrets(report: Report) -> None:
    section("Secrets")
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        report.warn(".env present", "no .env at the repo root")
    else:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        if tracked.returncode == 0:
            report.fail(".env is not tracked by git", "it is committed -- rotate every key in it")
        else:
            report.ok(".env is not tracked by git")

    # This one must fail loudly: the API must never serve the repo.
    async with httpx.AsyncClient(base_url=get_settings().frontend_url.replace("3000", "8000"), timeout=10.0) as raw:
        for path in ("/.env", "/config.json", "/../.env"):
            try:
                response = await raw.get(path)
            except httpx.HTTPError:
                continue
            if response.status_code == 200 and ("KEY=" in response.text or "api_key" in response.text):
                report.fail("secrets are not web-reachable", f"GET {path} returned credentials")
                return
    report.ok("secrets are not web-reachable")


async def check_frontend(report: Report) -> None:
    section("Frontend")
    url = get_settings().frontend_url
    try:
        async with httpx.AsyncClient(timeout=10.0) as raw:
            response = await raw.get(url)
    except httpx.HTTPError as exc:
        # A warning, not a failure: the backend is useful on its own, and
        # preflight is often run without the dev server up.
        report.warn("frontend reachable", f"{url} is not answering ({type(exc).__name__})")
        return
    if response.status_code == 200:
        report.ok("frontend reachable", url)
    else:
        report.warn("frontend reachable", f"{url} returned {response.status_code}")


async def cleanup(report: Report) -> None:
    """Remove anything this run left behind.

    A harness that quietly litters the user's real memory store is worse than
    no harness, so this reports what it removed rather than doing it silently.
    """
    section("Cleanup")
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                text("DELETE FROM memories WHERE content LIKE :pattern"), {"pattern": f"%{MARKER}%"}
            )
            removed_memories = result.rowcount or 0
            # Exactly the conversations this run created -- see
            # CREATED_CONVERSATIONS. Never a content match: the previous
            # version deleted real conversations that merely discussed the
            # same topic as a probe question.
            removed_conversations = 0
            if CREATED_CONVERSATIONS:
                result = await session.execute(
                    text("DELETE FROM conversations WHERE id = ANY(:ids)"),
                    {"ids": [UUID(cid) for cid in CREATED_CONVERSATIONS]},
                )
                removed_conversations = result.rowcount or 0
            # And anything a PREVIOUS run left behind by dying before it got
            # here. Matched on MARKER, which every probe message carries and
            # no human types -- not on the topic of the question, which is
            # what made the old sweep delete real conversations.
            result = await session.execute(
                text(
                    "DELETE FROM conversations WHERE id IN ("
                    "  SELECT conversation_id FROM messages WHERE content LIKE :pattern"
                    ")"
                ),
                {"pattern": f"%{MARKER}%"},
            )
            removed_conversations += result.rowcount or 0
            # A run that died between upload and delete leaves a document
            # behind; sweep those too rather than accumulating one per crash.
            result = await session.execute(
                text("DELETE FROM documents WHERE filename LIKE :pattern"),
                {"pattern": f"%{MARKER}%"},
            )
            removed_documents = result.rowcount or 0
            result = await session.execute(
                text("DELETE FROM agent_runs WHERE input ILIKE :pattern"),
                {"pattern": f"%{MARKER.replace('-', '_')}%"},
            )
            removed_runs = result.rowcount or 0
            # Permission grants made by the safety checks. The activity_logs
            # rows they produced are deliberately NOT deleted: an audit log
            # that a test run can erase is not an audit log.
            result = await session.execute(
                text("DELETE FROM permissions WHERE user_id = :uid AND action_name IN ('apps', 'browse')"),
                {"uid": get_settings().dev_user_id},
            )
            removed_grants = result.rowcount or 0
            await session.commit()
        report.ok(
            "test data removed",
            f"{removed_memories} memories, {removed_documents} documents, "
            f"{removed_runs} agent runs, {removed_grants} grants, "
            f"{removed_conversations} conversations (audit log kept)",
        )
    except Exception as exc:  # noqa: BLE001
        report.warn("test data removed", f"{type(exc).__name__}: {exc} -- check the dashboard for stray rows")


async def main(all_models: bool, skip_llm: bool) -> int:
    report = Report()
    base_url = f"http://127.0.0.1:{get_settings().app_port}"
    print(f"CIPHER preflight against {base_url}")

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        started = time.monotonic()
        if not await check_server(client, report):
            return report.summary()
        await check_served_code_is_this_checkout(client, report)
        await check_database(report)
        await check_secrets(report)
        await check_frontend(report)
        if skip_llm:
            section("LLM providers")
            report.warn("live model calls", "skipped (--skip-llm)")
        else:
            await check_llm(report, all_models)
            await check_embeddings(report)
            await check_chat_and_memory(client, report)
            await check_conversation_delete(client, report)
            await check_memory_graph(client, report)
            await check_tools_and_rag(client, report)
            await check_agents(client, report)
            await check_automation_safety(client, report)
            await check_model_swap(client, report)
            await check_speech_to_text(client, report)
            await cleanup(report)
        print(f"\nfinished in {time.monotonic() - started:.1f}s")

    return report.summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-models", action="store_true", help="Probe every model in the registry, not just the defaults.")
    parser.add_argument("--skip-llm", action="store_true", help="Skip every billable call (structure checks only).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.all_models, args.skip_llm)))
