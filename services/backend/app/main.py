import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.chat import router as chat_router
from app.api.memory import router as memory_router
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
    return {"status": "ok"}
