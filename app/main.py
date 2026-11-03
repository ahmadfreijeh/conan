from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    yield
    await redis_client.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
