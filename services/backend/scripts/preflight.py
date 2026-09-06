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
import subprocess
import sys
import time
from pathlib import Path

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
            report.fail(f"model {spec.id}", str(exc)[:120])
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
            json={"content": "What is my verification codeword?"},
            timeout=90.0,
        )
        if reply.status_code != 200:
            report.fail("chat replies", f"POST /chat/message returned {reply.status_code} {reply.text[:120]}")
            return
        payload = reply.json()
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
            result = await session.execute(
                text(
                    "DELETE FROM conversations WHERE title ILIKE :pattern OR id IN ("
                    "  SELECT conversation_id FROM messages WHERE content ILIKE :pattern"
                    ")"
                ),
                {"pattern": "%verification codeword%"},
            )
            removed_conversations = result.rowcount or 0
            await session.commit()
        report.ok("test data removed", f"{removed_memories} memories, {removed_conversations} conversations")
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
            await check_memory_graph(client, report)
            await check_model_swap(client, report)
            await cleanup(report)
        print(f"\nfinished in {time.monotonic() - started:.1f}s")

    return report.summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-models", action="store_true", help="Probe every model in the registry, not just the defaults.")
    parser.add_argument("--skip-llm", action="store_true", help="Skip every billable call (structure checks only).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.all_models, args.skip_llm)))
