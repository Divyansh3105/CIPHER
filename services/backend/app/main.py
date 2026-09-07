import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.agents import router as agents_router
from app.api.automation import router as automation_router
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.memory import router as memory_router
from app.api.models import router as models_router
from app.api.tools import router as tools_router
from app.api.vision import router as vision_router
from app.api.voice import router as voice_router
from app.api.personas import router as personas_router
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="CIPHER Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(models_router)
app.include_router(documents_router)
app.include_router(tools_router)
app.include_router(agents_router)
app.include_router(automation_router)
app.include_router(vision_router)
app.include_router(voice_router)
app.include_router(personas_router)


def _cors_headers(request: Request) -> dict[str, str]:
    """Explicitly set CORS headers on error responses.

    Responses built by an `@app.exception_handler` are, in this FastAPI/
    Starlette build, not reliably passed back through CORSMiddleware's
    header-injecting `send` wrapper (observed empirically: a real 500/503
    from here reaches the browser with no Access-Control-Allow-Origin
    header even though CORSMiddleware is registered and works fine for
    normal responses). Without this, the browser reports a misleading CORS
    error instead of the real one, and the frontend can't read the body.
    """
    origin = request.headers.get("origin")
    if origin and origin == settings.frontend_url:
        return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Vary": "Origin"}
    return {}


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database error handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database is currently unavailable."},
        headers=_cors_headers(request),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
        headers=_cors_headers(request),
    )


@app.get("/health")
def health_check():
    """Liveness: is this process running.

    Deliberately checks nothing else. A liveness probe that fails when the
    database is briefly unreachable gets the container restarted, which does
    not fix the database and does lose whatever was in flight.
    """
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness: can this process actually serve a request.

    Checks the database, because every meaningful endpoint needs it. Returns
    503 when it cannot, so a load balancer stops sending traffic to a process
    that would only produce errors -- while liveness above keeps it alive
    long enough to recover.
    """
    from sqlalchemy import text as sql_text

    from app.core.database import async_session_factory

    try:
        async with async_session_factory() as session:
            await session.scalar(sql_text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "database": f"{type(exc).__name__}"},
        )
    return {"status": "ready", "database": "ok"}
