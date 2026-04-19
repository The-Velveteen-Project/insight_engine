import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import discovery, editorial, github, health, telegram
from app.core.config import settings
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await init_db()
    yield


app = FastAPI(
    title="Velveteen Insight Engine",
    description="Editorial and portfolio system — The Velveteen Project",
    version="0.1.0",
    lifespan=lifespan,
    debug=settings.debug,
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(telegram.router, prefix="/api/v1/telegram")
app.include_router(discovery.router, prefix="/api/v1/discovery")
app.include_router(github.router, prefix="/api/v1/github")
app.include_router(editorial.router, prefix="/api/v1/editorial")
