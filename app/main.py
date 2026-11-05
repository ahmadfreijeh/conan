from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.v1.router import api_router
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.db.session import engine

UI_PAGE = Path(__file__).parent / "ui" / "index.html"


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

    @app.get("/", include_in_schema=False)
    async def ui() -> FileResponse:  # test UI: upload a photo and watch the run
        return FileResponse(UI_PAGE)

    return app


app = create_app()
