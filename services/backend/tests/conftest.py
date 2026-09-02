import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

import pytest

from app.core.config import get_settings

get_settings.cache_clear()


@pytest.fixture
def dev_user_id():
    return get_settings().dev_user_id
