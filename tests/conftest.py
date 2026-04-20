import os
import tempfile

# Must be set before any app module is imported
# pydantic-settings reads env at instantiation
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-000")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "")
os.environ["OPENAI_API_KEY"] = ""
os.environ["GITHUB_TOKEN"] = ""

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db.close()
os.environ["DB_PATH"] = _db.name

from collections.abc import AsyncGenerator  # noqa: E402

import aiosqlite  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Yields a real async connection to the test DB with tables initialized."""
    await init_db()
    async with aiosqlite.connect(os.environ["DB_PATH"]) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn
