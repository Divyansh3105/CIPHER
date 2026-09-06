"""Application settings, loaded from the repo-root .env file."""
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# services/backend/app/core/config.py -> repo root is 4 parents up.
ROOT_DIR = Path(__file__).resolve().parents[4]
ENV_FILE = ROOT_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "development"
    app_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # Database (Supabase Postgres). database_url is the pooled connection
    # used at request time; migration_database_url is a non-pooled
    # (session-mode) connection used only for running Alembic migrations.
    database_url: str
    migration_database_url: str | None = None

    # Supabase
    supabase_url: str | None = None
    supabase_key: str | None = None
    supabase_jwt_secret: str | None = None

    # LLM providers
    gemini_api_key: str
    groq_api_key: str

    # Tools (later phases)
    search_api_key: str | None = None
    picovoice_access_key: str | None = None

    # Phase 7: computer control. Off unless explicitly enabled, so a fresh
    # clone cannot act on anyone's machine. Read through Settings (and
    # therefore .env) rather than os.environ alone -- uvicorn does not load
    # .env into the process environment, so an os.environ-only read silently
    # ignored the file this project documents the setting in.
    automation_enabled: bool = False
    #: Comma-separated absolute paths `list_directory` may read.
    automation_allowed_dirs: str = ""
    #: Comma-separated name=executable pairs `open_app` may launch.
    automation_allowed_apps: str = ""

    # Security
    jwt_secret_key: str | None = None
    session_secret: str | None = None

    # Phase 1: auth is deferred. Every request is attributed to this single
    # seeded dev user. Replace with real JWT-derived user ids when auth lands.
    dev_user_id: UUID = Field(default=UUID("00000000-0000-0000-0000-000000000001"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
